"""Folders: the tree (empty Folders included), create, rename, and delete.

Every change is a job, so the change routes answer 202 with the job.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from second_brain_builder import services
from second_brain_builder.api.schemas import FolderCreate, FolderNode, FolderRename, JobView
from second_brain_builder.api.state import Ctx

router = APIRouter(prefix="/brains/{brain_id}", tags=["sources"])


@router.get("/folders")
def list_folders(brain_id: str, ctx: Ctx) -> FolderNode:
    """The Folder tree of the Brain, empty Folders included. The root node is ``sources/``."""
    return services.get_folder_tree(ctx, brain_id)


@router.post("/folders", status_code=202)
def create_folder(brain_id: str, body: FolderCreate, ctx: Ctx) -> JobView:
    """Enqueue ``folder.create``: an empty Folder. Each part of the name becomes a slug."""
    return services.create_folder(ctx, brain_id, body.folder)


@router.post("/folders/rename", status_code=202)
def rename_folder(brain_id: str, body: FolderRename, ctx: Ctx) -> JobView:
    """Enqueue ``folder.rename``: move the Folder and rewrite every link to its Sources."""
    return services.rename_folder(ctx, brain_id, body.folder, body.new_folder)


@router.delete("/folders", status_code=202)
def delete_folder(
    brain_id: str,
    ctx: Ctx,
    folder: Annotated[str, Query(description="The Folder to delete, for example 'work/old'.")],
) -> JobView:
    """Enqueue ``folder.delete``. Only a Folder without Sources can go."""
    return services.delete_folder(ctx, brain_id, folder)
