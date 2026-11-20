"""Where each file of a Brain goes. See docs/architecture.md, "Brain repo layout"."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from types import MappingProxyType

from second_brain_builder.brains.slugs import is_slug
from second_brain_builder.okf import PageType

SOURCES_DIR = PurePosixPath("sources")
WIKI_DIR = PurePosixPath("wiki")
ASSETS_DIR_NAME = "assets"
SETTINGS_PATH = PurePosixPath(".brain/brain.yaml")
INDEX_PATH = PurePosixPath("index.md")
LOG_PATH = PurePosixPath("log.md")

MARKDOWN_SOURCE_SUFFIX = ".original.markdown"
"""The file suffix of a Source that is itself Markdown (``.md``). It does not collide
with the Canonical Document ``<slug>.md``, and because it does not end in ``.md``,
OKF does not read the user's note as a page and the ``sources/**`` LFS rule of
``.gitattributes`` stores it like every other Source."""

WIKI_DIRS: Mapping[PageType, str] = MappingProxyType(
    {
        PageType.SUMMARY: "summaries",
        PageType.ENTITY: "entities",
        PageType.CONCEPT: "concepts",
        PageType.COMPARISON: "comparisons",
        PageType.SYNTHESIS: "syntheses",
        PageType.NAME_INDEX: "name-indexes",
    }
)
"""The directory under ``wiki/`` for each Wiki Page type."""

_EXTENSION_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")
_EXTENSION_MAX_LENGTH = 16


class BrainLayout:
    """Pure path logic for one Brain: every result is a path from the Brain root.

    Folders are slug paths (``books`` or ``books/sci-fi``). The empty path
    (``PurePosixPath()``) puts a Source directly under ``sources/``. Every method
    raises ``ValueError`` for a folder part or slug that is not a slug (see
    ``is_slug``), so a result never leaves the Brain or uses a dot-path.
    """

    def source_dir(self, folder: PurePosixPath, slug: str) -> PurePosixPath:
        """``sources/<folder>/<slug>``: the directory of one Source."""
        return SOURCES_DIR.joinpath(*_folder_parts(folder), _checked_slug(slug))

    def canonical_path(self, folder: PurePosixPath, slug: str) -> PurePosixPath:
        """``sources/<folder>/<slug>/<slug>.md``: the Canonical Document."""
        return self.source_dir(folder, slug) / f"{slug}.md"

    def source_file_path(self, folder: PurePosixPath, slug: str, ext: str) -> PurePosixPath:
        """``sources/<folder>/<slug>/<slug>.<ext>``: the Source file.

        ``ext`` can have a leading dot and capitals (``".PDF"``); the filename uses
        it in lowercase. A Markdown Source (``md``) is ``<slug>.original.markdown``.
        """
        extension = ext.lower().removeprefix(".")
        if len(extension) > _EXTENSION_MAX_LENGTH or not _EXTENSION_RE.fullmatch(extension):
            raise ValueError(f"not a file extension: {ext!r}")
        directory = self.source_dir(folder, slug)
        if extension == "md":
            return directory / f"{slug}{MARKDOWN_SOURCE_SUFFIX}"
        return directory / f"{slug}.{extension}"

    def assets_dir(self, folder: PurePosixPath, slug: str) -> PurePosixPath:
        """``sources/<folder>/<slug>/assets``: images, ``<slug>.vtt``, and other Assets."""
        return self.source_dir(folder, slug) / ASSETS_DIR_NAME

    def wiki_path(self, type: PageType, slug: str) -> PurePosixPath:
        """``wiki/<type-dir>/<slug>.md``, for example ``wiki/entities/ishmael.md``.

        Raises:
            ValueError: ``type`` is ``SOURCE`` (Canonical Documents are not in
                ``wiki/``), or ``slug`` is not a slug.
        """
        directory = WIKI_DIRS.get(type)
        if directory is None:
            raise ValueError(f"{type} pages are not Wiki Pages")
        return WIKI_DIR / directory / f"{_checked_slug(slug)}.md"


def _checked_slug(slug: str) -> str:
    if not is_slug(slug):
        raise ValueError(f"not a slug: {slug!r}")
    return slug


def _folder_parts(folder: PurePosixPath) -> tuple[str, ...]:
    if folder.is_absolute():
        raise ValueError(f"a folder must be a relative slug path: {folder}")
    for part in folder.parts:
        if not is_slug(part):
            raise ValueError(f"folder {folder} has a part that is not a slug: {part!r}")
    return folder.parts
