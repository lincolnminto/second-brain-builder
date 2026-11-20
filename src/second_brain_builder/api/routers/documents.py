"""Large Markdown pages in segments: the outline and the segments of one page.

The document viewer uses these routes instead of ``GET /pages/{path}`` for a large
page, such as a Canonical Document of several megabytes. Offsets are indexes into
the page body after the frontmatter, in Unicode code points.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from second_brain_builder import services
from second_brain_builder.api.schemas import DocumentOutline, DocumentSegments
from second_brain_builder.api.state import Ctx

router = APIRouter(prefix="/brains/{brain_id}/documents", tags=["documents"])

PagePath = Annotated[
    str,
    Query(
        min_length=1,
        description="The page path from the Brain root, for example 'sources/books/dune/dune.md'.",
    ),
]


@router.get("/outline")
def get_document_outline(brain_id: str, ctx: Ctx, path: PagePath) -> DocumentOutline:
    """The length, segment count, and headings of a Markdown page."""
    return services.get_document_outline(ctx, brain_id, path)


@router.get("/segments")
def get_document_segments(
    brain_id: str,
    ctx: Ctx,
    path: PagePath,
    start: Annotated[
        int, Query(alias="from", ge=0, description="The index of the first segment.")
    ] = 0,
    count: Annotated[
        int,
        Query(ge=1, le=services.MAX_SEGMENT_COUNT, description="How many segments, at most."),
    ] = services.MAX_SEGMENT_COUNT,
) -> DocumentSegments:
    """Consecutive segments of a Markdown page.

    ``markdown`` is untrusted: render it without raw HTML.
    """
    return services.get_document_segments(ctx, brain_id, path, start, count)
