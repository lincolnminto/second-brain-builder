"""Media type detection, file hashes, and the table of supported Source formats."""

import codecs
import hashlib
import lzma
import re
import struct
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO

from second_brain_builder.conversion.models import ConversionError, UnsupportedFormatError

_HASH_CHUNK_BYTES = 1024 * 1024
_SNIFF_BYTES = 8192
_ZIP_MIMETYPE_MAX_BYTES = 256
_EPUB_CONTAINER_MEMBER = "META-INF/container.xml"
# Bound both ZipInfo allocations and the amount of data a converter can expand.
_ZIP_MAX_ENTRIES = 10_000
_ZIP_MAX_UNCOMPRESSED_BYTES = 2 * 1024**3
_ZIP_MAX_DIRECTORY_BYTES = 32 * 1024**2
# Docling's EPUB extraction has tighter byte limits than the ZIP pre-check.
_EPUB_MAX_TOTAL_BYTES = 512 * 1024**2
_EPUB_MAX_FILE_BYTES = 64 * 1024**2
_ZIP_END = struct.Struct("<4s4H2IH")
_ZIP64_END = struct.Struct("<4sQ2H2I4Q")
_ZIP_CENTRAL_HEADER = struct.Struct("<4s6H3I5H2I")

OCTET_STREAM = "application/octet-stream"

_MEDIA_TYPE_PATTERN = re.compile(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*")
# Size of the info header that follows the 14-byte BMP file header, per format version.
_BMP_INFO_HEADER_SIZES = {12, 16, 40, 52, 56, 64, 108, 124}

# What zipfile raises while it reads a damaged or unusual archive. RuntimeError covers
# an encrypted member (a password is needed) and its subclass NotImplementedError an
# unsupported compression method, such as Deflate64.
ZIP_READ_ERRORS: tuple[type[Exception], ...] = (
    zipfile.BadZipFile,
    OSError,
    EOFError,
    RuntimeError,
    UnicodeDecodeError,
    zlib.error,
    lzma.LZMAError,
)
# General purpose bit 0 of a ZIP entry: the entry is encrypted.
ZIP_ENCRYPTED_FLAG = 0x1

# Byte order marks of UTF-32 and UTF-16 text. UTF-32 LE comes first: it starts with
# the UTF-16 LE mark. UTF-8 text needs no special case.
_BOM_CODECS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_TEXT_WHITESPACE_CONTROLS = frozenset("\t\n\r\f\v")

# MPEG audio frame headers (ISO/IEC 11172-3 and 13818-3). Bit rates in kbit/s, by
# version group (1: MPEG-1, 2: MPEG-2 and 2.5) and layer. Index 0 (free format) and
# index 15 are not valid for sniffing.
_MPEG_LAYER2_3_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
_MPEG_BIT_RATES: dict[tuple[int, int], tuple[int, ...]] = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): _MPEG_LAYER2_3_V2,
    (2, 3): _MPEG_LAYER2_3_V2,
}
# Sample rates in Hz by the two version bits: 3 is MPEG-1, 2 is MPEG-2, 0 is MPEG-2.5.
_MPEG_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
# ADTS (AAC) sampling frequency indexes above 12 are reserved or not allowed.
_ADTS_MAX_FREQUENCY_INDEX = 12
_ADTS_HEADER_BYTES = 7
_ID3_HEADER_BYTES = 10
# An audio file can start with more than one ID3v2 tag. Skip at most this many.
_ID3_MAX_TAGS = 4
_ID3_MEDIA_TYPES = {"audio/mpeg", "audio/aac", "audio/flac"}


class SourceKind(str, Enum):
    """How Conversion handles a Source."""

    DOCUMENT = "document"  # Docling reads the file directly
    KINDLE = "kindle"  # Calibre converts it to EPUB first
    AUDIO = "audio"
    VIDEO = "video"


@dataclass(frozen=True)
class SourceFormat:
    """One supported media type and how to read it."""

    media_type: str
    kind: SourceKind
    extension: str  # canonical file extension, without the dot
    docling_format: str | None = None  # value of docling's InputFormat
    ffmpeg_demuxer: str | None = None  # forced ffmpeg input format for audio and video
    # Extension of the copy that the converter reads, if it is not ``extension``.
    staged_extension: str | None = None

    @property
    def staged_suffix(self) -> str:
        """File suffix of the staged copy. Docling and Calibre choose a reader from it."""
        return f".{self.staged_extension or self.extension}"


def _doc(
    media_type: str, extension: str, docling_format: str, staged_extension: str | None = None
) -> SourceFormat:
    return SourceFormat(
        media_type,
        SourceKind.DOCUMENT,
        extension,
        docling_format=docling_format,
        staged_extension=staged_extension,
    )


def _media(media_type: str, kind: SourceKind, extension: str, demuxer: str) -> SourceFormat:
    return SourceFormat(media_type, kind, extension, ffmpeg_demuxer=demuxer)


_FORMATS: tuple[SourceFormat, ...] = (
    _doc("application/pdf", "pdf", "pdf"),
    _doc("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx", "docx"),
    _doc(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx", "pptx"
    ),
    _doc("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx", "xlsx"),
    _doc("text/html", "html", "html"),
    _doc("application/xhtml+xml", "xhtml", "html"),
    _doc("application/epub+zip", "epub", "epub"),
    _doc("text/markdown", "md", "md"),
    # Docling maps no extension to plain text: it sniffs a .txt file and can take
    # comma- or tab-separated lines for CSV. As .md, the Markdown reader gets it.
    _doc("text/plain", "txt", "md", staged_extension="md"),
    _doc("text/csv", "csv", "csv"),
    _doc("image/png", "png", "image"),
    _doc("image/jpeg", "jpg", "image"),
    _doc("image/tiff", "tiff", "image"),
    _doc("image/bmp", "bmp", "image"),
    _doc("image/webp", "webp", "image"),
    _doc("image/gif", "gif", "image"),
    SourceFormat("application/x-mobipocket-ebook", SourceKind.KINDLE, "mobi"),
    SourceFormat("application/vnd.amazon.mobi8-ebook", SourceKind.KINDLE, "azw3"),
    _media("audio/wav", SourceKind.AUDIO, "wav", "wav"),
    _media("audio/mpeg", SourceKind.AUDIO, "mp3", "mp3"),
    _media("audio/mp4", SourceKind.AUDIO, "m4a", "mov"),
    _media("audio/aac", SourceKind.AUDIO, "aac", "aac"),
    _media("audio/ogg", SourceKind.AUDIO, "ogg", "ogg"),
    _media("audio/flac", SourceKind.AUDIO, "flac", "flac"),
    _media("video/mp4", SourceKind.VIDEO, "mp4", "mov"),
    _media("video/x-msvideo", SourceKind.VIDEO, "avi", "avi"),
    _media("video/quicktime", SourceKind.VIDEO, "mov", "mov"),
    _media("video/x-matroska", SourceKind.VIDEO, "mkv", "matroska"),
    _media("video/webm", SourceKind.VIDEO, "webm", "matroska"),
)

SUPPORTED_FORMATS: dict[str, SourceFormat] = {fmt.media_type: fmt for fmt in _FORMATS}

# Used only when the content has no known signature.
_EXTENSION_MEDIA_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html",
    "htm": "text/html",
    "xhtml": "application/xhtml+xml",
    "epub": "application/epub+zip",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "txt": "text/plain",
    "text": "text/plain",
    "csv": "text/csv",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "gif": "image/gif",
    "mobi": "application/x-mobipocket-ebook",
    "prc": "application/x-mobipocket-ebook",
    "azw": "application/vnd.amazon.mobi8-ebook",
    "azw3": "application/vnd.amazon.mobi8-ebook",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "flac": "audio/flac",
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "avi": "video/x-msvideo",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint",
}

_KF8_EXTENSIONS = {"azw", "azw3", "kf8"}
# Media types that text with a UTF-16 or UTF-32 byte order mark can have.
_TEXT_MEDIA_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "application/xhtml+xml",
}
_AUDIO_MP4_BRANDS = {b"M4A ", b"M4B ", b"M4P "}
_AUDIO_MP4_EXTENSIONS = {"m4a", "m4b", "m4p"}
_HEIF_BRANDS = {b"heic", b"heix", b"mif1", b"msf1"}
_OLE_EXTENSION_MEDIA_TYPES = {
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint",
}


def source_format(media_type: str) -> SourceFormat:
    """Return how to read a media type, or raise ``UnsupportedFormatError``."""
    fmt = SUPPORTED_FORMATS.get(media_type)
    if fmt is None:
        raise UnsupportedFormatError(f"Conversion does not support the media type {media_type}.")
    return fmt


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file. The file is read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def detect_media_type(path: Path) -> str:
    """Return the media type of a file.

    The content decides first (magic bytes, ZIP members, HTML markup). The file
    extension decides only when the content has no known signature. Text with a
    UTF-16 or UTF-32 byte order mark is always a text type. A file that is neither
    known nor text is ``application/octet-stream``.
    """
    with path.open("rb") as file:
        head = file.read(_SNIFF_BYTES)
    extension = path.suffix.lower().lstrip(".")

    bom_text = _bom_text(head)
    if bom_text is not None:
        return _bom_text_media_type(bom_text, extension)
    detected = _from_signature(path, head, extension)
    if detected is not None:
        return detected
    by_extension = _EXTENSION_MEDIA_TYPES.get(extension)
    if by_extension is not None:
        return by_extension
    if _looks_like_text(head):
        return "text/plain"
    return OCTET_STREAM


def _from_signature(path: Path, head: bytes, extension: str) -> str | None:
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        return _zip_media_type(path)
    if len(head) >= 68 and head[60:68] == b"BOOKMOBI":
        if extension in _KF8_EXTENSIONS:
            return "application/vnd.amazon.mobi8-ebook"
        return "application/x-mobipocket-ebook"
    image = _image_media_type(head)
    if image is not None:
        return image
    media = _audio_video_media_type(path, head, extension)
    if media is not None:
        return media
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        # OLE2 container: legacy Office files. Only the extension tells them apart.
        return _OLE_EXTENSION_MEDIA_TYPES.get(extension, "application/x-ole-storage")
    if head.startswith(b"{\\rtf"):
        return "application/rtf"
    return _markup_media_type(head)


def _image_media_type(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if head.startswith(b"BM") and _bmp_info_header_size(head) in _BMP_INFO_HEADER_SIZES:
        return "image/bmp"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _bmp_info_header_size(head: bytes) -> int:
    return int.from_bytes(head[14:18], "little") if len(head) >= 18 else 0


def _audio_video_media_type(path: Path, head: bytes, extension: str) -> str | None:
    if _id3_tag_length(head) is not None:
        return _id3_tagged_media_type(path, extension)
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return "video/x-msvideo"
    if head.startswith(b"fLaC"):
        return "audio/flac"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head[4:8] == b"ftyp":
        return _iso_media_type(head[8:12], extension)
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        # EBML header. Its DocType element names the variant.
        return "video/webm" if b"webm" in head[:64] else "video/x-matroska"
    return _audio_stream_media_type(head)


def _audio_stream_media_type(data: bytes) -> str | None:
    """FLAC, AAC (ADTS), or MPEG audio that starts at the first byte of ``data``.

    A frame sync alone is two bytes and matches too much (the UTF-16 LE byte order
    mark, for one), so ADTS and MPEG audio need a valid header and a second frame.
    """
    if data.startswith(b"fLaC"):
        return "audio/flac"
    adts_length = _adts_frame_length(data)
    if adts_length is not None and _adts_frame_length(data[adts_length:]) is not None:
        return "audio/aac"
    mpeg_length = _mpeg_frame_length(data)
    if mpeg_length is not None and _same_mpeg_stream(data, data[mpeg_length:]):
        return "audio/mpeg"
    return None


def _adts_frame_length(header: bytes) -> int | None:
    """Length of the ADTS frame that starts ``header``, or None if it is not one."""
    if len(header) < _ADTS_HEADER_BYTES or header[0] != 0xFF or header[1] & 0xF6 != 0xF0:
        return None  # 12-bit sync, then layer bits 00
    if (header[2] >> 2) & 0x0F > _ADTS_MAX_FREQUENCY_INDEX:
        return None
    length = ((header[3] & 0x03) << 11) | (header[4] << 3) | (header[5] >> 5)
    return length if length >= _ADTS_HEADER_BYTES else None


def _mpeg_frame_length(header: bytes) -> int | None:
    """Length of the MPEG audio frame that starts ``header``, or None if it is not one."""
    if len(header) < 4 or header[0] != 0xFF or header[1] & 0xE0 != 0xE0:
        return None
    version_bits = (header[1] >> 3) & 0x03
    layer_bits = (header[1] >> 1) & 0x03
    bit_rate_index = header[2] >> 4
    sample_rate_index = (header[2] >> 2) & 0x03
    padding = (header[2] >> 1) & 0x01
    if version_bits == 1 or layer_bits == 0 or bit_rate_index in (0, 15):
        return None  # reserved version, reserved layer, free or bad bit rate
    if sample_rate_index == 3:
        return None
    layer = 4 - layer_bits
    group = 1 if version_bits == 3 else 2
    bit_rate = _MPEG_BIT_RATES[(group, layer)][bit_rate_index] * 1000
    sample_rate = _MPEG_SAMPLE_RATES[version_bits][sample_rate_index]
    if layer == 1:
        return (12 * bit_rate // sample_rate + padding) * 4
    if layer == 3 and group == 2:
        return 72 * bit_rate // sample_rate + padding
    return 144 * bit_rate // sample_rate + padding


def _same_mpeg_stream(first: bytes, second: bytes) -> bool:
    """True if ``second`` starts with a frame of the same version, layer, and sample rate."""
    if _mpeg_frame_length(second) is None:
        return False
    return first[1] & 0xFE == second[1] & 0xFE and first[2] & 0x0C == second[2] & 0x0C


def _id3_tag_length(data: bytes) -> int | None:
    """Length of the ID3v2 tag that starts ``data``, or None if there is none."""
    if len(data) < _ID3_HEADER_BYTES or not data.startswith(b"ID3"):
        return None
    if data[3] not in (2, 3, 4) or data[4] == 0xFF:
        return None
    size_bytes = data[6:10]
    if any(byte & 0x80 for byte in size_bytes):
        return None  # the size is a syncsafe integer: 7 bits per byte
    size = (size_bytes[0] << 21) | (size_bytes[1] << 14) | (size_bytes[2] << 7) | size_bytes[3]
    footer = _ID3_HEADER_BYTES if data[3] == 4 and data[5] & 0x10 else 0
    return _ID3_HEADER_BYTES + size + footer


def _id3_tagged_media_type(path: Path, extension: str) -> str:
    """The audio after one or more ID3v2 tags.

    ID3v2 tags start MP3 files, but also AAC (ADTS) and some FLAC files. The tags
    can be large (cover art), so the audio is read from the file after them.
    """
    offset = 0
    data = b""
    with path.open("rb") as file:
        for _ in range(_ID3_MAX_TAGS):
            file.seek(offset)
            data = file.read(_SNIFF_BYTES)
            tag_length = _id3_tag_length(data)
            if tag_length is None:
                break
            offset += tag_length
            data = b""
    # Some writers pad the tag with more zero bytes than its size says.
    detected = _audio_stream_media_type(data.lstrip(b"\x00"))
    if detected is not None:
        return detected
    by_extension = _EXTENSION_MEDIA_TYPES.get(extension)
    return by_extension if by_extension in _ID3_MEDIA_TYPES else "audio/mpeg"


def _iso_media_type(brand: bytes, extension: str) -> str:
    """ISO base media files (MP4 family), told apart by the major brand."""
    if brand in _AUDIO_MP4_BRANDS or extension in _AUDIO_MP4_EXTENSIONS:
        return "audio/mp4"
    if brand == b"qt  ":
        return "video/quicktime"
    if brand in _HEIF_BRANDS:
        return "image/heic"
    if brand == b"avif":
        return "image/avif"
    if brand.startswith(b"3g"):
        return "video/3gpp"
    return "video/mp4"


def _zip_media_type(path: Path) -> str:
    """ZIP containers: EPUB and OpenDocument name their type, OOXML has known folders.

    An EPUB whose ``mimetype`` file cannot be read (it is encrypted, for example) is
    still known by its container file, so the DRM check can refuse it.
    """
    try:
        with _open_zip(path) as archive:
            names = set(archive.namelist())
            declared = _zip_declared_media_type(archive) if "mimetype" in names else None
    except ZIP_READ_ERRORS:
        return "application/zip"
    if declared is not None:
        return declared
    if "[Content_Types].xml" in names:
        if any(name.startswith("word/") for name in names):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if any(name.startswith("ppt/") for name in names):
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        if any(name.startswith("xl/") for name in names):
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if _EPUB_CONTAINER_MEMBER in names:
        return "application/epub+zip"
    return "application/zip"


@contextmanager
def _open_zip(path: Path) -> Iterator[zipfile.ZipFile]:
    """Validate bounds before ZipFile reads its directory into memory.

    Use the same open file for validation and parsing. Each conversion entry
    point that opens a ZIP uses this guard, including metadata and DRM checks.
    """
    with path.open("rb") as file:
        _check_zip_limits(file)
        file.seek(0)
        with zipfile.ZipFile(file) as archive:
            yield archive


def _zip_directory(file: BinaryIO) -> tuple[int, int, int]:
    """Return the entry count, directory size, and directory offset with bounded reads."""
    length = file.seek(0, 2)
    tail_offset = max(0, length - (65535 + _ZIP_END.size))
    file.seek(tail_offset)
    tail = file.read(65535 + _ZIP_END.size)
    end = tail.rfind(b"PK\x05\x06")
    if end < 0 or len(tail) - end < _ZIP_END.size:
        raise zipfile.BadZipFile("Missing ZIP end record.")
    _, disk, cd_disk, disk_count, count, size, offset, comment = _ZIP_END.unpack_from(tail, end)
    if end + _ZIP_END.size + comment > len(tail) or disk or cd_disk or disk_count != count:
        raise zipfile.BadZipFile("Invalid or multi-disk ZIP end record.")
    directory_end = tail_offset + end
    # ZIP64 can be used even when the ordinary fields have not overflowed.
    if directory_end >= 20:
        file.seek(directory_end - 20)
        locator = file.read(20)
        if locator.startswith(b"PK\x06\x07"):
            _, zip64_disk, _, disks = struct.unpack("<4sIQI", locator)
            if zip64_disk or disks != 1 or directory_end < 20 + _ZIP64_END.size:
                raise zipfile.BadZipFile("Invalid or multi-disk ZIP64 locator.")
            file.seek(directory_end - 20 - _ZIP64_END.size)
            record = _ZIP64_END.unpack(file.read(_ZIP64_END.size))
            if record[0] != b"PK\x06\x06" or record[1] != 44 or record[4] or record[5]:
                raise zipfile.BadZipFile("Invalid ZIP64 end record.")
            if record[6] != record[7]:
                raise zipfile.BadZipFile("Multi-disk ZIP64 archive.")
            count, size, offset = record[7:10]
            directory_end -= 20 + _ZIP64_END.size
    if count > _ZIP_MAX_ENTRIES or size > _ZIP_MAX_DIRECTORY_BYTES:
        raise ConversionError("The ZIP archive exceeds the entry count or directory size limit.")
    # Like ZipFile, allow a prefix before the archive, but never a negative offset.
    start = directory_end - size
    if start < 0 or offset > start:
        raise zipfile.BadZipFile("Invalid ZIP directory offset.")
    return count, size, start


def _zip_uncompressed_size(size: int, extra: bytes) -> int:
    """Read the ZIP64 uncompressed size when its 32-bit field is a sentinel."""
    if size != 0xFFFFFFFF:
        return size
    position = 0
    while position + 4 <= len(extra):
        kind, length = struct.unpack_from("<HH", extra, position)
        position += 4
        if position + length > len(extra):
            break
        if kind == 1 and length >= 8:
            return int(struct.unpack_from("<Q", extra, position)[0])
        position += length
    raise zipfile.BadZipFile("Missing ZIP64 uncompressed size.")


def _check_zip_limits(file: BinaryIO) -> None:
    expected_count, size, start = _zip_directory(file)
    file.seek(start)
    remaining = size
    count = total = 0
    while remaining:
        if remaining < _ZIP_CENTRAL_HEADER.size:
            raise zipfile.BadZipFile("Truncated ZIP directory.")
        header = file.read(_ZIP_CENTRAL_HEADER.size)
        if len(header) != _ZIP_CENTRAL_HEADER.size or not header.startswith(b"PK\x01\x02"):
            raise zipfile.BadZipFile("Invalid ZIP directory entry.")
        fields = _ZIP_CENTRAL_HEADER.unpack(header)
        name_size, extra_size, comment_size = fields[10:13]
        entry_size = _ZIP_CENTRAL_HEADER.size + name_size + extra_size + comment_size
        if entry_size > remaining:
            raise zipfile.BadZipFile("Truncated ZIP directory entry.")
        file.seek(name_size, 1)
        extra = file.read(extra_size)
        file.seek(comment_size, 1)
        total += _zip_uncompressed_size(fields[9], extra)
        count += 1
        if count > _ZIP_MAX_ENTRIES or total > _ZIP_MAX_UNCOMPRESSED_BYTES:
            raise ConversionError(
                "The ZIP archive exceeds the entry count or uncompressed size limit."
            )
        remaining -= entry_size
    if count != expected_count:
        raise zipfile.BadZipFile("The ZIP directory entry count is inconsistent.")


def _zip_declared_media_type(archive: zipfile.ZipFile) -> str | None:
    """The media type in the ``mimetype`` member, or None if it cannot be read."""
    info = archive.getinfo("mimetype")
    if info.file_size > _ZIP_MIMETYPE_MAX_BYTES or info.flag_bits & ZIP_ENCRYPTED_FLAG:
        return None
    try:
        declared = archive.read(info).decode("ascii", "replace").strip().lower()
    except ZIP_READ_ERRORS:
        return None
    return declared if _MEDIA_TYPE_PATTERN.fullmatch(declared) else None


def _markup_media_type(head: bytes) -> str | None:
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n")[:1024].lower()
    if text.startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if text.startswith(b"<?xml"):
        if b"<html" in text:
            return "application/xhtml+xml"
        if b"<svg" in text:
            return "image/svg+xml"
        return "application/xml"
    if text.startswith(b"<svg"):
        return "image/svg+xml"
    return None


def _bom_text(head: bytes) -> str | None:
    """The sniffed bytes as text, if they start with a UTF-16 or UTF-32 byte order mark.

    None if there is no such mark, or if the rest does not decode to text.
    """
    for bom, codec in _BOM_CODECS:
        if head.startswith(bom):
            # An incremental decoder keeps a character that the sniff limit cut in two.
            decoder = codecs.getincrementaldecoder(codec)()
            try:
                text = decoder.decode(head[len(bom) :], final=False)
            except UnicodeDecodeError:
                return None
            controls = (char for char in text if char < " " or char == "\x7f")
            if any(char not in _TEXT_WHITESPACE_CONTROLS for char in controls):
                return None
            return text
    return None


def _bom_text_media_type(text: str, extension: str) -> str:
    """Markup decides, then a text extension. Anything else is plain text."""
    markup = _markup_media_type(text.encode("utf-8"))
    if markup is not None:
        return markup
    by_extension = _EXTENSION_MEDIA_TYPES.get(extension)
    return by_extension if by_extension in _TEXT_MEDIA_TYPES else "text/plain"


def _looks_like_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as error:
        # A multi-byte character can be cut at the end of the sniffed block.
        return error.start >= len(head) - 3 and error.reason == "unexpected end of data"
    return True
