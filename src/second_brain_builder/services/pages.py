"""Pages of a Brain clone: list, read, raw files, the link graph, and search.

Every path from a request or an MCP tool goes through ``brains.resolve_in_brain``,
so no request can read outside the clone or a dot-path such as ``.git``.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path, PurePosixPath
from threading import Lock

from second_brain_builder.brains import (
    BrainPathError,
    GitError,
    GitRepo,
    build_graph,
    resolve_in_brain,
)
from second_brain_builder.brains import list_pages as list_brain_pages
from second_brain_builder.okf import OKFError, Page
from second_brain_builder.okf import read_page as read_okf_page
from second_brain_builder.services._files import guess_media_type, local_file
from second_brain_builder.services.brains import READ_ONLY_IDENTITY, clone_root
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import InvalidInputError, NotFoundError
from second_brain_builder.services.folders import filter_folder
from second_brain_builder.services.models import (
    GraphEdgeView,
    GraphNodeView,
    GraphView,
    LocalFile,
    PageSummaryView,
    PageView,
    SearchHitView,
    SourceRefView,
)

DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100

_GRAPH_CACHE_SIZE = 16
_graph_cache: OrderedDict[tuple[Path, str], tuple[str, GraphView]] = OrderedDict()
_graph_cache_lock = Lock()


def list_pages(
    ctx: AppContext, brain_id: str, types: list[str] | None = None, folder: str | None = None
) -> list[PageSummaryView]:
    """The OKF pages of a Brain (Canonical Documents and Wiki Pages), sorted by path.

    ``types`` keeps only pages of those types, for example ``["entity"]``.
    ``folder`` keeps only the Sources in that Folder and its subfolders, and the
    pages that cite at least one of them (``brains.folder_filter``). ``None`` and
    the root (``""``) keep every page.

    Raises:
        NotFoundError: No such Brain.
        InvalidInputError: The Folder is not valid.
    """
    in_folder = filter_folder(ctx, brain_id, folder)
    root = clone_root(ctx, brain_id)
    if not root.is_dir():
        return []
    wanted = None if types is None else set(types)
    return [
        PageSummaryView(
            path=summary.path.as_posix(),
            type=summary.type,
            title=summary.title,
            description=summary.description,
        )
        for summary in list_brain_pages(root, in_folder)
        if wanted is None or summary.type in wanted
    ]


def read_page(ctx: AppContext, brain_id: str, path: str) -> PageView:
    """One Markdown file of a Brain, such as ``wiki/entities/x.md`` or ``/index.md``.

    Raises:
        NotFoundError: No such Brain or file.
        InvalidInputError: The path leaves the Brain, has a dot-path or ``..``, is
            not a ``.md`` file, or the file's frontmatter cannot be read.
    """
    root = clone_root(ctx, brain_id).resolve()
    relative = _relative(root, path)
    if relative.suffix != ".md":
        raise InvalidInputError(f"{relative} is not a Markdown page.")
    try:
        page = read_okf_page(root, relative)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        raise NotFoundError(f"The page {relative} does not exist.") from None
    except OKFError as exc:
        raise InvalidInputError(f"The page {relative} cannot be read: {exc}") from None
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from None
    return _page_view(page)


def raw_file(ctx: AppContext, brain_id: str, path: str) -> LocalFile:
    """Any file of a Brain clone, such as an Asset that a Canonical Document shows.

    Check ``LocalFile.inline`` before the API shows it inline: Brain content is
    untrusted, and HTML or SVG must go as an attachment.

    Raises:
        NotFoundError: No such Brain or file, or a Git LFS file that is not downloaded.
        InvalidInputError: The path leaves the Brain or has a dot-path or ``..``.
    """
    root = clone_root(ctx, brain_id).resolve()
    relative = _relative(root, path)
    target = root.joinpath(*relative.parts)
    return local_file(target, target.name, guess_media_type(target))


def get_graph(ctx: AppContext, brain_id: str, folder: str | None = None) -> GraphView:
    """The pages of a Brain and the links between them.

    ``folder`` keeps the nodes that ``list_pages`` keeps for it, and the links
    between them.

    Raises:
        NotFoundError: No such Brain.
        InvalidInputError: The Folder is not valid.
    """
    in_folder = filter_folder(ctx, brain_id, folder)
    root = clone_root(ctx, brain_id)
    if not root.is_dir():
        return GraphView(nodes=[], edges=[])
    root = root.resolve()
    key = (root, "" if in_folder is None else in_folder.as_posix())
    head = _graph_head(root)
    with _graph_cache_lock:
        cached = _graph_cache.get(key)
        if head is not None and cached is not None and cached[0] == head:
            _graph_cache.move_to_end(key)
            return cached[1].model_copy(deep=True)
        _graph_cache.pop(key, None)
    graph = build_graph(root, in_folder)
    view = GraphView(
        nodes=[
            GraphNodeView(path=node.path.as_posix(), type=node.type, title=node.title)
            for node in graph.nodes
        ],
        edges=[
            GraphEdgeView(from_path=edge.from_path.as_posix(), to_path=edge.to_path.as_posix())
            for edge in graph.edges
        ],
    )
    # A worker can advance HEAD while the files are being read. Never retain
    # that potentially mixed graph under either commit.
    if head is not None and _graph_head(root) == head:
        with _graph_cache_lock:
            _graph_cache[key] = (head, view.model_copy(deep=True))
            _graph_cache.move_to_end(key)
            while len(_graph_cache) > _GRAPH_CACHE_SIZE:
                _graph_cache.popitem(last=False)
    return view


def _graph_head(root: Path) -> str | None:
    """Clones without a readable HEAD are served without caching."""
    try:
        return GitRepo(root, READ_ONLY_IDENTITY, None).head_commit()
    except GitError:
        return None


def search(
    ctx: AppContext,
    brain_id: str,
    query: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    types: list[str] | None = None,
) -> list[SearchHitView]:
    """Full-text search over the pages of one Brain, best hits first.

    Raises:
        NotFoundError: No such Brain.
        InvalidInputError: ``limit`` is not between 1 and ``MAX_SEARCH_LIMIT``.
    """
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise InvalidInputError(f"The limit must be between 1 and {MAX_SEARCH_LIMIT}.")
    clone_root(ctx, brain_id)
    hits = ctx.search.query(brain_id, query, limit=limit, types=types)
    return [
        SearchHitView(
            path=hit.path.as_posix(),
            title=hit.title,
            type=hit.type,
            snippet=hit.snippet,
            score=hit.score,
        )
        for hit in hits
    ]


def _relative(root: Path, path: str) -> PurePosixPath:
    """The path from the Brain root, checked by ``resolve_in_brain``."""
    try:
        resolved = resolve_in_brain(root, path)
    except BrainPathError as exc:
        raise InvalidInputError(str(exc)) from None
    return PurePosixPath(resolved.relative_to(root).as_posix())


def _page_view(page: Page) -> PageView:
    frontmatter = page.frontmatter
    if frontmatter is None:
        return PageView(
            path=page.path.as_posix(),
            type="",
            title=page.path.stem,
            description=None,
            tags=[],
            sources=[],
            generated_by=None,
            generated_at=None,
            body=page.body,
        )
    return PageView(
        path=page.path.as_posix(),
        type=frontmatter.type,
        title=" ".join((frontmatter.title or "").split()) or page.path.stem,
        description=frontmatter.description,
        tags=list(frontmatter.tags),
        sources=[
            SourceRefView(id=ref.id, resource=ref.resource, title=ref.title)
            for ref in frontmatter.sources
        ],
        generated_by=None if frontmatter.generated is None else frontmatter.generated.by,
        generated_at=None if frontmatter.generated is None else frontmatter.generated.at,
        body=page.body,
    )
