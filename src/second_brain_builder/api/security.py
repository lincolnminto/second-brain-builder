"""Request checks that apply before routing: Host allowlist, CSRF rule, login, API tokens.

One pure ASGI middleware does all checks, in this order:

1. Every request (``/api``, ``/mcp``, and the web UI): the ``Host`` header must be
   in ``SBB_ALLOWED_HOSTS``, else HTTP 400. This blocks DNS rebinding.
2. ``/api``: an ``Authorization`` header must hold a valid API token
   (``Bearer sbb_...``), else HTTP 401.
3. ``/api``: a request that is not GET needs an API token or the header
   ``X-SBB-Client: web``, else HTTP 403. A cross-site form cannot set the header,
   and the app sends no CORS headers, so a cross-site script cannot either.
4. ``/api``: when ``SBB_PASSWORD`` is set, every route except the public ones
   (health, login status, login, logout, the OpenAPI document) needs the session
   cookie or an API token, else HTTP 401.
5. ``/mcp``: every request needs a valid API token, else HTTP 401.

The ``Principal`` of an allowed request is put in ``scope["state"]``.
"""

from __future__ import annotations

import anyio
from pydantic import SecretStr
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from second_brain_builder import services
from second_brain_builder.api.errors import error_response, service_error_response
from second_brain_builder.api.schemas import ErrorResponse
from second_brain_builder.api.sessions import SESSION_COOKIE
from second_brain_builder.api.state import PRINCIPAL_KEY, ApiState, Principal
from second_brain_builder.services import AuthError

API_PREFIX = "/api"
MCP_PREFIX = "/mcp"
CLIENT_HEADER = "x-sbb-client"
CLIENT_HEADER_VALUE = "web"
SAFE_METHODS = frozenset({"GET", "HEAD"})

PUBLIC_API_ROUTES = frozenset(
    {
        ("GET", "/api/health"),
        ("GET", "/api/auth/status"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/openapi.json"),
    }
)
"""Routes that need no login when ``SBB_PASSWORD`` is set. HEAD counts as GET."""

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "SAMEORIGIN",
}
"""Headers added to every HTTP response that does not set them itself."""

API_HEADERS = {**SECURITY_HEADERS, "cache-control": "no-store"}
"""Headers added to ``/api`` responses: API data, such as a new token, is never cached."""

_AUTH_HINT = "Send an API token as Authorization: Bearer sbb_..."


def is_under(path: str, prefix: str) -> bool:
    """True when ``path`` is ``prefix`` or below it (``/api`` and ``/api/x``, not ``/apix``)."""
    return path == prefix or path.startswith(f"{prefix}/")


def route_path(scope: Scope) -> str:
    """The path that the router matches: ``scope["path"]`` without the ASGI ``root_path``.

    Behind a reverse proxy prefix (``uvicorn --root-path /brain``), ``scope["path"]``
    is ``/brain/api/...`` while the routes match ``/api/...``. The checks use the
    same path as the router, so a prefix cannot skip them.
    """
    path: str = scope["path"]
    root: str = scope.get("root_path", "")
    if root and path.startswith(root) and path[len(root) : len(root) + 1] in ("", "/"):
        return path[len(root) :] or "/"
    return path


def host_name(value: str) -> str | None:
    """The host of a ``Host`` header, lowercased and without the port; None if malformed.

    ``localhost:8484`` gives ``localhost``, and ``[::1]:8484`` gives ``[::1]``.
    """
    text = value.strip().lower()
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return None
        host, port = text[: end + 1], text[end + 1 :]
    else:
        host, colon, digits = text.partition(":")
        port = colon + digits
    if port and not (port.startswith(":") and port[1:].isdigit()):
        return None
    return host or None


class SecurityMiddleware:
    """The checks in the module docstring. Add it as the outermost user middleware."""

    def __init__(self, app: ASGIApp, api: ApiState) -> None:
        self.app = app
        self._api = api
        self._allowed_hosts = frozenset(api.context.settings.allowed_hosts)
        self._login_required = api.context.settings.password is not None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # The app has no WebSocket routes, and the checks below are HTTP-only:
            # refuse every WebSocket so a future route cannot skip them by accident.
            await WebSocketClose(code=1008)(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = host_name(headers.get("host", ""))
        path = route_path(scope)
        method: str = scope["method"]
        send = _with_default_headers(
            send, API_HEADERS if is_under(path, API_PREFIX) else SECURITY_HEADERS
        )
        if host is None or host not in self._allowed_hosts:
            body = ErrorResponse(
                code="invalid_host",
                message="The Host header is not allowed. Add the host to SBB_ALLOWED_HOSTS.",
            )
            await error_response(400, body)(scope, receive, send)
            return
        try:
            if is_under(path, API_PREFIX):
                principal = await self._api_principal(method, path, headers)
            elif is_under(path, MCP_PREFIX):
                principal = await self._token_principal(headers.get("authorization"))
            else:
                principal = None
        except AuthError as exc:
            await service_error_response(exc)(scope, receive, send)
            return
        except _Rejected as rejected:
            await error_response(rejected.status_code, rejected.body)(scope, receive, send)
            return
        if principal is not None:
            scope.setdefault("state", {})[PRINCIPAL_KEY] = principal
        await self.app(scope, receive, send)

    async def _api_principal(self, method: str, path: str, headers: Headers) -> Principal | None:
        principal: Principal | None = None
        authorization = headers.get("authorization")
        if authorization is not None:
            principal = await self._token_principal(authorization)
        if (
            method != "GET"
            and principal is None
            and headers.get(CLIENT_HEADER) != CLIENT_HEADER_VALUE
        ):
            raise _Rejected(
                403,
                ErrorResponse(
                    code="client_header_required",
                    message="A request that changes data needs the header X-SBB-Client: web "
                    f"or an API token. {_AUTH_HINT}",
                ),
            )
        if principal is None:
            if not self._login_required:
                principal = Principal(kind="open")
            elif self._api.sessions.is_valid(_session_cookie(headers)):
                principal = Principal(kind="session")
        route = ("GET" if method == "HEAD" else method, path)
        if principal is None and route not in PUBLIC_API_ROUTES:
            raise _Rejected(
                401,
                ErrorResponse(code="login_required", message="Log in to use the app."),
            )
        return principal

    async def _token_principal(self, authorization: str | None) -> Principal:
        """Check an ``Authorization`` header through ``services.verify_api_token``.

        Raises:
            AuthError: The header is missing, is not ``Bearer``, or the token is not valid.
        """
        if authorization is None:
            raise AuthError(f"This endpoint needs an API token. {_AUTH_HINT}", service="app")
        scheme, _, token = authorization.strip().partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError(f"The Authorization header is not valid. {_AUTH_HINT}", service="app")
        view = await anyio.to_thread.run_sync(
            services.verify_api_token, self._api.context, SecretStr(token.strip())
        )
        return Principal(kind="token", token=view)


class _Rejected(Exception):
    def __init__(self, status_code: int, body: ErrorResponse) -> None:
        super().__init__(body.message)
        self.status_code = status_code
        self.body = body


def _session_cookie(headers: Headers) -> str | None:
    cookie = headers.get("cookie")
    return None if cookie is None else cookie_parser(cookie).get(SESSION_COOKIE)


def _with_default_headers(send: Send, defaults: dict[str, str]) -> Send:
    """Wrap ``send`` to add each header of ``defaults`` that the response does not set."""

    async def send_with_headers(message: Message) -> None:
        if message["type"] == "http.response.start":
            message.setdefault("headers", [])
            headers = MutableHeaders(scope=message)
            for name, value in defaults.items():
                if name not in headers:
                    headers.append(name, value)
        await send(message)

    return send_with_headers
