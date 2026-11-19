"""The app factory: the HTTP API at ``/api``, the MCP server at ``/mcp``, the web UI at ``/``.

Run it with ``uvicorn --factory second_brain_builder.api.app:create_app``. Without
arguments, ``create_app`` reads the settings from the ``SBB_*`` environment.

The MCP server is mounted with ``mount_mcp`` (or the ``mcp`` argument of
``create_app``). Until then, ``/mcp`` answers 404. In both cases the security
middleware checks the ``Host`` header and the API token of every ``/mcp`` request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from second_brain_builder.api.errors import error_response, install_error_handlers
from second_brain_builder.api.routers import (
    agents,
    auth,
    brains,
    github,
    jobs,
    pages,
    providers,
    sources,
    system,
)
from second_brain_builder.api.schemas import ErrorResponse
from second_brain_builder.api.security import (
    API_PREFIX,
    MCP_PREFIX,
    SAFE_METHODS,
    SecurityMiddleware,
    is_under,
    route_path,
)
from second_brain_builder.api.sessions import SessionSigner
from second_brain_builder.api.state import (
    ApiState,
    McpFactory,
    McpMount,
    SecondBrainApp,
    api_state,
)
from second_brain_builder.api.static import SKILL_PATH, find_web_dir, skill_response, ui_response
from second_brain_builder.config import Settings, get_settings
from second_brain_builder.services import AppContext

OPENAPI_URL = f"{API_PREFIX}/openapi.json"
DEFAULT_EVENT_POLL_SECONDS = 1.0

OPENAPI_TAGS = [
    {"name": "system", "description": "Health and setup status."},
    {"name": "auth", "description": "Web UI login, when SBB_PASSWORD is set."},
    {"name": "github", "description": "The GitHub connection."},
    {"name": "providers", "description": "LLM Providers and task settings."},
    {"name": "brains", "description": "Brains: one GitHub repository each."},
    {"name": "sources", "description": "Sources and Folders of a Brain."},
    {"name": "pages", "description": "Pages, files, graph, and search of a Brain."},
    {"name": "jobs", "description": "Jobs and the live event stream."},
    {"name": "agents", "description": "API tokens and agent setup."},
]

_ROUTERS = (
    system.router,
    auth.router,
    github.router,
    providers.router,
    brains.router,
    sources.router,
    pages.router,
    jobs.router,
    agents.router,
)


def create_app(
    settings: Settings | None = None,
    *,
    context: AppContext | None = None,
    mcp: McpFactory | None = None,
    event_poll_seconds: float = DEFAULT_EVENT_POLL_SECONDS,
) -> SecondBrainApp:
    """Build the app.

    Give ``settings`` (or nothing, to read the environment) and the app builds and
    later closes its own ``AppContext``; or give a ``context`` that the caller owns.
    ``mcp`` builds the MCP server for ``/mcp`` from the context.

    Raises:
        ValueError: Both ``settings`` and ``context`` are given.
    """
    if settings is not None and context is not None:
        raise ValueError("Give settings or context, not both.")
    owns_context = context is None
    if context is None:
        context = AppContext.create(settings if settings is not None else get_settings())
    api = ApiState(
        context=context,
        owns_context=owns_context,
        sessions=SessionSigner.from_settings(context.settings),
        web_dir=find_web_dir(context.settings),
        event_poll_seconds=event_poll_seconds,
    )
    app = SecondBrainApp(
        api,
        title="Second Brain Builder",
        version=_package_version(),
        openapi_url=OPENAPI_URL,
        openapi_tags=OPENAPI_TAGS,
        docs_url=None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
        lifespan=_lifespan,
        generate_unique_id_function=_operation_id,
        responses={"default": {"model": ErrorResponse, "description": "An error."}},
        telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
    )
    for router in _ROUTERS:
        app.include_router(router, prefix=API_PREFIX)
    app.add_api_route(
        SKILL_PATH, _get_skill, methods=["GET"], include_in_schema=False, name="get_skill"
    )
    app.router.default = _NotFound(api)
    install_error_handlers(app)
    # The last middleware added runs first: the security checks wrap the MCP dispatch.
    app.add_middleware(_McpDispatch, api=api)
    app.add_middleware(SecurityMiddleware, api=api)
    if mcp is not None:
        mount_mcp(app, mcp(context))
    return app


def mount_mcp(app: FastAPI, mount: McpMount) -> None:
    """Serve an MCP server at ``/mcp``. Call it before the app starts.

    See ``McpMount`` for what the MCP app receives. Its ``lifespan`` runs in the
    app's lifespan.

    Raises:
        RuntimeError: An MCP server is mounted already.
        TypeError: ``app`` was not built by ``create_app``.
    """
    api = api_state(app)
    if api.mcp is not None:
        raise RuntimeError("An MCP server is mounted already.")
    api.mcp = mount


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    api = api_state(app)
    try:
        async with AsyncExitStack() as stack:
            if api.mcp is not None and api.mcp.lifespan is not None:
                await stack.enter_async_context(api.mcp.lifespan())
            yield
    finally:
        if api.owns_context:
            api.context.close()


def _operation_id(route: APIRoute) -> str:
    """The OpenAPI operation id: the endpoint function name, such as ``list_brains``."""
    return route.name


def _package_version() -> str:
    try:
        return version("second-brain-builder")
    except PackageNotFoundError:
        return "0.0.0"


def _get_skill() -> Response:
    return skill_response()


class _McpDispatch:
    """Send ``/mcp`` requests to the mounted MCP app, with the path unchanged."""

    def __init__(self, app: ASGIApp, api: ApiState) -> None:
        self.app = app
        self._api = api

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and is_under(route_path(scope), MCP_PREFIX):
            mount = self._api.mcp
            if mount is None:
                body = ErrorResponse(code="not_found", message="The MCP server is not available.")
                await error_response(404, body)(scope, receive, send)
            else:
                await mount.app(scope, receive, send)
            return
        await self.app(scope, receive, send)


class _NotFound:
    """The router's handler for a path that no route matches.

    Unknown ``/api`` paths get a 404 ``ErrorResponse``. A GET outside ``/api`` and
    ``/mcp`` gets the web UI (a file of the build, or ``index.html``).
    """

    def __init__(self, api: ApiState) -> None:
        self._api = api

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await WebSocketClose()(scope, receive, send)
            return
        path = route_path(scope)
        if is_under(path, API_PREFIX) or is_under(path, MCP_PREFIX):
            body = ErrorResponse(code="not_found", message=f"No route matches {path}.")
            response: Response = error_response(404, body)
        elif scope["method"] not in SAFE_METHODS:
            body = ErrorResponse(code="method_not_allowed", message="Use GET for the web UI.")
            response = error_response(405, body, {"Allow": "GET, HEAD"})
        else:
            response = ui_response(self._api.web_dir, path)
        await response(scope, receive, send)
