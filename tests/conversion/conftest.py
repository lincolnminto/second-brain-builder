"""Synthetic Sources for the conversion tests.

Every file is built at test time from synthetic text, so no fixture holds real data.
Tests get the builders through the ``synthetic`` fixture: with the importlib import
mode, test modules cannot import from this file.
"""

import io
import os
import struct
import subprocess
import sys
import wave
import zipfile
import zlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from second_brain_builder.conversion import ConversionOptions, ConversionResult

EPUB_CONTAINER = (
    '<?xml version="1.0"?>'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)


def _pdf_string(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return b"(" + escaped.encode("latin-1") + b")"


class SyntheticSources:
    """Builds synthetic Sources in one directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def file(self, name: str, data: bytes | str) -> Path:
        path = self.directory / name
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_bytes(data)
        return path

    @staticmethod
    def png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
        """A solid-color PNG, encoded without Pillow."""

        def chunk(kind: bytes, data: bytes) -> bytes:
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        row = b"\x00" + bytes(rgb) * width
        header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(row * height))
            + chunk(b"IEND", b"")
        )

    @staticmethod
    def png_size(data: bytes) -> tuple[int, int]:
        """Width and height from a PNG header."""
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        return width, height

    def pdf(
        self,
        name: str,
        lines: Sequence[str],
        *,
        title: str,
        author: str,
        image: tuple[int, int, tuple[int, int, int]] | None = None,
        security_handler: str | None = None,
    ) -> Path:
        """A one-page PDF written by hand: Helvetica text lines and an optional RGB image.

        ``security_handler`` adds an /Encrypt dictionary with that /Filter. "Standard"
        has a user password that is not empty, so a reader needs a password. Other
        names, such as "EBX_HANDLER" (Adobe ADEPT), are store DRM handlers.
        """
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        content = [b"BT /F1 20 Tf 72 720 Td " + _pdf_string(lines[0]) + b" Tj ET"]
        for index, line in enumerate(lines[1:]):
            y = 680 - 20 * index
            content.append(b"BT /F1 12 Tf 72 %d Td " % y + _pdf_string(line) + b" Tj ET")
        resources = b"/Font << /F1 %d 0 R >>" % font
        if image is not None:
            width, height, rgb = image
            pixels = zlib.compress(bytes(rgb) * (width * height))
            picture = add(
                b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                b"/Length %d >>\nstream\n" % (width, height, len(pixels)) + pixels + b"\nendstream"
            )
            resources += b" /XObject << /Im1 %d 0 R >>" % picture
            content.append(b"q 240 0 0 180 72 360 cm /Im1 Do Q")
        stream = b"\n".join(content)
        contents = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        pages_number = len(objects) + 2
        page = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Resources << %s >> "
            b"/Contents %d 0 R >>" % (pages_number, resources, contents)
        )
        pages = add(b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % page)
        catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages)
        info = add(b"<< /Title " + _pdf_string(title) + b" /Author " + _pdf_string(author) + b" >>")
        trailer = b"<< /Size %d /Root %d 0 R /Info %d 0 R" % (len(objects) + 1, catalog, info)
        if security_handler == "Standard":
            security = add(
                b"<< /Filter /Standard /V 1 /R 2 /O <"
                + b"11" * 32
                + b"> /U <"
                + b"22" * 32
                + b"> /P -44 >>"
            )
        elif security_handler is not None:
            security = add(
                b"<< /Filter /" + security_handler.encode("ascii") + b" /V 2 /R 3 /Length 128 >>"
            )
        if security_handler is not None:
            trailer = b"<< /Size %d /Root %d 0 R /Info %d 0 R /Encrypt %d 0 R /ID [<%s> <%s>]" % (
                len(objects) + 1,
                catalog,
                info,
                security,
                b"ab" * 16,
                b"ab" * 16,
            )

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        for offset in offsets:
            out += b"%010d 00000 n \n" % offset
        out += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
        return self.file(name, bytes(out))

    def epub(
        self,
        name: str,
        *,
        title: str,
        author: str,
        chapters: Sequence[tuple[str, str]],
        image: bytes | None = None,
        extra_members: dict[str, str] | None = None,
    ) -> Path:
        """A minimal EPUB 3: one XHTML file per chapter and an optional PNG in chapter 1.

        ``extra_members`` adds files such as ``META-INF/encryption.xml``.
        """
        manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        ]
        spine = []
        files: dict[str, str | bytes] = {}
        for index, (heading, paragraph) in enumerate(chapters, start=1):
            chapter = f"chapter{index}.xhtml"
            picture = '<img src="images/figure.png" alt="figure"/>' if image and index == 1 else ""
            files[f"OEBPS/{chapter}"] = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>'
                f"{heading}</title></head><body><h1>{heading}</h1><p>{paragraph}</p>"
                f"{picture}</body></html>"
            )
            manifest.append(
                f'<item id="c{index}" href="{chapter}" media-type="application/xhtml+xml"/>'
            )
            spine.append(f'<itemref idref="c{index}"/>')
        if image is not None:
            files["OEBPS/images/figure.png"] = image
            manifest.append('<item id="figure" href="images/figure.png" media-type="image/png"/>')
        toc = "".join(
            f'<li><a href="chapter{index}.xhtml">{heading}</a></li>'
            for index, (heading, _) in enumerate(chapters, start=1)
        )
        files["OEBPS/nav.xhtml"] = (
            '<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head>'
            f'<body><nav epub:type="toc"><ol>{toc}</ol></nav></body></html>'
        )
        files["OEBPS/content.opf"] = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="id">urn:uuid:00000000-0000-4000-8000-000000000000'
            f"</dc:identifier><dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>"
            "<dc:language>en</dc:language></metadata>"
            f"<manifest>{''.join(manifest)}</manifest><spine>{''.join(spine)}</spine></package>"
        )
        files["META-INF/container.xml"] = EPUB_CONTAINER
        files.update(extra_members or {})

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
            for member, data in files.items():
                archive.writestr(member, data)
        return self.file(name, buffer.getvalue())

    @staticmethod
    def flag_zip_member_encrypted(path: Path, member: str) -> None:
        """Set the "encrypted" bit of one ZIP member, as a password-protected ZIP has it."""
        raw = bytearray(path.read_bytes())
        with zipfile.ZipFile(path) as archive:
            raw[archive.getinfo(member).header_offset + 6] |= 0x01
        name = member.encode("utf-8")
        position = raw.find(b"PK\x01\x02")
        while position != -1:
            (name_length,) = struct.unpack("<H", raw[position + 28 : position + 30])
            if raw[position + 46 : position + 46 + name_length] == name:
                raw[position + 8] |= 0x01
            position = raw.find(b"PK\x01\x02", position + 4)
        path.write_bytes(bytes(raw))

    @staticmethod
    def corrupt_zip_member(path: Path, member: str) -> None:
        """Make the deflate stream of one ZIP member start with a reserved block type."""
        raw = bytearray(path.read_bytes())
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(member)
        assert info.compress_type == zipfile.ZIP_DEFLATED
        offset = info.header_offset
        name_length, extra_length = struct.unpack("<HH", raw[offset + 26 : offset + 30])
        raw[offset + 30 + name_length + extra_length] = 0xFF
        path.write_bytes(bytes(raw))

    @staticmethod
    def id3_tag(title: str, padding: int = 0) -> bytes:
        """An ID3v2.3 tag with a title frame and ``padding`` zero bytes."""
        text = b"\x00" + title.encode("latin-1")
        body = b"TIT2" + struct.pack(">I", len(text)) + b"\x00\x00" + text + bytes(padding)
        size = len(body)
        syncsafe = bytes((size >> shift) & 0x7F for shift in (21, 14, 7, 0))
        return b"ID3\x03\x00\x00" + syncsafe + body

    def palmdb(self, name: str, *, encryption: int) -> Path:
        """A PalmDB file of type BOOKMOBI. Record 0 holds the PalmDOC and MOBI headers."""
        text = b"Synthetic ebook text."
        header = bytearray(78)
        header[0:16] = b"Synthetic_Ebook\x00"
        header[60:68] = b"BOOKMOBI"
        header[76:78] = struct.pack(">H", 2)
        record0_offset = 78 + 2 * 8 + 2
        record0 = struct.pack(">HHIHHHH", 1, 0, len(text), 1, 4096, encryption, 0)
        record0 += b"MOBI" + bytes(228)
        record1_offset = record0_offset + len(record0)
        entries = struct.pack(">II", record0_offset, 0) + struct.pack(">II", record1_offset, 1)
        return self.file(name, bytes(header) + entries + b"\x00\x00" + record0 + text)

    def silent_wav(self, name: str, seconds: float, rate: int = 16_000) -> Path:
        """A mono 16-bit WAV file of silence, written with the standard library."""
        path = self.directory / name
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            writer.writeframes(b"\x00\x00" * int(seconds * rate))
        return path

    @staticmethod
    def ffmpeg(*args: str) -> None:
        """Run ffmpeg quietly. A failure fails the test."""
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", *args],
            check=True,
            capture_output=True,
        )


@pytest.fixture
def synthetic(tmp_path: Path) -> SyntheticSources:
    """Builders for synthetic Sources, writing into a fresh directory."""
    directory = tmp_path / "sources"
    directory.mkdir()
    return SyntheticSources(directory)


@pytest.fixture
def model_cache(request: pytest.FixtureRequest) -> Path:
    """A models directory that survives test runs, so slow tests download each model once.

    It lives in the pytest cache (``.pytest_cache/``). Its content says nothing about
    the current run: to check where models go, use ``isolated_conversion``.
    """
    return request.config.cache.mkdir("sbb-conversion-models")


class IsolatedConversion:
    """Runs ``convert_source`` in a new Python process that has an empty home directory.

    Hugging Face reads HF_HOME once, when it is imported, so only a new process shows
    where model downloads go. The child gets no HF_HOME, no XDG cache directory, and
    no other model cache variable, and HOME is an empty directory. If Conversion did
    not point the downloads at ``ConversionOptions.models_dir``, the libraries would
    use their defaults under HOME, and ``model_files_in_home`` would list the files.
    """

    _SCRIPT = (
        "import sys\n"
        "from pathlib import Path\n"
        "from second_brain_builder.conversion import ConversionOptions, convert_source\n"
        "options = ConversionOptions.model_validate_json(sys.argv[3])\n"
        "result = convert_source(Path(sys.argv[1]), Path(sys.argv[2]), options)\n"
        "print(result.model_dump_json())\n"
    )
    _CLEARED_PREFIXES = ("HF_", "HUGGINGFACE_", "TRANSFORMERS_", "TORCH_", "XDG_", "DOCLING_")
    # Model checkpoints are megabytes. Small files (settings, locks) do not count.
    _MODEL_FILE_MIN_BYTES = 1_000_000

    def __init__(self, home: Path) -> None:
        self.home = home

    def convert(
        self, source: Path, assets_dir: Path, options: ConversionOptions
    ) -> ConversionResult:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(self._CLEARED_PREFIXES)
        }
        env["HOME"] = str(self.home)
        argv = [sys.executable, "-c", self._SCRIPT, str(source), str(assets_dir)]
        completed = subprocess.run(
            [*argv, options.model_dump_json()],
            env=env,
            cwd=self.home,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr[-4000:]
        return ConversionResult.model_validate_json(completed.stdout.strip().splitlines()[-1])

    def model_files_in_home(self) -> list[Path]:
        return sorted(
            path
            for path in self.home.rglob("*")
            if path.is_file() and path.stat().st_size >= self._MODEL_FILE_MIN_BYTES
        )


@pytest.fixture
def isolated_conversion(tmp_path: Path) -> IsolatedConversion:
    """Runs conversions in a new process with an empty HOME (see ``IsolatedConversion``)."""
    home = tmp_path / "home"
    home.mkdir()
    return IsolatedConversion(home)
