"""The state of one API app, and the FastAPI dependencies that read it."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Request
from starlette.types import ASGIApp

from second_brain_builder.api.openapi import document_event_stream
from second_brain_builder.api.sessions import SessionSigner
from second_brain_builder.services import ApiTokenView, AppContext

PRINCIPAL_KEY = "sbb_principal"
"""``scope["state"]`` key of the ``Principal`` that the security middleware found."""


@dataclass(frozen=True)
class McpMount:
    """An MCP server for ``/mcp``.

    ``app`` gets every request whose path is ``/mcp`` or starts with ``/mcp/``, with
    the path unchanged, so a FastMCP app with ``streamable_http_path="/mcp"`` fits.
    The security middleware has checked the ``Host`` header and the Bearer token
    first: the request's ``Principal`` (with the ``ApiTokenView``) is in
    ``scope["state"]["sbb_principal"]``.

    ``lifespan``, if set, runs for the lifetime of the app, for example
    ``session_manager.run`` of the MCP Streamable HTTP transport.
    """

    app: ASGIApp
    lifespan: Callable[[], AbstractAsyncContextManager[Any]] | None = None


McpFactory = Callable[[AppContext], McpMount]
"""Builds the MCP server from the app's ``AppContext``."""


@dataclass(frozen=True)
class Principal:
    """Who sends a request.

    ``open``: no login is needed (``SBB_PASSWORD`` is unset) and the request has no
    API token. ``session``: the web UI session cookie. ``token``: an API token.
    """

    kind: Literal["open", "session", "token"]
    token: ApiTokenView | None = None


@dataclass
class ApiState:
    """Everything that the routes and middleware of one app share."""

    context: AppContext
    owns_context: bool
    """True when ``create_app`` built the context, so the app closes it on shutdown."""
    sessions: SessionSigner
    web_dir: Path | None
    """The built web UI, or None when it is not built."""
    event_poll_seconds: float
    """How often ``GET /api/events`` reads new job events."""
    mcp: McpMount | None = None


class SecondBrainApp(FastAPI):
    """The FastAPI app of second-brain-builder, with its typed ``ApiState``."""

    def __init__(self, api: ApiState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api = api

    def openapi(self) -> dict[str, Any]:
        """The OpenAPI document, with the event stream documented as ``text/event-stream``."""
        if self.openapi_schema is None:
            document_event_stream(super().openapi())  # FastAPI caches the document
        return super().openapi()


def api_state(app: object) -> ApiState:
    """The ``ApiState`` of an app that ``create_app`` built."""
    if not isinstance(app, SecondBrainApp):
        raise TypeError("This app was not built by second_brain_builder.api.create_app.")
    return app.api


async def get_state(request: Request) -> ApiState:
    return api_state(request.app)


async def get_context(request: Request) -> AppContext:
    return api_state(request.app).context


def get_principal(request: Request) -> Principal | None:
    """The ``Principal`` of the request, or ``None`` when it has no valid login."""
    state = request.scope.get("state")
    value = state.get(PRINCIPAL_KEY) if isinstance(state, dict) else None
    return value if isinstance(value, Principal) else None


State = Annotated[ApiState, Depends(get_state)]
Ctx = Annotated[AppContext, Depends(get_context)]
