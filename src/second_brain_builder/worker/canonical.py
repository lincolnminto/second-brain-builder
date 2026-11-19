"""Canonical Documents in a clone: find them, read their facts, and build their frontmatter."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from second_brain_builder.brains import ASSETS_DIR_NAME, SOURCES_DIR, WIKI_DIR, WIKI_DIRS, is_slug
from second_brain_builder.conversion import SourceMetadata
from second_brain_builder.okf import (
    Frontmatter,
    OKFError,
    Page,
    PageType,
    Stamp,
    extract_links,
    parse_timestamp,
    read_page,
    resolve_link,
)

_log = logging.getLogger(__name__)

SOURCE_METADATA_KEY = "x-sbb-source"
"""The extension key of a Canonical Document with the facts about its Source."""

CONVERSION_STAMP = "process:sbb-conversion"
"""``generated.by`` of a Canonical Document: Conversion wrote it, not an LLM."""

_FORMAT_LABELS = {
    "pdf": "PDF",
    "docx": "Word document",
    "pptx": "PowerPoint presentation",
    "xlsx": "Excel workbook",
    "html": "HTML page",
    "xhtml": "XHTML page",
    "epub": "EPUB",
    "md": "Markdown",
    "txt": "Plain text",
    "csv": "CSV table",
    "png": "PNG image",
    "jpg": "JPEG image",
    "tiff": "TIFF image",
    "bmp": "BMP image",
    "webp": "WebP image",
    "gif": "GIF image",
    "mobi": "MOBI ebook",
    "azw3": "AZW3 ebook",
    "wav": "WAV audio",
    "mp3": "MP3 audio",
    "m4a": "M4A audio",
    "aac": "AAC audio",
    "ogg": "Ogg audio",
    "flac": "FLAC audio",
    "mp4": "MP4 video",
    "avi": "AVI video",
    "mov": "QuickTime video",
    "mkv": "Matroska video",
    "webm": "WebM video",
}


class SourceFacts(BaseModel):
    """The ``x-sbb-source`` mapping of a Canonical Document."""

    model_config = ConfigDict(extra="allow")

    original_filename: str | None = None
    sha256: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    converter: str | None = None
    added_at: Any = None
    """Kept as read: a quoted RFC 3339 string, or a ``datetime`` when this code builds it."""


class SourceRecord(BaseModel):
    """What the ``sources`` table caches about one Source, read from its Canonical Document."""

    slug: str
    folder: str
    canonical_path: PurePosixPath
    path: str | None
    title: str
    sha256: str | None
    media_type: str | None
    size_bytes: int | None
    added_at: datetime | None


def canonical_documents(root: Path) -> dict[str, PurePosixPath]:
    """The Canonical Document of every Source in the clone at ``root``, by Source id (slug).

    A Canonical Document is ``sources/<folder>/<slug>/<slug>.md``. Dot-paths, symbolic
    links, and ``assets`` directories are skipped. When two Folders hold the same
    slug, the first path in sorted order wins, with a warning.
    """
    found: dict[str, PurePosixPath] = {}
    start = root / SOURCES_DIR
    if start.is_symlink() or not start.is_dir():
        return found
    pending = [start]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            _log.warning("Skipped the directory %s: %s", directory, exc)
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
                continue
            if entry.name == ASSETS_DIR_NAME:
                continue
            pending.append(entry)
            candidate = entry / f"{entry.name}.md"
            if not is_slug(entry.name) or candidate.is_symlink() or not candidate.is_file():
                continue
            path = PurePosixPath(candidate.relative_to(root).as_posix())
            other = found.get(entry.name)
            if other is not None:
                _log.warning("Two Sources have the id %s: %s and %s", entry.name, other, path)
                found[entry.name] = min(other, path)
                continue
            found[entry.name] = path
    return found


def folder_of(canonical_path: PurePosixPath) -> PurePosixPath:
    """The Folder of a Canonical Document: the path between ``sources/`` and the Source dir."""
    return canonical_path.parent.parent.relative_to(SOURCES_DIR)


def folder_text(folder: PurePosixPath) -> str:
    """The stored form of a Folder: ``""`` for the root of ``sources/``, else ``a/b``."""
    return "" if folder == PurePosixPath() else folder.as_posix()


def folder_path(text: str) -> PurePosixPath:
    """A Folder from a payload (``""`` or ``a/b``) as a path."""
    return PurePosixPath(text) if text else PurePosixPath()


def summary_path(slug: str) -> PurePosixPath:
    """``wiki/summaries/<slug>.md``: the summary Wiki Page of a Source."""
    return WIKI_DIR / WIKI_DIRS[PageType.SUMMARY] / f"{slug}.md"


def read_source_record(root: Path, canonical_path: PurePosixPath) -> SourceRecord:
    """The ``sources`` row data of one Canonical Document.

    A file whose frontmatter cannot be read still gives a record, with the slug as
    the title, so that a damaged file does not hide its Source.
    """
    slug = canonical_path.stem
    folder = folder_text(folder_of(canonical_path))
    try:
        page = read_page(root, canonical_path)
    except (OKFError, OSError, ValueError) as exc:
        _log.warning("Cannot read %s: %s", canonical_path, exc)
        page = None
    frontmatter = page.frontmatter if page is not None else None
    facts = source_facts(frontmatter)
    title = " ".join((frontmatter.title or "").split()) if frontmatter else ""
    return SourceRecord(
        slug=slug,
        folder=folder,
        canonical_path=canonical_path,
        path=_source_file(canonical_path, frontmatter),
        title=title or slug,
        sha256=facts.sha256,
        media_type=facts.media_type,
        size_bytes=facts.size_bytes,
        added_at=_timestamp(facts.added_at),
    )


def source_facts(frontmatter: Frontmatter | None) -> SourceFacts:
    """The ``x-sbb-source`` mapping; empty when it is missing or has the wrong shape."""
    value = frontmatter.extensions.get(SOURCE_METADATA_KEY) if frontmatter else None
    if not isinstance(value, dict):
        return SourceFacts()
    try:
        return SourceFacts.model_validate(value)
    except ValidationError:
        return SourceFacts()


def source_file(
    canonical_path: PurePosixPath, frontmatter: Frontmatter | None
) -> PurePosixPath | None:
    """The Source file that ``resource`` names, if it is inside the Source directory."""
    text = _source_file(canonical_path, frontmatter)
    return PurePosixPath(text) if text else None


def describe_source(extension: str, metadata: SourceMetadata) -> str:
    """The one-line description of a Canonical Document, from metadata: ``EPUB, 412 pages.``"""
    label = _FORMAT_LABELS.get(extension, extension.upper())
    author = " ".join((metadata.author or "").split())
    parts = [f"{label} by {author}" if author else label]
    if metadata.pages:
        parts.append(f"{metadata.pages} page" + ("" if metadata.pages == 1 else "s"))
    if metadata.duration_seconds:
        parts.append(_duration(metadata.duration_seconds))
    return ", ".join(parts) + "."


def canonical_frontmatter(
    *,
    title: str,
    description: str,
    resource: str,
    facts: SourceFacts,
    generated_at: datetime,
    tags: list[str] | None = None,
    extensions: dict[str, Any] | None = None,
) -> Frontmatter:
    """The frontmatter of a Canonical Document (docs/architecture.md, "OKF rules")."""
    other = {key: value for key, value in (extensions or {}).items() if key != SOURCE_METADATA_KEY}
    return Frontmatter(
        type=PageType.SOURCE.value,
        title=title,
        description=description,
        resource=resource,
        tags=tags or [],
        generated=Stamp(by=CONVERSION_STAMP, at=generated_at),
        extensions={SOURCE_METADATA_KEY: facts.model_dump(exclude_none=True), **other},
    )


def page_event(page: Page) -> dict[str, Any]:
    """``page_written`` event data: ``path``, ``title``, ``type``, and ``links``."""
    data = page_ref(page)
    links: dict[str, None] = {}
    for href in extract_links(page.body):
        target = resolve_link(page.path, href)
        if target is not None and target.parts and target != page.path:
            links.setdefault(target.as_posix(), None)
    data["links"] = list(links)
    return data


def page_ref(page: Page) -> dict[str, Any]:
    """``page_deleted`` event data: ``path``, ``title``, and ``type``."""
    frontmatter = page.frontmatter
    title = " ".join(((frontmatter.title if frontmatter else None) or "").split())
    return {
        "path": page.path.as_posix(),
        "title": title or page.path.stem,
        "type": frontmatter.type if frontmatter else "",
    }


def _source_file(canonical_path: PurePosixPath, frontmatter: Frontmatter | None) -> str | None:
    if frontmatter is None or not frontmatter.resource:
        return None
    target = resolve_link(canonical_path, frontmatter.resource)
    if target is None or target.parent != canonical_path.parent:
        return None
    return target.as_posix()


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str):
        try:
            parsed = parse_timestamp(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def _duration(seconds: float) -> str:
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"
