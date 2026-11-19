"""The GitHub connection: device flow, personal access token, account, repositories."""

from __future__ import annotations

from fastapi import APIRouter

from second_brain_builder import services
from second_brain_builder.api.schemas import (
    DeviceFlowPoll,
    DeviceFlowStart,
    GitHubAccountView,
    GitHubPatInput,
    InstallUrl,
    RepoView,
)
from second_brain_builder.api.state import Ctx

router = APIRouter(prefix="/github", tags=["github"])


@router.post("/device/start")
def start_device_flow(ctx: Ctx) -> DeviceFlowStart:
    """Start the GitHub App device flow. Show ``user_code`` and ``verification_uri``."""
    return services.start_github_device_flow(ctx)


@router.post("/device/poll")
def poll_device_flow(ctx: Ctx) -> DeviceFlowPoll:
    """Poll once for the user's authorization. Poll again after ``interval`` seconds."""
    return services.poll_github_device_flow(ctx)


@router.post("/pat")
def connect_pat(body: GitHubPatInput, ctx: Ctx) -> GitHubAccountView:
    """Connect GitHub with a personal access token. It replaces a connected account."""
    return services.connect_github_pat(ctx, body.token)


@router.get("/account")
def get_github_account(ctx: Ctx) -> GitHubAccountView | None:
    """The connected GitHub account, or ``null``."""
    return services.get_github_account(ctx)


@router.delete("/account", status_code=204)
def disconnect_github(ctx: Ctx) -> None:
    """Remove the connected account and its token. Brains stay."""
    services.disconnect_github(ctx)


@router.get("/repos")
def list_github_repos(ctx: Ctx) -> list[RepoView]:
    """The repositories that the connected account can use, with the Brain of each."""
    return services.list_github_repos(ctx)


@router.get("/install-url")
def get_install_url(ctx: Ctx) -> InstallUrl:
    """The page where the user installs the GitHub App on repositories."""
    return InstallUrl(url=services.github_install_url(ctx))
