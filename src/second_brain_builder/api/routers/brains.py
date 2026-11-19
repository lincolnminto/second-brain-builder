"""Brains: list, create, get, disconnect, sync, and Git LFS usage."""

from __future__ import annotations

from fastapi import APIRouter

from second_brain_builder import services
from second_brain_builder.api.schemas import (
    BrainCreate,
    BrainCreated,
    BrainView,
    JobView,
    LfsUsageView,
)
from second_brain_builder.api.state import Ctx

router = APIRouter(prefix="/brains", tags=["brains"])


@router.get("")
def list_brains(ctx: Ctx) -> list[BrainView]:
    """Every Brain, sorted by name."""
    return services.list_brains(ctx)


@router.post("", status_code=201)
def create_brain(body: BrainCreate, ctx: Ctx) -> BrainCreated:
    """Add a Brain in a new private repository or an existing one, and enqueue ``brain.create``."""
    return services.create_brain(ctx, body)


@router.get("/{brain_id}")
def get_brain(brain_id: str, ctx: Ctx) -> BrainView:
    return services.get_brain(ctx, brain_id)


@router.delete("/{brain_id}", status_code=204)
def disconnect_brain(brain_id: str, ctx: Ctx) -> None:
    """Remove the Brain from this app. The GitHub repository is never deleted."""
    services.disconnect_brain(ctx, brain_id)


@router.post("/{brain_id}/sync", status_code=202)
def sync_brain(brain_id: str, ctx: Ctx) -> JobView:
    """Enqueue ``brain.sync``: pull, then rebuild the Source list and the search index."""
    return services.sync_brain(ctx, brain_id)


@router.get("/{brain_id}/lfs-usage")
def get_lfs_usage(brain_id: str, ctx: Ctx) -> LfsUsageView:
    """Git LFS storage of the Brain and of all Brains of the same repository owner."""
    return services.get_lfs_usage(ctx, brain_id)
