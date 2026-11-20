"""Folders of a Brain clone: empty Folders, and the Folder filter of pages and the graph.

A Folder is a slug path under ``sources/``, such as ``work`` or ``work/clients``.
A Folder with Sources exists through their directories. An empty Folder holds a
``.gitkeep`` file (``FOLDER_MARKER``), because git does not keep empty
directories; OKF ignores it, like every dot-path.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from second_brain_builder.brains.layout import ASSETS_DIR_NAME, SOURCES_DIR
from second_brain_builder.brains.slugs import is_slug
from second_brain_builder.okf import Page, PageType

_log = logging.getLogger(__name__)

FOLDER_MARKER = ".gitkeep"
"""The file that keeps an empty Folder in the Brain repo: ``sources/<folder>/.gitkeep``."""


def kept_folders(root: Path) -> list[PurePosixPath]:
    """The Folders that hold ``FOLDER_MARKER``, sorted, as paths from ``sources/``.

    Dot-paths, symbolic links, ``assets`` directories, Source directories (a
    directory ``x`` that holds ``x.md``), and directories whose name is not a slug
    are skipped, with everything below them.
    """
    found: list[PurePosixPath] = []
    start = root / SOURCES_DIR
    if start.is_symlink() or not start.is_dir():
        return found
    pending = [start]
    while pending:
        directory = pending.pop()
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            _log.warning("Skipped the directory %s: %s", directory, exc)
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
                continue
            if entry.name == ASSETS_DIR_NAME or not is_slug(entry.name):
                continue
            if (entry / f"{entry.name}.md").is_file():
                continue  # a Source directory: it never holds Folders
            pending.append(entry)
            marker = entry / FOLDER_MARKER
            if marker.is_file() and not marker.is_symlink():
                found.append(PurePosixPath(entry.relative_to(start).as_posix()))
    return sorted(found)


def source_folder(path: PurePosixPath) -> PurePosixPath | None:
    """The Folder of a Canonical Document path, ``sources/<folder>/<slug>/<slug>.md``.

    ``PurePosixPath()`` is the root of ``sources/``. ``None`` when the path does not
    have the form of a Canonical Document.
    """
    parts = path.parts
    if len(parts) < 3 or parts[0] != SOURCES_DIR.name or path.suffix != ".md":
        return None
    if path.parent.name != path.stem:
        return None
    return path.parent.parent.relative_to(SOURCES_DIR)


def in_folder(folder: PurePosixPath, wanted: PurePosixPath) -> bool:
    """True when ``folder`` is ``wanted`` or one of its subfolders."""
    return folder == wanted or wanted in folder.parents


def folder_filter(pages: Iterable[Page], folder: PurePosixPath) -> set[PurePosixPath]:
    """The page paths that the Folder filter keeps.

    These are the Canonical Documents of the Sources in ``folder`` and its
    subfolders, and the other pages that cite at least one of these Sources in
    their ``sources`` (Wiki Pages, Name Index pages of those Sources included).
    """
    pages = list(pages)
    kept: set[PurePosixPath] = set()
    source_ids: set[str] = set()
    for page in pages:
        frontmatter = page.frontmatter
        if frontmatter is None or frontmatter.type != PageType.SOURCE.value:
            continue
        page_folder = source_folder(page.path)
        if page_folder is not None and in_folder(page_folder, folder):
            kept.add(page.path)
            source_ids.add(page.path.stem)
    if not source_ids:
        return kept
    for page in pages:
        frontmatter = page.frontmatter
        if frontmatter is None or frontmatter.type == PageType.SOURCE.value:
            continue
        if any(ref.id in source_ids for ref in frontmatter.sources):
            kept.add(page.path)
    return kept
