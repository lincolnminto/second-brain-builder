"""Health and setup: ``GET /health``, ``GET /setup``."""

from __future__ import annotations

from fastapi import APIRouter, Response

from second_brain_builder import services
from second_brain_builder.api.schemas import HealthStatus, SetupStatus
from second_brain_builder.api.state import Ctx

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    responses={503: {"model": HealthStatus, "description": "The database does not answer."}},
)
def get_health(ctx: Ctx, response: Response) -> HealthStatus:
    """Whether the database answers (HTTP 503 if not), and whether the worker is online."""
    health = services.check_health(ctx)
    if not health.ok:
        response.status_code = 503
    return health


@router.get("/setup")
def get_setup(ctx: Ctx) -> SetupStatus:
    """What the onboarding screens need: GitHub, Brain and Provider counts, the worker."""
    return services.get_setup_status(ctx)
