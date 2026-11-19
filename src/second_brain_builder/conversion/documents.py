"""Documents: Docling conversion, document metadata, and Calibre for Kindle ebooks.

Docling is imported only inside functions, so importing this module needs no
worker dependencies.
"""

import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from second_brain_builder.conversion.formats import ZIP_READ_ERRORS, SourceFormat, _open_zip
from second_brain_builder.conversion.models import (
    ConversionError,
    DrmProtectedError,
    SourceMetadata,
)
from second_brain_builder.conversion.transcript import ASSETS_PREFIX

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc.document import DoclingDocument

PDF = "application/pdf"
EPUB = "application/epub+zip"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_OOXML = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    PPTX,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
# Formats whose Docling page count is a real page or slide count.
_PAGED = {PDF, PPTX}

# Pictures in PDFs and images are cropped from pages rendered at this scale (144 dpi).
_PICTURE_IMAGE_SCALE = 2.0
_METADATA_MAX_BYTES = 4 * 1024 * 1024
_CALIBRE_TIMEOUT_SECONDS = 30 * 60
_ERROR_TAIL_CHARS = 600

_DC = "{http://purl.org/dc/elements/1.1/}"
_CONTAINER_ROOTFILE = "{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
_WHITESPACE = re.compile(r"\s+")


@dataclass
class DocumentOutput:
    """What Docling produced for one document."""

    markdown: str
    title: str | None
    page_count: int | None
    warnings: list[str] = field(default_factory=list)


def package_version(name: str) -> str:
    """Installed version of a distribution, or ``unknown``."""
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return "unknown"


def require_docling() -> None:
    """Raise ``ConversionError`` if Docling is not installed."""
    try:
        import docling.document_converter  # noqa: F401
    except ImportError as error:
        raise ConversionError(
            "Docling is not installed. Conversion runs in the worker, "
            "which installs the 'worker' extra."
        ) from error


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = _WHITESPACE.sub(" ", text).strip()
    return cleaned or None


@lru_cache(maxsize=8)
def _docling_converter(input_format: str) -> "DocumentConverter":
    """One Docling converter per input format. It keeps its loaded models."""
    from docling.datamodel.backend_options import (
        EpubBackendOptions,
        HTMLBackendOptions,
        MarkdownBackendOptions,
    )
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import (
        DocumentConverter,
        EpubFormatOption,
        FormatOption,
        HTMLFormatOption,
        ImageFormatOption,
        MarkdownFormatOption,
        PdfFormatOption,
    )

    docling_format = InputFormat(input_format)
    pdf_pipeline = PdfPipelineOptions(
        generate_picture_images=True, images_scale=_PICTURE_IMAGE_SCALE
    )
    format_options: dict[InputFormat, FormatOption] = {}
    if docling_format == InputFormat.PDF:
        format_options[docling_format] = PdfFormatOption(pipeline_options=pdf_pipeline)
    elif docling_format == InputFormat.IMAGE:
        format_options[docling_format] = ImageFormatOption(pipeline_options=pdf_pipeline)
    elif docling_format == InputFormat.HTML:
        # Local and remote fetch stay off, so only data: URI images are read.
        format_options[docling_format] = HTMLFormatOption(
            backend_options=HTMLBackendOptions(fetch_images=True)
        )
    elif docling_format == InputFormat.MD:
        format_options[docling_format] = MarkdownFormatOption(
            backend_options=MarkdownBackendOptions(fetch_images=True)
        )
    elif docling_format == InputFormat.EPUB:
        # Docling extracts the EPUB to a temporary directory and confines local
        # fetches to it, so images come only from inside the EPUB.
        format_options[docling_format] = EpubFormatOption(
            backend_options=EpubBackendOptions(fetch_images=True, enable_local_fetch=True)
        )
    return DocumentConverter(allowed_formats=[docling_format], format_options=format_options)


def convert_document(path: Path, fmt: SourceFormat, assets_out: Path) -> DocumentOutput:
    """Convert a document with Docling. Images are written to ``assets_out``."""
    from docling.datamodel.base_models import ConversionStatus
    from docling_core.types.doc.base import ImageRefMode

    if fmt.docling_format is None:
        raise ConversionError(f"Docling does not read {fmt.media_type}.")
    converter = _docling_converter(fmt.docling_format)
    try:
        result = converter.convert(path, raises_on_error=True)
    except Exception as error:  # Docling raises many error types for malformed input.
        raise ConversionError(
            f"Docling could not convert the {fmt.extension.upper()} file: {error}"
        ) from error

    warnings: list[str] = []
    if result.status == ConversionStatus.PARTIAL_SUCCESS:
        details = "; ".join(item.error_message for item in result.errors) or "no details"
        warnings.append(f"Docling converted the file only in part: {details}")
    markdown = result.document.export_to_markdown(
        image_mode=ImageRefMode.REFERENCED,
        image_dir=assets_out,
        image_uri_prefix=ASSETS_PREFIX,
    )
    page_count = result.input.page_count if fmt.media_type in _PAGED else None
    return DocumentOutput(
        markdown=markdown,
        title=_docling_title(result.document),
        page_count=page_count or None,
        warnings=warnings,
    )


def _docling_title(document: "DoclingDocument") -> str | None:
    """Text of the first item that Docling labels as the title."""
    from docling_core.types.doc.common.content_layer import ContentLayer
    from docling_core.types.doc.items.text import TitleItem

    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
    for item, _level in document.iterate_items(included_content_layers=layers):
        if isinstance(item, TitleItem):
            return _clean(item.text)
    return None


def read_document_metadata(path: Path, fmt: SourceFormat) -> SourceMetadata:
    """Title, author, and page count from the metadata that the file carries."""
    if fmt.media_type == PDF:
        return _pdf_metadata(path)
    if fmt.media_type == EPUB:
        return _epub_metadata(path)
    if fmt.media_type in _OOXML:
        return _ooxml_metadata(path)
    return SourceMetadata()


def _pdf_metadata(path: Path) -> SourceMetadata:
    import pypdfium2  # type: ignore[import-untyped]

    document = pypdfium2.PdfDocument(path)
    try:
        info = document.get_metadata_dict(skip_empty=True)
        page_count = len(document)
    finally:
        document.close()
    title = info.get("Title")
    author = info.get("Author")
    return SourceMetadata(
        title=_clean(title if isinstance(title, str) else None),
        author=_clean(author if isinstance(author, str) else None),
        pages=page_count,
    )


def _parse_xml(data: bytes) -> ElementTree.Element | None:
    """Parse a small metadata file from inside a Source.

    Metadata files never need a DTD. Refusing DTDs rules out entity expansion, and
    ElementTree never loads external resources.
    """
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        return None
    try:
        return ElementTree.fromstring(data)  # noqa: S314 (see the docstring)
    except ElementTree.ParseError:
        return None


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes | None:
    """A small member of a ZIP container, or None if it is missing or cannot be read.

    Metadata is best effort: a damaged metadata file must not stop the conversion.
    """
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _METADATA_MAX_BYTES:
        return None
    try:
        return archive.read(info)
    except ZIP_READ_ERRORS:
        return None


def _dc_metadata(root: ElementTree.Element) -> SourceMetadata:
    """Dublin Core title and creators, as used by EPUB packages and OOXML core properties."""
    titles = [_clean(element.text) for element in root.iter(f"{_DC}title")]
    creators: list[str] = []
    for element in root.iter(f"{_DC}creator"):
        creator = _clean(element.text)
        if creator and creator not in creators:
            creators.append(creator)
    return SourceMetadata(
        title=next((title for title in titles if title), None),
        author=", ".join(creators) or None,
    )


def _epub_metadata(path: Path) -> SourceMetadata:
    try:
        with _open_zip(path) as archive:
            container = _read_member(archive, "META-INF/container.xml")
            root = _parse_xml(container) if container else None
            if root is None:
                return SourceMetadata()
            rootfile = next(root.iter(_CONTAINER_ROOTFILE), None)
            package_path = rootfile.get("full-path") if rootfile is not None else None
            if not package_path:
                return SourceMetadata()
            package = _read_member(archive, str(PurePosixPath(package_path)))
    except ZIP_READ_ERRORS:
        return SourceMetadata()
    package_root = _parse_xml(package) if package else None
    return _dc_metadata(package_root) if package_root is not None else SourceMetadata()


def _ooxml_metadata(path: Path) -> SourceMetadata:
    try:
        with _open_zip(path) as archive:
            core = _read_member(archive, "docProps/core.xml")
    except ZIP_READ_ERRORS:
        return SourceMetadata()
    root = _parse_xml(core) if core else None
    return _dc_metadata(root) if root is not None else SourceMetadata()


def merge_metadata(found: SourceMetadata, output: DocumentOutput) -> SourceMetadata:
    """File metadata first; Docling's title and page count fill the gaps."""
    return SourceMetadata(
        title=found.title or output.title,
        author=found.author,
        pages=found.pages if found.pages is not None else output.page_count,
        duration_seconds=found.duration_seconds,
    )


def _tail(text: str) -> str:
    text = text.strip()
    return text[-_ERROR_TAIL_CHARS:] if text else "no output"


def kindle_to_epub(source: Path, work_dir: Path) -> tuple[Path, str]:
    """Convert a MOBI or AZW3 file to EPUB with Calibre.

    Return the EPUB path and the converter label, such as ``calibre 7.6.0``.
    Calibre runs with an empty configuration directory, so no Calibre plug-in
    (a DRM removal plug-in, for example) can take part.
    """
    executable = shutil.which("ebook-convert")
    if executable is None:
        raise ConversionError(
            "Calibre is not installed: the ebook-convert command is missing. "
            "The worker image includes it."
        )
    config_dir = work_dir / "calibre-config"
    config_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "CALIBRE_CONFIG_DIRECTORY": str(config_dir)}
    output = work_dir / "calibre-output.epub"
    try:
        version = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=60,
            check=False,
        )
        completed = subprocess.run(
            [executable, str(source), str(output)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=_CALIBRE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ConversionError("Calibre did not finish the ebook conversion in time.") from error
    messages = f"{completed.stdout}\n{completed.stderr}"
    if "DRMError" in messages:
        raise DrmProtectedError("Calibre reports that the ebook has DRM.")
    if completed.returncode != 0 or not output.is_file():
        raise ConversionError(f"Calibre could not convert the ebook: {_tail(completed.stderr)}")
    match = re.search(r"calibre ([0-9][0-9.]*)", version.stdout)
    label = f"calibre {match.group(1)}" if match else "calibre"
    return output, label
