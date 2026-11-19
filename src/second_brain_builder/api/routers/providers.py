"""LLM Providers and the task settings (which Provider and model run Ingest and transcription)."""

from __future__ import annotations

from fastapi import APIRouter

from second_brain_builder import services
from second_brain_builder.api.schemas import (
    ProviderInput,
    ProviderTestResult,
    ProviderUpdate,
    ProviderView,
    TaskSettingsInput,
    TaskSettingsView,
)
from second_brain_builder.api.state import Ctx

router = APIRouter(tags=["providers"])


@router.get("/providers")
def list_providers(ctx: Ctx) -> list[ProviderView]:
    """Every Provider, sorted by name. API keys are masked."""
    return services.list_providers(ctx)


@router.post("/providers", status_code=201)
def create_provider(body: ProviderInput, ctx: Ctx) -> ProviderView:
    """Add a Provider. The API key is stored encrypted."""
    return services.create_provider(ctx, body)


@router.patch("/providers/{provider_id}")
def update_provider(provider_id: int, body: ProviderUpdate, ctx: Ctx) -> ProviderView:
    """Change a Provider. Fields that are ``null`` stay; an empty ``api_key`` removes the key."""
    return services.update_provider(ctx, provider_id, body)


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: int, ctx: Ctx) -> None:
    """Delete a Provider. Task settings that use it go back to their defaults."""
    services.delete_provider(ctx, provider_id)


@router.post("/providers/{provider_id}/test")
def test_provider(provider_id: int, ctx: Ctx) -> ProviderTestResult:
    """Test a Provider with one call that lists its models. A failure is ``ok: false``."""
    return services.check_provider(ctx, provider_id)


@router.get("/providers/{provider_id}/models")
def list_provider_models(provider_id: int, ctx: Ctx) -> list[str]:
    """The model ids that the Provider offers."""
    return services.list_provider_models(ctx, provider_id)


@router.get("/settings/tasks")
def get_task_settings(ctx: Ctx) -> TaskSettingsView:
    """Where Ingest and transcription run."""
    return services.get_task_settings(ctx)


@router.put("/settings/tasks")
def put_task_settings(body: TaskSettingsInput, ctx: Ctx) -> TaskSettingsView:
    """Replace the task settings. ``null`` resets a task to its default."""
    return services.put_task_settings(ctx, body)
