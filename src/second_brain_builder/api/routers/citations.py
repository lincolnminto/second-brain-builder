"""Citations that point to the passage: the passage that a footnote reference cites."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from second_brain_builder import services
from second_brain_builder.api.schemas import CitationPassage
from second_brain_builder.api.state import Ctx

router = APIRouter(prefix="/brains/{brain_id}/citations", tags=["citations"])


@router.get("/resolve")
def resolve_citation(
    brain_id: str,
    ctx: Ctx,
    page: Annotated[
        str,
        Query(
            min_length=1,
            description="The Wiki Page path from the Brain root, for example 'wiki/entities/x.md'.",
        ),
    ],
    ref: Annotated[
        int,
        Query(
            ge=0,
            description="The footnote reference number in the rendered page, from 0: the "
            "references of the body first, then those in footnote definitions.",
        ),
    ],
) -> CitationPassage:
    """The passage of the cited Canonical Document that a footnote reference points to.

    404 when the page has no such reference or no passage matches. ``text`` is
    untrusted plain text: show it as text.
    """
    return services.resolve_citation(ctx, brain_id, page, ref)
