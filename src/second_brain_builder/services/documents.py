"""Large Markdown pages in segments: the outline and the segments of one page.

The document viewer reads a large Canonical Document (or any page) in segments of
``search.SEGMENT_CHARS`` characters instead of the whole body. Offsets are indexes
into the page body after the frontmatter (``PageView.body``), in Unicode code
points.

Every path goes through ``brains.resolve_in_brain`` (in ``services.pages.read_page``).
The segmentation of recently read pages stays in memory, keyed by the file's
identity, size, and modification time, so scrolling does not split the body again.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, ConfigDict

from second_brain_builder.brains import BrainPathError, resolve_in_brain
from second_brain_builder.search import Segmentation, segment_markdown
from second_brain_builder.services.brains import clone_root
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import InvalidInputError
from second_brain_builder.services.pages import read_page

MAX_SEGMENT_COUNT = 20
"""The most segments that one ``get_document_segments`` call returns."""

_CACHE_SIZE = 8
_CACHE_CHARS = 32_000_000
"""The most body characters that the cache keeps, over all its pages."""


class DocumentHeading(BaseModel):
    """One heading of a page. ``text`` is plain text: show it as text."""

    model_config = ConfigDict(frozen=True)

    level: int
    text: str
    segment: int
    """The segment that starts with this heading."""
    char_start: int


class DocumentOutline(BaseModel):
    """The size and the headings of a page, to show a table of contents."""

    model_config = ConfigDict(frozen=True)

    path: str
    title: str
    total_chars: int
    """The length of the body (after the frontmatter)."""
    segments: int
    """How many segments the body has. An empty body has none."""
    headings: list[DocumentHeading]


class DocumentSegment(BaseModel):
    """One segment of a page body: ``body[char_start:char_end]``.

    ``markdown`` is untrusted: render it without raw HTML.
    """

    model_config = ConfigDict(frozen=True)

    index: int
    char_start: int
    char_end: int
    heading_trail: list[str]
    """The headings that the segment is under, outermost first."""
    markdown: str


class DocumentSegments(BaseModel):
    """Some consecutive segments of a page body. ``total`` counts all of them."""

    model_config = ConfigDict(frozen=True)

    path: str
    total: int
    segments: list[DocumentSegment]


@dataclass(frozen=True)
class _SegmentedPage:
    path: str
    title: str
    body: str
    segmentation: Segmentation


_FileKey = tuple[str, str, int, int, int, int]
_cache: OrderedDict[_FileKey, _SegmentedPage] = OrderedDict()
_cache_lock = Lock()


def get_document_outline(ctx: AppContext, brain_id: str, path: str) -> DocumentOutline:
    """The outline of a Markdown page: its length, segment count, and headings.

    Works for any page: a Canonical Document, a Wiki Page, ``index.md``.

    Raises:
        NotFoundError: No such Brain or page.
        InvalidInputError: The path leaves the Brain, has a dot-path or ``..``, is
            not a ``.md`` file, or the page's frontmatter cannot be read.
    """
    page = _segmented_page(ctx, brain_id, path)
    segmentation = page.segmentation
    return DocumentOutline(
        path=page.path,
        title=page.title,
        total_chars=len(page.body),
        segments=len(segmentation.segments),
        headings=[
            DocumentHeading(
                level=heading.level,
                text=heading.text,
                segment=heading.segment,
                char_start=heading.char_start,
            )
            for heading in segmentation.headings
        ],
    )


def get_document_segments(
    ctx: AppContext,
    brain_id: str,
    path: str,
    start: int = 0,
    count: int = MAX_SEGMENT_COUNT,
) -> DocumentSegments:
    """Segments ``start`` to ``start + count - 1`` of a Markdown page, in order.

    A range past the last segment gives fewer segments, or none.

    Raises:
        NotFoundError: No such Brain or page.
        InvalidInputError: ``start`` is negative, ``count`` is not between 1 and
            ``MAX_SEGMENT_COUNT``, or the path is not valid (see
            ``get_document_outline``).
    """
    if start < 0:
        raise InvalidInputError("The first segment must be 0 or more.")
    if not 1 <= count <= MAX_SEGMENT_COUNT:
        raise InvalidInputError(f"The segment count must be between 1 and {MAX_SEGMENT_COUNT}.")
    page = _segmented_page(ctx, brain_id, path)
    segments = page.segmentation.segments
    return DocumentSegments(
        path=page.path,
        total=len(segments),
        segments=[
            DocumentSegment(
                index=segment.index,
                char_start=segment.char_start,
                char_end=segment.char_end,
                heading_trail=list(segment.heading_trail),
                markdown=page.body[segment.char_start : segment.char_end],
            )
            for segment in segments[start : start + count]
        ],
    )


def _segmented_page(ctx: AppContext, brain_id: str, path: str) -> _SegmentedPage:
    """The page and its segmentation, from the cache when the file did not change."""
    root = clone_root(ctx, brain_id).resolve()
    try:
        target = resolve_in_brain(root, path)
    except BrainPathError as exc:
        raise InvalidInputError(str(exc)) from None
    key = _file_key(path, target)
    if key is not None:
        with _cache_lock:
            cached = _cache.get(key)
            if cached is not None:
                _cache.move_to_end(key)
                return cached
    view = read_page(ctx, brain_id, path)
    page = _SegmentedPage(
        path=view.path,
        title=view.title,
        body=view.body,
        segmentation=segment_markdown(view.body),
    )
    # A worker can replace the file while it is read: keep the result only when
    # the file is the same before and after.
    if key is not None and _file_key(path, target) == key:
        with _cache_lock:
            _cache[key] = page
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_SIZE or (
                len(_cache) > 1 and sum(len(item.body) for item in _cache.values()) > _CACHE_CHARS
            ):
                _cache.popitem(last=False)
    return page


def _file_key(path: str, target: Path) -> _FileKey | None:
    """The identity of a page's content: the requested path, and the file's device,
    inode, size, and modification time.

    The requested path is part of the key, so each path passes the checks of
    ``read_page`` before it can read a cache entry.
    """
    try:
        status = target.stat()
    except OSError:
        return None
    return (path, str(target), status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)
