"""API tokens and the agent setup instructions."""

from __future__ import annotations

from fastapi import APIRouter

from second_brain_builder import services
from second_brain_builder.api.schemas import AgentSetup, ApiTokenView, CreatedApiToken, TokenCreate
from second_brain_builder.api.state import Ctx

router = APIRouter(tags=["agents"])


@router.get("/tokens")
def list_tokens(ctx: Ctx) -> list[ApiTokenView]:
    """Every API token, oldest first, without secrets."""
    return services.list_api_tokens(ctx)


@router.post("/tokens", status_code=201)
def create_token(body: TokenCreate, ctx: Ctx) -> CreatedApiToken:
    """Create an API token. ``secret`` is in this response only."""
    return services.create_api_token(ctx, body.name)


@router.delete("/tokens/{token_id}", status_code=204)
def delete_token(token_id: int, ctx: Ctx) -> None:
    """Revoke an API token."""
    services.delete_api_token(ctx, token_id)


@router.get("/agent-setup")
def get_agent_setup(ctx: Ctx) -> AgentSetup:
    """The MCP URL, the skill install command, and a clone command for each Brain."""
    return services.get_agent_setup(ctx)
