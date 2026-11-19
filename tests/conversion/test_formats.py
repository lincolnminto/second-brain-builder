"""Media type detection, file hashes, and formats that Conversion refuses."""

import codecs
import hashlib
import os
import shutil
import struct
import zipfile

import pytest

from second_brain_builder.conversion import (
    ConversionError,
    ConversionOptions,
    UnsupportedFormatError,
    convert_source,
    detect_media_type,
    formats,
    sha256_file,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
HTML_PAGE = "<!DOCTYPE html><html><head><title>Tides</title></head><body><p>Hi</p></body></html>"


def _ftyp(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x02\x00isomiso2" + bytes(32)


def _ebml(doc_type: bytes) -> bytes:
    return b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84" + doc_type + bytes(32)


def _mp3_frames(count: int) -> bytes:
    """MPEG-1 Layer III frames: 128 kbit/s at 44.1 kHz is 417 bytes per frame."""
    return (b"\xff\xfb\x90\x64" + bytes(413)) * count


def _adts_frames(count: int, length: int = 96) -> bytes:
    """AAC LC frames in ADTS: 44.1 kHz, two channels, ``length`` bytes per frame."""
    header = bytes(
        [0xFF, 0xF1, 0x50, 0x80 | (length >> 11), (length >> 3) & 0xFF, (length & 7) << 5 | 0x1F]
    )
    return (header + b"\xfc" + bytes(length - 7)) * count


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("upload.bin", b"%PDF-1.7\n%synthetic\n", "application/pdf"),
        ("page.txt", HTML_PAGE.encode(), "text/html"),
        ("clip.mp4", _ftyp(b"M4A "), "audio/mp4"),
        ("clip.m4a", _ftyp(b"isom"), "audio/mp4"),
        ("clip.bin", _ftyp(b"isom"), "video/mp4"),
        ("clip.mp4", _ftyp(b"qt  "), "video/quicktime"),
        ("clip.bin", _ebml(b"webm"), "video/webm"),
        ("clip.webm", _ebml(b"matroska"), "video/x-matroska"),
        ("sound.bin", _mp3_frames(3), "audio/mpeg"),
        ("sound.mp3", _adts_frames(3), "audio/aac"),
        # One frame sync with nothing after it is not enough to call a file MPEG audio.
        ("sound.bin", b"\xff\xfb\x90\x64" + bytes(64), "application/octet-stream"),
        ("legacy.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(64), "application/msword"),
    ],
    ids=lambda value: value if isinstance(value, str) else "content",
)
def test_content_decides_over_the_file_name(synthetic, name, content, expected):
    path = synthetic.directory / name
    path.write_bytes(content)

    assert detect_media_type(path) == expected


@pytest.mark.parametrize(
    ("name", "bom", "codec", "text", "expected"),
    [
        # Windows Notepad "Unicode" and Excel "Unicode Text" write UTF-16 LE with a BOM.
        ("notes.txt", codecs.BOM_UTF16_LE, "utf-16-le", "River notes\n", "text/plain"),
        ("levels.csv", codecs.BOM_UTF16_LE, "utf-16-le", "hour,level\n06:00,1.2\n", "text/csv"),
        ("notes.md", codecs.BOM_UTF16_BE, "utf-16-be", "# Garden\n", "text/markdown"),
        ("page.bin", codecs.BOM_UTF16_LE, "utf-16-le", "<!DOCTYPE html><p>Hi</p>", "text/html"),
        ("wide.txt", codecs.BOM_UTF32_LE, "utf-32-le", "Wide notes\n", "text/plain"),
        # An audio extension does not make text into audio.
        ("song.mp3", codecs.BOM_UTF16_LE, "utf-16-le", "Lyrics only\n", "text/plain"),
    ],
    ids=["txt", "csv", "md-be", "html", "utf32", "audio-extension"],
)
def test_text_with_a_utf16_or_utf32_byte_order_mark_is_text(
    synthetic, name, bom, codec, text, expected
):
    path = synthetic.file(name, bom + text.encode(codec))

    assert detect_media_type(path) == expected


@pytest.mark.parametrize("padding", [0, 20_000], ids=["small-tag", "tag-past-sniff-window"])
def test_id3_tag_before_flac_audio_does_not_make_it_mp3(synthetic, padding):
    flac_stream = b"fLaC" + bytes(64)
    tagged = synthetic.file("upload.bin", synthetic.id3_tag("Tide Talk", padding) + flac_stream)

    assert detect_media_type(tagged) == "audio/flac"


def test_id3_tag_alone_still_means_mp3(synthetic):
    tagged = synthetic.file("upload.bin", synthetic.id3_tag("Tide Talk") + bytes(32))

    assert detect_media_type(tagged) == "audio/mpeg"


def test_epub_whose_mimetype_is_encrypted_is_still_an_epub(synthetic):
    epub = synthetic.epub("locked.bin", title="Tides", author="Ada Example", chapters=[("A", "B")])
    synthetic.flag_zip_member_encrypted(epub, "mimetype")

    assert detect_media_type(epub) == "application/epub+zip"


def test_zip_containers_are_told_apart_by_their_members(synthetic):
    epub = synthetic.epub("book.zip", title="Tides", author="Ada Example", chapters=[("A", "B")])
    docx = synthetic.file("report.bin", b"")
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    plain = synthetic.file("bundle.epub", b"")
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("notes.txt", "synthetic")

    assert detect_media_type(epub) == "application/epub+zip"
    assert detect_media_type(docx) == DOCX
    assert detect_media_type(plain) == "application/zip"


@pytest.mark.parametrize("forged_count", [False, True])
def test_zip_member_limit_is_checked_before_zipfile_loads_members(
    synthetic, monkeypatch, forged_count
):
    path = synthetic.file("many.epub", b"")
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(4):
            archive.writestr(f"entry-{index}", b"")
    if forged_count:
        content = bytearray(path.read_bytes())
        end = content.rfind(b"PK\x05\x06")
        struct.pack_into("<HH", content, end + 8, 1, 1)
        path.write_bytes(content)
    monkeypatch.setattr(formats, "_ZIP_MAX_ENTRIES", 3, raising=False)
    loaded = []
    original = zipfile.ZipFile._RealGetContents

    def load_members(archive):
        loaded.append(True)
        return original(archive)

    monkeypatch.setattr(zipfile.ZipFile, "_RealGetContents", load_members)
    with pytest.raises(ConversionError, match="ZIP"):
        detect_media_type(path)
    assert loaded == []


def test_zip_total_uncompressed_size_is_checked_before_loading_members(synthetic, monkeypatch):
    path = synthetic.file("large.docx", b"")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", b"a" * 100)
        archive.writestr("word/styles.xml", b"b" * 100)
    monkeypatch.setattr(formats, "_ZIP_MAX_UNCOMPRESSED_BYTES", 150, raising=False)
    loaded = []
    original = zipfile.ZipFile._RealGetContents

    def load_members(archive):
        loaded.append(True)
        return original(archive)

    monkeypatch.setattr(zipfile.ZipFile, "_RealGetContents", load_members)
    with pytest.raises(ConversionError, match="ZIP"):
        detect_media_type(path)
    assert loaded == []


def test_kindle_files_are_recognized_from_the_palmdb_header(synthetic):
    assert detect_media_type(synthetic.palmdb("book.azw3", encryption=0)) == (
        "application/vnd.amazon.mobi8-ebook"
    )
    assert detect_media_type(synthetic.palmdb("book.bin", encryption=0)) == (
        "application/x-mobipocket-ebook"
    )


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("notes.md", "# Garden notes\n\nPlant beans.\n", "text/markdown"),
        ("levels.csv", "hour,level\n06:00,1.2\n", "text/csv"),
        ("README", "Plain synthetic text with no extension.\n", "text/plain"),
        # Text that starts like a weak binary signature is still text.
        ("cars.md", "BMW owners keep notes about tides.\n", "text/markdown"),
        ("tags.txt", "ID3 tags explained in plain words.\n", "text/plain"),
        ("accents", "Café notes — synthetic.\n", "text/plain"),
    ],
)
def test_extension_and_text_decide_when_there_is_no_signature(synthetic, name, content, expected):
    path = synthetic.directory / name
    path.write_text(content, encoding="utf-8")

    assert detect_media_type(path) == expected


def test_unknown_binary_is_octet_stream(synthetic):
    path = synthetic.directory / "blob"
    path.write_bytes(bytes(range(256)) * 4)

    assert detect_media_type(path) == "application/octet-stream"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
@pytest.mark.parametrize(
    ("name", "codec_args", "expected"),
    [
        ("tone.mp3", ["-c:a", "libmp3lame"], "audio/mpeg"),
        ("bare.mp3", ["-c:a", "libmp3lame", "-id3v2_version", "0"], "audio/mpeg"),
        ("bare.aac", ["-c:a", "aac", "-f", "adts"], "audio/aac"),
        ("tagged.aac", ["-c:a", "aac", "-f", "adts", "-write_id3v2", "1"], "audio/aac"),
        ("tone.m4a", ["-c:a", "aac"], "audio/mp4"),
        ("tone.flac", ["-c:a", "flac"], "audio/flac"),
        ("tone.ogg", ["-c:a", "flac"], "audio/ogg"),
        ("clip.mp4", ["-c:v", "mpeg4"], "video/mp4"),
        ("clip.mov", ["-c:v", "mpeg4"], "video/quicktime"),
        ("clip.mkv", ["-c:v", "mpeg4"], "video/x-matroska"),
        ("clip.avi", ["-c:v", "mpeg4"], "video/x-msvideo"),
    ],
)
def test_files_written_by_ffmpeg_are_detected_without_their_extension(
    synthetic, name, codec_args, expected
):
    path = synthetic.directory / name
    if expected.startswith("video/"):
        source = ["-f", "lavfi", "-i", "color=c=blue:s=64x48:d=1"]
    else:
        source = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    synthetic.ffmpeg(*source, *codec_args, str(path))
    renamed = path.rename(path.with_name("upload.bin"))

    assert detect_media_type(renamed) == expected


def test_sha256_file_matches_a_one_shot_digest_across_chunks(synthetic):
    data = os.urandom(3 * 1024 * 1024 + 17)
    path = synthetic.directory / "large.bin"
    path.write_bytes(data)

    assert sha256_file(path) == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("bundle.zip", None),
        ("drawing.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'),
        ("legacy.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(64)),
        ("blob", bytes(range(256))),
    ],
    ids=["zip", "svg", "legacy-doc", "binary"],
)
def test_unsupported_formats_are_refused_before_anything_is_written(
    synthetic, tmp_path, name, content
):
    path = synthetic.directory / name
    if content is None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("notes.txt", "synthetic")
    else:
        path.write_bytes(content)
    assets_dir = tmp_path / "assets"

    with pytest.raises(UnsupportedFormatError):
        convert_source(path, assets_dir, ConversionOptions(models_dir=tmp_path / "models"))
    assert not assets_dir.exists()


def test_a_missing_source_is_a_conversion_error(tmp_path):
    with pytest.raises(ConversionError, match="does not exist"):
        convert_source(
            tmp_path / "gone.pdf",
            tmp_path / "assets",
            ConversionOptions(models_dir=tmp_path / "models"),
        )
