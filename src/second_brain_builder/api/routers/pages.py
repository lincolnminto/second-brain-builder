"""Pages of a Brain clone: list, read, raw files, the link graph, and search.

Paths are from the Brain root, with or without a leading ``/``. ``services`` rejects
every path that leaves the clone or goes through a dot-path such as ``.git``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from second_brain_builder import services
from second_brain_builder.api.files import FILE_RESPONSES, file_response
from second_brain_builder.api.schemas import GraphView, PageSummaryView, PageView, SearchHitView
from second_brain_builder.api.state import Ctx

router = APIRouter(prefix="/brains/{brain_id}", tags=["pages"])

TypeFilter = Annotated[
    list[str] | None,
    Query(alias="type", description="Keep only pages of these types, for example 'entity'."),
]


FolderFilter = Annotated[
    str | None,
    Query(
        description=(
            "Keep only the Sources in this Folder (with its subfolders) and the pages "
            "that cite them. '' is the root: no filter."
        )
    ),
]


@router.get("/pages")
def list_pages(
    brain_id: str, ctx: Ctx, types: TypeFilter = None, folder: FolderFilter = None
) -> list[PageSummaryView]:
    """The OKF pages of the Brain (Canonical Documents and Wiki Pages), sorted by path."""
    return services.list_pages(ctx, brain_id, types, folder)


@router.get("/pages/{path:path}")
def read_page(brain_id: str, path: str, ctx: Ctx) -> PageView:
    """One Markdown file of the Brain. ``body`` is untrusted: render it without raw HTML."""
    return services.read_page(ctx, brain_id, path)


@router.get("/files/{path:path}", response_class=FileResponse, responses=FILE_RESPONSES)
def get_file(brain_id: str, path: str, ctx: Ctx) -> FileResponse:
    """Any file of the Brain, such as an Asset that a Canonical Document shows."""
    return file_response(services.raw_file(ctx, brain_id, path))


@router.get("/graph")
def get_graph(brain_id: str, ctx: Ctx, folder: FolderFilter = None) -> GraphView:
    """The pages of the Brain and the links between them."""
    return services.get_graph(ctx, brain_id, folder)


@router.get("/search")
def search(
    brain_id: str,
    ctx: Ctx,
    q: Annotated[str, Query(min_length=1, description="The search text.")],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    types: TypeFilter = None,
) -> list[SearchHitView]:
    """Full-text search over the pages of the Brain, best hits first."""
    return services.search(ctx, brain_id, q, limit, types)
