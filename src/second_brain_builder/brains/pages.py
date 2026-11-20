"""Read the OKF pages of a Brain clone, and build the link graph between them."""

from __future__ import annotations

import contextlib
import errno
import logging
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TextIO

from second_brain_builder.brains.folders import folder_filter
from second_brain_builder.brains.layout import (
    ASSETS_DIR_NAME,
    SOURCES_DIR,
)
from second_brain_builder.brains.models import Graph, GraphEdge, GraphNode, PageSummary
from second_brain_builder.okf import (
    OKFError,
    Page,
    extract_links,
    is_concept,
    parse_page,
    read_page,
    resolve_link,
)
from second_brain_builder.okf.pages import bundle_file, normalize_text

_log = logging.getLogger(__name__)


def read_pages(root: Path) -> list[Page]:
    """Read every OKF page (concept) of the Brain at ``root``, sorted by path.

    A page is a ``.md`` file with frontmatter that has a ``type``, outside the
    reserved names ``index.md`` and ``log.md``. These files are never pages:
    dot-paths (``.git``, ``.brain``), symbolic links, files in the ``assets/``
    directory of a Source, and files and directories whose name is not UTF-8 (no
    bundle path can name them). A Markdown Source is ``<slug>.original.markdown``
    (see ``MARKDOWN_SOURCE_SUFFIX``), so it is never a ``.md`` file.

    A file is skipped with a warning when its frontmatter cannot be read
    (``okf.validate_bundle`` reports it), and also when it or its directory is
    removed or replaced while the pages are read: the worker changes a clone
    while the app reads it.

    Raises:
        FileNotFoundError: ``root`` does not exist.
    """
    return _read_pages(root, graph=False)


def _read_pages(root: Path, *, graph: bool) -> list[Page]:
    if graph:
        return [entry.page for entry in _graph_pages(root)]
    pages: list[Page] = []
    for path in _markdown_files(root):
        try:
            page = read_page(root, path)
        except (OKFError, OSError, ValueError) as exc:
            # OSError, or the ValueError of a path that now goes through a
            # symbolic link: the file changed after _markdown_files listed it.
            _log.warning("Skipped %s in %s: %s", path, root, exc)
            continue
        if is_concept(page):
            pages.append(page)
    return pages


_Signature = tuple[int, int, int, int]
"""The mtime, ctime (both in nanoseconds), size, and inode of a file."""


@dataclass(frozen=True)
class _GraphPage:
    """A page as the graph and the page list need it."""

    page: Page
    """The path and the frontmatter. The body is not kept: it is always empty."""
    links: tuple[str, ...]
    """The link targets in the body (``extract_links``); none for a Canonical Document."""


_graph_cache: dict[Path, dict[PurePosixPath, tuple[_Signature, _GraphPage]]] = {}
"""The pages of each clone as ``_graph_pages`` last read them, with their file signatures.

The YAML parser needs milliseconds for one frontmatter, so a Brain with a thousand
pages took seconds per page list or graph. Only the worker changes a clone, a few
files at a time, so each read parses only the files whose signature changed.
"""


def _graph_pages(root: Path) -> list[_GraphPage]:
    """The pages that ``read_pages`` reads, with the frontmatter only of Canonical Documents."""
    previous = _graph_cache.get(root, {})
    current: dict[PurePosixPath, tuple[_Signature, _GraphPage]] = {}
    pages: list[_GraphPage] = []
    for path in _markdown_files(root):
        try:
            signature, entry = _read_graph_page(root, path, previous.get(path))
        except (OKFError, OSError, ValueError) as exc:
            # OSError, or the ValueError of a path that now goes through a
            # symbolic link: the file changed after _markdown_files listed it.
            _log.warning("Skipped %s in %s: %s", path, root, exc)
            continue
        current[path] = (signature, entry)
        if is_concept(entry.page):
            pages.append(entry)
    _graph_cache[root] = current
    return pages


def list_pages(root: Path, folder: PurePosixPath | None = None) -> list[PageSummary]:
    """A summary of every page that ``read_pages`` reads, sorted by path.

    With ``folder`` (a path from ``sources/``), only the pages that
    ``folder_filter`` keeps: the Sources in that Folder and its subfolders, and
    the pages that cite them.
    """
    pages = _read_pages(root, graph=True)
    if folder is None:
        return [_summary(page) for page in pages]
    kept = folder_filter(pages, folder)
    return [_summary(page) for page in pages if page.path in kept]


def build_graph(root: Path, folder: PurePosixPath | None = None) -> Graph:
    """The pages of the Brain at ``root`` and the links between them.

    With ``folder`` (a path from ``sources/``), the nodes are only the pages that
    ``folder_filter`` keeps, and the edges only the links between them.

    Nodes are the pages that ``read_pages`` reads. There is one edge per pair of
    pages where the body of the first links to the second, in any form that
    ``okf.extract_links`` finds: inline links, reference links, and the links in
    citation footnotes. Links to the page itself, to files that are not pages
    (Assets, Source files), to missing files, and to external URLs are left out.

    Canonical Documents (``type: source``) contribute nodes only: Conversion
    unwraps their non-asset links, so only their frontmatter is read.
    """
    entries = _graph_pages(root)
    if folder is not None:
        kept = folder_filter((entry.page for entry in entries), folder)
        entries = [entry for entry in entries if entry.page.path in kept]
    paths = {entry.page.path for entry in entries}
    nodes: list[GraphNode] = []
    edges: set[tuple[PurePosixPath, PurePosixPath]] = set()
    for entry in entries:
        page = entry.page
        summary = _summary(page)
        nodes.append(GraphNode(path=summary.path, type=summary.type, title=summary.title))
        for href in entry.links:
            target = resolve_link(page.path, href)
            if target is not None and target != page.path and target in paths:
                edges.add((page.path, target))
    return Graph(
        nodes=nodes,
        edges=[GraphEdge(from_path=start, to_path=end) for start, end in sorted(edges)],
    )


def _read_graph_page(
    root: Path, path: PurePosixPath, cached: tuple[_Signature, _GraphPage] | None
) -> tuple[_Signature, _GraphPage]:
    """Read a page with the same no-symlink traversal as OKF reads.

    ``cached`` is returned when the file still has its signature. A Canonical
    Document is read up to the end of its frontmatter. The frontmatter is parsed
    once, and the links of the body are extracted once.
    """
    bundle_file(root, path)
    with contextlib.ExitStack() as stack:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, parent)
        for index, part in enumerate(path.parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if index < len(path.parts) - 1:
                flags |= os.O_DIRECTORY
            try:
                descriptor = os.open(part, flags, dir_fd=parent)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError(
                        f"path goes through a symbolic link or non-directory: {path}"
                    ) from exc
                raise
            stack.callback(os.close, descriptor)
            parent = descriptor
        status = os.fstat(descriptor)
        signature = (status.st_mtime_ns, status.st_ctime_ns, status.st_size, status.st_ino)
        if cached is not None and cached[0] == signature:
            return cached
        try:
            with os.fdopen(os.dup(descriptor), encoding="utf-8-sig") as file:
                page, body = _parse_graph_page(path, file)
        except UnicodeDecodeError as exc:
            raise OKFError(f"{path} is not UTF-8 text") from exc
    links = tuple(extract_links(body)) if is_concept(page) else ()
    return signature, _GraphPage(page=page.model_copy(update={"body": ""}), links=links)


def _parse_graph_page(path: PurePosixPath, file: TextIO) -> tuple[Page, str]:
    """The page in ``file`` and the body to take links from (empty for a Canonical Document)."""
    first = file.readline()
    if first.rstrip("\n") != "---":
        page = parse_page(path, first + file.read())
        return page, page.body
    header = [first]
    for line in file:
        header.append(line)
        if line.rstrip("\n") == "---":
            page = parse_page(path, "".join(header))
            if page.frontmatter is None or page.frontmatter.type == "source":
                return page, ""
            # The same body that parse_page gives for the whole file.
            return page, normalize_text(file.read()).lstrip("\n")
    return parse_page(path, "".join(header)), ""


def _summary(page: Page) -> PageSummary:
    frontmatter = page.frontmatter
    if frontmatter is None:  # read_pages keeps only pages with frontmatter
        raise ValueError(f"{page.path} has no frontmatter")
    return PageSummary(
        path=page.path,
        type=frontmatter.type,
        title=" ".join((frontmatter.title or "").split()) or page.path.stem,
        description=frontmatter.description,
    )


def _markdown_files(root: Path) -> list[PurePosixPath]:
    """Paths of the ``.md`` files that can be pages, sorted."""
    found: list[PurePosixPath] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            if directory == root:
                raise
            _log.warning("Skipped the directory %s in %s: %s", directory.name, root, exc)
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if not _is_utf8(entry.name):
                # Its path cannot go into JSON, index.md, or an OKF link.
                _log.warning("Skipped %r in %s: the name is not UTF-8", entry.name, directory)
                continue
            path = PurePosixPath(entry.relative_to(root).as_posix())
            in_sources = path.parts[0] == SOURCES_DIR.name
            if entry.is_dir():
                if not (in_sources and entry.name == ASSETS_DIR_NAME):
                    pending.append(entry)
            elif entry.suffix == ".md" and entry.is_file():
                found.append(path)
    return sorted(found)


def _is_utf8(name: str) -> bool:
    """False for a name with bytes that are not UTF-8 (``os.fsdecode`` escapes them)."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
