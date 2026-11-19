"""Web UI login: ``GET /auth/status``, ``POST /auth/login``, ``POST /auth/logout``."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from second_brain_builder import services
from second_brain_builder.api.schemas import AuthState, LoginRequest
from second_brain_builder.api.state import Ctx, State, get_principal

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status")
def get_auth_status(ctx: Ctx, request: Request) -> AuthState:
    """Whether the UI needs a login (``SBB_PASSWORD`` is set) and whether this request has one."""
    status = services.get_auth_status(ctx)
    return AuthState(
        login_required=status.login_required, authenticated=get_principal(request) is not None
    )


@router.post("/login")
def login(body: LoginRequest, ctx: Ctx, state: State, response: Response) -> AuthState:
    """Check the password and start a session (an ``HttpOnly``, ``SameSite=Strict`` cookie)."""
    services.check_password(ctx, body.password)
    login_required = services.get_auth_status(ctx).login_required
    if login_required:
        state.sessions.set_cookie(response)
    return AuthState(login_required=login_required, authenticated=True)


@router.post("/logout", status_code=204)
def logout(state: State, response: Response) -> None:
    """End the session of this browser: the cookie is removed."""
    state.sessions.clear_cookie(response)
