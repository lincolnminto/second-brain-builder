"""Real Docling conversions of small synthetic documents."""

import base64
import codecs
import http.server
import importlib.metadata
import shutil
import subprocess
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from second_brain_builder.conversion import (
    ConversionError,
    ConversionOptions,
    convert_source,
)

pytest.importorskip("docling", reason="Docling comes with the worker dependencies")

HAS_CALIBRE = shutil.which("ebook-convert") is not None
FONT_OBFUSCATION_XML = """<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
    <enc:CipherData><enc:CipherReference URI="OEBPS/fonts/serif.otf"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""


@pytest.fixture
def options(tmp_path):
    return ConversionOptions(models_dir=tmp_path / "models")


@dataclass
class RecordingProxy:
    url: str
    requests: list[str] = field(default_factory=list)


@pytest.fixture
def recording_proxy(synthetic, monkeypatch) -> Iterator[RecordingProxy]:
    """An HTTP proxy on 127.0.0.1, set in the environment, that records each request.

    It answers every request with a PNG. Docling refuses to connect to loopback and
    private addresses by itself, but it sends requests for public hosts through a
    proxy from the environment, as a worker behind a proxy would. So a fetch of
    ``http://example.com/...`` reaches this proxy and nothing leaves the machine.
    """
    picture = synthetic.png(16, 16, (255, 0, 0))
    proxy = RecordingProxy(url="")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            proxy.requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(picture)))
            self.end_headers()
            self.wfile.write(picture)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    proxy.url = f"http://127.0.0.1:{server.server_port}"
    for name in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(name, proxy.url)
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield proxy
    server.shutdown()
    server.server_close()
    thread.join()


def _asset_links(markdown):
    return [line for line in markdown.splitlines() if "](assets/" in line]


@pytest.mark.filterwarnings("ignore:Could not process an image:UserWarning")
def test_html_keeps_structure_and_embedded_images_only(synthetic, tmp_path, options):
    picture = synthetic.png(40, 30, (200, 30, 30))
    html = synthetic.file(
        "tides.html",
        "<!DOCTYPE html><html><head><title>Tide Tables</title></head><body>"
        "<h1>Tide Tables</h1><p>The tide rises twice a day.</p>"
        f'<img src="data:image/png;base64,{base64.b64encode(picture).decode()}" alt="chart">'
        '<img src="https://example.com/tracker.png" alt="remote">'
        "<table><tr><th>Hour</th><th>Level</th></tr><tr><td>06:00</td><td>1.2 m</td></tr>"
        "</table></body></html>",
    )
    assets_dir = tmp_path / "assets"

    result = convert_source(html, assets_dir, options)

    assert "# Tide Tables" in result.markdown
    assert "The tide rises twice a day." in result.markdown
    assert "| 06:00" in result.markdown
    assert "example.com" not in result.markdown
    assert [asset.parent for asset in result.assets] == [assets_dir]
    (asset,) = result.assets
    assert _asset_links(result.markdown) == [f"![Image](assets/{asset.name})"]
    assert synthetic.png_size(asset.read_bytes()) == (40, 30)
    assert any("placeholder" in warning for warning in result.warnings)
    assert result.metadata.title == "Tide Tables"
    assert result.converter == f"docling {importlib.metadata.version('docling')}"


@pytest.mark.filterwarnings("ignore:Could not process an image:UserWarning")
@pytest.mark.parametrize("kind", ["html", "md"])
def test_remote_images_are_never_fetched(synthetic, tmp_path, options, recording_proxy, kind):
    # A remote image in an untrusted Source can be a tracking pixel.
    remote = "http://example.com/pixel.png"
    if kind == "html":
        source = synthetic.file(
            "page.html", f'<html><body><h1>Pictures</h1><img src="{remote}"></body></html>'
        )
    else:
        source = synthetic.file("page.md", f"# Pictures\n\n![pixel]({remote})\n")

    result = convert_source(source, tmp_path / "assets", options)

    assert "# Pictures" in result.markdown
    assert recording_proxy.requests == []
    assert result.assets == []


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        # csv.Sniffer takes lines with a common delimiter for CSV.
        (
            "journal.txt",
            "Monday, the river rose.\nTuesday, the river fell.\nWednesday, we fished.\n",
            ["Monday, the river rose.", "Wednesday, we fished."],
        ),
        # Excel "Text (Tab delimited)" export.
        ("levels.txt", "Hour\tLevel\r\n06:00\t1.2\r\n07:00\t1.4\r\n", ["06:00", "1.4"]),
        ("README", "Plain text, with commas, and no extension.\n", ["with commas"]),
    ],
    ids=["comma-prose", "tab-export", "no-extension"],
)
def test_plain_text_converts_whatever_its_delimiters(
    synthetic, tmp_path, options, name, text, expected
):
    source = synthetic.file(name, text)

    result = convert_source(source, tmp_path / "assets", options)

    for fragment in expected:
        assert fragment in result.markdown


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("notes.txt", "River notes\n", "River notes"),
        ("levels.csv", "hour,level\n06:00,1.2\n", "| 06:00  |"),
        ("page.html", "<html><body><h1>Tides</h1></body></html>", "# Tides"),
    ],
)
def test_utf16_text_sources_convert(synthetic, tmp_path, options, name, text, expected):
    source = synthetic.file(name, codecs.BOM_UTF16_LE + text.encode("utf-16-le"))

    result = convert_source(source, tmp_path / "assets", options)

    assert expected in result.markdown


def test_markdown_upload_without_extension_converts(synthetic, tmp_path, options):
    notes = synthetic.file("upload-7f3a", "# Garden Notes\n\nPlant *beans* in spring.\n\n- one\n")

    result = convert_source(notes, tmp_path / "assets", options)

    assert result.markdown.startswith("# Garden Notes\n")
    assert "Plant *beans* in spring." in result.markdown
    assert result.metadata.title == "Garden Notes"
    assert result.assets == []


def test_docx_reads_core_properties_and_extracts_pictures(synthetic, tmp_path, options):
    docx = pytest.importorskip("docx")
    picture = synthetic.file("figure.png", synthetic.png(64, 48, (20, 120, 40)))
    document = docx.Document()
    document.core_properties.title = "Harbor Report"
    document.core_properties.author = "Ada Example"
    document.add_heading("Harbor Report", 0)
    document.add_paragraph("Boats arrive at dawn.")
    document.add_picture(str(picture))
    source = synthetic.directory / "harbor.docx"
    document.save(str(source))

    result = convert_source(source, tmp_path / "assets", options)

    assert "Boats arrive at dawn." in result.markdown
    (asset,) = result.assets
    assert f"(assets/{asset.name})" in result.markdown
    assert synthetic.png_size(asset.read_bytes()) == (64, 48)
    assert result.metadata.title == "Harbor Report"
    assert result.metadata.author == "Ada Example"


def test_epub_follows_the_spine_and_extracts_images(synthetic, tmp_path, options):
    epub = synthetic.epub(
        "keeper.epub",
        title="The Lighthouse Keeper",
        author="Ada Example",
        chapters=[("Dusk", "The lamp was lit at dusk."), ("Dawn", "The lamp went out at dawn.")],
        image=synthetic.png(32, 32, (240, 200, 0)),
        extra_members={"META-INF/encryption.xml": FONT_OBFUSCATION_XML},
    )

    result = convert_source(epub, tmp_path / "assets", options)

    dusk = result.markdown.index("The lamp was lit at dusk.")
    dawn = result.markdown.index("The lamp went out at dawn.")
    assert dusk < dawn
    (asset,) = result.assets
    assert f"(assets/{asset.name})" in result.markdown
    assert result.metadata.title == "The Lighthouse Keeper"
    assert result.metadata.author == "Ada Example"


def test_assets_dir_keeps_other_files_and_gets_only_referenced_assets(synthetic, tmp_path, options):
    picture = synthetic.png(8, 8, (0, 0, 255))
    html = synthetic.file(
        "page.html",
        "<html><body><h1>Blue</h1>"
        f'<img src="data:image/png;base64,{base64.b64encode(picture).decode()}"></body></html>',
    )
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "notes.txt").write_text("kept")

    result = convert_source(html, assets_dir, options)

    assert sorted(path.name for path in assets_dir.iterdir()) == sorted(
        ["notes.txt", *(asset.name for asset in result.assets)]
    )
    for asset in result.assets:
        assert f"(assets/{asset.name})" in result.markdown


def test_failed_conversion_writes_no_assets(synthetic, tmp_path, options):
    broken = synthetic.file("broken.docx", b"")
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("[Content_Types].xml", "not xml at all")
        archive.writestr("word/document.xml", "<w:document")
    assets_dir = tmp_path / "assets"

    with pytest.raises(ConversionError, match="Docling could not convert"):
        convert_source(broken, assets_dir, options)
    assert not assets_dir.exists()


@pytest.mark.parametrize("damage", ["corrupt", "encrypted"])
def test_damaged_metadata_member_is_a_conversion_error(synthetic, tmp_path, options, damage):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.core_properties.title = "Harbor Report"
    document.add_paragraph("Boats arrive at dawn.")
    source = synthetic.directory / "harbor.docx"
    document.save(str(source))
    if damage == "corrupt":
        synthetic.corrupt_zip_member(source, "docProps/core.xml")
    else:
        synthetic.flag_zip_member_encrypted(source, "docProps/core.xml")
    assets_dir = tmp_path / "assets"

    # The metadata read skips the member. Docling's reader needs every part, so it
    # fails, and the failure comes out as the documented ConversionError.
    with pytest.raises(ConversionError, match="Docling could not convert"):
        convert_source(source, assets_dir, options)
    assert not assets_dir.exists()


@pytest.mark.skipif(HAS_CALIBRE, reason="checks the error when Calibre is missing")
def test_kindle_file_without_calibre_is_a_clear_error(synthetic, tmp_path, options):
    ebook = synthetic.palmdb("book.azw3", encryption=0)

    with pytest.raises(ConversionError, match="ebook-convert"):
        convert_source(ebook, tmp_path / "assets", options)


@pytest.mark.skipif(not HAS_CALIBRE, reason="Calibre (ebook-convert) is not installed")
def test_azw3_converts_through_calibre(synthetic, tmp_path, options):
    epub = synthetic.epub(
        "keeper.epub",
        title="The Lighthouse Keeper",
        author="Ada Example",
        chapters=[("Dusk", "The lamp was lit at dusk.")],
    )
    azw3 = synthetic.directory / "keeper.azw3"
    subprocess.run(["ebook-convert", str(epub), str(azw3)], check=True, capture_output=True)

    result = convert_source(azw3, tmp_path / "assets", options)

    assert "The lamp was lit at dusk." in result.markdown
    assert result.converter.startswith("calibre ")
    assert "docling" in result.converter
    assert result.metadata.title == "The Lighthouse Keeper"


@pytest.mark.slow
@pytest.mark.timeout(1800)  # the first run downloads the layout models
def test_pdf_keeps_text_pictures_and_metadata(
    synthetic, tmp_path, model_cache, isolated_conversion
):
    pdf = synthetic.pdf(
        "river.pdf",
        ["Synthetic River Report", "The river floods each spring.", "Farmers plant after."],
        title="River Report",
        author="Ada Example",
        image=(64, 48, (200, 30, 30)),
    )
    options = ConversionOptions(models_dir=model_cache)

    result = isolated_conversion.convert(pdf, tmp_path / "assets", options)

    # The layout models load from, or download into, the models dir and nowhere else.
    assert isolated_conversion.model_files_in_home() == []
    assert any((model_cache / "hub").glob("models--docling-project--*"))

    assert "Synthetic River Report" in result.markdown
    assert "The river floods each spring." in result.markdown
    (asset,) = result.assets
    assert _asset_links(result.markdown) == [f"![Image](assets/{asset.name})"]
    assert asset.read_bytes().startswith(b"\x89PNG")
    assert result.metadata.title == "River Report"
    assert result.metadata.author == "Ada Example"
    assert result.metadata.pages == 1
