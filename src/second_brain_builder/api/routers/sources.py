"""Sources and Folders: list, upload, get, download, move, delete, reconvert, ingest."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from second_brain_builder import services
from second_brain_builder.api.files import FILE_RESPONSES, file_response
from second_brain_builder.api.schemas import (
    AddSourceResult,
    FolderNode,
    JobView,
    SourceDetail,
    SourceMove,
    SourceView,
)
from second_brain_builder.api.state import Ctx
from second_brain_builder.api.uploads import UPLOAD_REQUEST_BODY, receive_upload

router = APIRouter(prefix="/brains/{brain_id}", tags=["sources"])


@router.get("/folders")
def list_folders(brain_id: str, ctx: Ctx) -> FolderNode:
    """The Folder tree of the Brain. The root node is ``sources/``."""
    return services.get_folder_tree(ctx, brain_id)


@router.get("/sources")
def list_sources(
    brain_id: str,
    ctx: Ctx,
    folder: str | None = None,
    recursive: bool = True,
) -> list[SourceView]:
    """The Sources of the Brain, or of one Folder (with its subfolders when ``recursive``)."""
    return services.list_sources(ctx, brain_id, folder, recursive=recursive)


@router.post("/sources", status_code=202, openapi_extra=UPLOAD_REQUEST_BODY)
async def add_source(
    brain_id: str,
    request: Request,
    ctx: Ctx,
    folder: Annotated[str, Query(description="Target Folder; '' is the root.")] = "",
    confirm_over_quota: Annotated[
        bool, Query(description="Add the file even if it takes Git LFS over the quota.")
    ] = False,
) -> AddSourceResult:
    """Upload a Source as ``multipart/form-data`` (part ``file``) and enqueue ``source.add``.

    The body is streamed to disk and never held in memory. A file with the content of
    a Source of the Brain gives 409 ``duplicate_source`` with ``existing_source``.
    """
    return await receive_upload(
        request, ctx, brain_id, folder, confirm_over_quota=confirm_over_quota
    )


@router.get("/sources/{slug}")
def get_source(brain_id: str, slug: str, ctx: Ctx) -> SourceDetail:
    """A Source and the Wiki Pages that cite it."""
    return services.get_source(ctx, brain_id, slug)


@router.delete("/sources/{slug}", status_code=202)
def delete_source(brain_id: str, slug: str, ctx: Ctx) -> JobView:
    """Enqueue ``source.delete``: the Source, its Canonical Document, and its citations go."""
    return services.delete_source(ctx, brain_id, slug)


@router.get("/sources/{slug}/original", response_class=FileResponse, responses=FILE_RESPONSES)
def download_original(brain_id: str, slug: str, ctx: Ctx) -> FileResponse:
    """The Source file, with its original filename."""
    return file_response(services.original_file(ctx, brain_id, slug))


@router.post("/sources/{slug}/move", status_code=202)
def move_source(brain_id: str, slug: str, body: SourceMove, ctx: Ctx) -> JobView:
    """Enqueue ``source.move`` to another Folder. The slug stays."""
    return services.move_source(ctx, brain_id, slug, body.folder)


@router.post("/sources/{slug}/reconvert", status_code=202)
def reconvert_source(brain_id: str, slug: str, ctx: Ctx) -> JobView:
    """Enqueue ``source.reconvert``."""
    return services.reconvert_source(ctx, brain_id, slug)


@router.post("/sources/{slug}/ingest", status_code=202)
def ingest_source(brain_id: str, slug: str, ctx: Ctx) -> JobView:
    """Enqueue ``source.ingest``. Needs an ingest Provider and model."""
    return services.ingest_source(ctx, brain_id, slug)
