"""Security rules of the app: Host allowlist, CSRF header, login, API tokens, and /mcp."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from second_brain_builder.api import McpMount, Principal, create_app, mount_mcp
from second_brain_builder.services import AppContext

from .conftest import WEB

if TYPE_CHECKING:
    from .conftest import MakeClient, MakeContext

PASSWORD = "synthetic-test-passphrase"


def create_token(client: TestClient, name: str = "laptop agent") -> tuple[int, str]:
    response = client.post("/api/tokens", json={"name": name}, headers=WEB)
    assert response.status_code == 201, response.text
    created = response.json()
    return created["token"]["id"], created["secret"]


def bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def session_cookie(response: Any) -> str:
    """The ``Set-Cookie`` header of the session cookie."""
    cookies = [
        value for value in response.headers.get_list("set-cookie") if "sbb_session=" in value
    ]
    assert len(cookies) == 1, response.headers
    return str(cookies[0])


# --- Host header ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["evil.example.com", "localhost.evil.example.com", "localhost:abc", "[::1", ""]
)
@pytest.mark.parametrize("path", ["/api/health", "/mcp", "/", "/brains/some-id"])
def test_host_outside_the_allowlist_gets_400_on_every_route(
    client: TestClient, host: str, path: str
) -> None:
    response = client.get(path, headers={"Host": host})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_host"


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST:8484", "127.0.0.1:8484", "[::1]:8484"])
def test_allowed_hosts_match_with_or_without_a_port(client: TestClient, host: str) -> None:
    response = client.get("/api/health", headers={"Host": host})

    assert response.status_code == 200


def test_allowlist_comes_from_the_settings(make_ctx: MakeContext, make_client: MakeClient) -> None:
    client = make_client(make_ctx(allowed_hosts=["brain.example.com"]))

    assert client.get("/api/health", headers={"Host": "brain.example.com"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "localhost"}).status_code == 400


# --- CSRF rule -----------------------------------------------------------------------------


def test_request_that_changes_data_needs_the_client_header(client: TestClient) -> None:
    json_post = client.post("/api/tokens", json={"name": "agent"})
    form_post = client.post("/api/tokens", data={"name": "agent"})

    for response in (json_post, form_post):
        assert response.status_code == 403
        assert response.json()["code"] == "client_header_required"
    assert client.get("/api/tokens").json() == []


def test_client_header_or_bearer_token_allows_the_request(client: TestClient) -> None:
    token_id, secret = create_token(client)

    with_token = client.post("/api/tokens", json={"name": "second"}, headers=bearer(secret))
    delete_without_header = client.delete(f"/api/tokens/{token_id}")
    delete_with_header = client.delete(f"/api/tokens/{token_id}", headers=WEB)

    assert with_token.status_code == 201
    assert delete_without_header.status_code == 403
    assert delete_with_header.status_code == 204


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "PUT", "PATCH", "DELETE", "POST"])
def test_every_non_get_method_needs_the_csrf_header(client: TestClient, method: str) -> None:
    blocked = client.request(method, "/api/health")
    allowed = client.request(method, "/api/health", headers=WEB)

    assert blocked.status_code == 403
    assert allowed.status_code == 405


def test_no_cors_headers_are_sent(client: TestClient) -> None:
    origin = {"Origin": "https://evil.example.com"}
    preflight = client.options(
        "/api/tokens",
        headers={**origin, "Access-Control-Request-Method": "POST"},
    )
    get = client.get("/api/tokens", headers=origin)

    for response in (preflight, get):
        assert not [name for name in response.headers if name.startswith("access-control-")]


# --- login ---------------------------------------------------------------------------------


def test_login_flow_with_a_session_cookie(make_ctx: MakeContext, make_client: MakeClient) -> None:
    client = make_client(make_ctx(password=PASSWORD))

    assert client.get("/api/auth/status").json() == {
        "login_required": True,
        "authenticated": False,
    }
    assert client.get("/api/health").status_code == 200
    blocked = client.get("/api/brains")
    assert (blocked.status_code, blocked.json()["code"]) == (401, "login_required")

    wrong = client.post("/api/auth/login", json={"password": "wrong"}, headers=WEB)
    assert (wrong.status_code, wrong.json()["code"]) == (401, "auth")
    assert "set-cookie" not in wrong.headers

    login = client.post("/api/auth/login", json={"password": PASSWORD}, headers=WEB)
    assert login.status_code == 200
    assert login.json() == {"login_required": True, "authenticated": True}
    attributes = {part.strip().lower() for part in session_cookie(login).split(";")}
    assert {"httponly", "samesite=strict", "path=/"} <= attributes
    assert "secure" not in attributes
    assert client.get("/api/brains").status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True

    logout = client.post("/api/auth/logout", headers=WEB)
    assert logout.status_code == 204
    assert client.get("/api/brains").status_code == 401


def test_login_needs_the_client_header(make_ctx: MakeContext, make_client: MakeClient) -> None:
    client = make_client(make_ctx(password=PASSWORD))

    response = client.post("/api/auth/login", json={"password": PASSWORD})

    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_forged_or_tampered_session_cookie_is_rejected(
    make_ctx: MakeContext, make_client: MakeClient
) -> None:
    client = make_client(make_ctx(password=PASSWORD))
    client.post("/api/auth/login", json={"password": PASSWORD}, headers=WEB)
    value = client.cookies["sbb_session"]
    client.cookies.clear()

    valid = client.get("/api/brains", headers={"Cookie": f"sbb_session={value}"})
    changed = value[:-2] + ("AA" if value[-2:] != "AA" else "BB")
    tampered = client.get("/api/brains", headers={"Cookie": f"sbb_session={changed}"})
    forged = client.get("/api/brains", headers={"Cookie": "sbb_session=e30.forged.signature"})

    assert valid.status_code == 200
    assert tampered.status_code == 401
    assert forged.status_code == 401


def test_new_password_ends_existing_sessions(
    make_ctx: MakeContext, make_client: MakeClient
) -> None:
    old = make_client(make_ctx(password=PASSWORD))
    old.post("/api/auth/login", json={"password": PASSWORD}, headers=WEB)
    cookie = old.cookies["sbb_session"]
    same_password = make_client(make_ctx(password=PASSWORD))
    new_password = make_client(make_ctx(password="another-synthetic-passphrase"))

    header = {"Cookie": f"sbb_session={cookie}"}

    assert same_password.get("/api/brains", headers=header).status_code == 200
    assert new_password.get("/api/brains", headers=header).status_code == 401


def test_session_cookie_is_secure_for_an_https_public_url(
    make_ctx: MakeContext, make_client: MakeClient
) -> None:
    client = make_client(make_ctx(password=PASSWORD, public_url="https://brain.example.com"))

    login = client.post("/api/auth/login", json={"password": PASSWORD}, headers=WEB)

    assert "secure" in {part.strip().lower() for part in session_cookie(login).split(";")}


def test_without_a_password_no_login_is_needed(client: TestClient) -> None:
    assert client.get("/api/auth/status").json() == {
        "login_required": False,
        "authenticated": True,
    }
    assert client.get("/api/brains").status_code == 200
    login = client.post("/api/auth/login", json={"password": "anything"}, headers=WEB)
    assert login.json()["authenticated"] is True
    assert "set-cookie" not in login.headers


# --- API tokens ----------------------------------------------------------------------------


def test_api_token_works_without_a_session(make_ctx: MakeContext, make_client: MakeClient) -> None:
    client = make_client(make_ctx(password=PASSWORD))
    client.post("/api/auth/login", json={"password": PASSWORD}, headers=WEB)
    token_id, secret = create_token(client)
    client.post("/api/auth/logout", headers=WEB)

    listed = client.get("/api/tokens", headers=bearer(secret))
    created = client.post("/api/tokens", json={"name": "ci"}, headers=bearer(secret))

    assert listed.status_code == 200
    assert [token["id"] for token in listed.json()] == [token_id]
    assert listed.json()[0]["last_used_at"] is not None
    assert created.status_code == 201


@pytest.mark.parametrize(
    "authorization",
    ["Bearer sbb_notARealTokenButWellFormed000000", "Bearer not-a-token", "Basic dXNlcjpwYXNz"],
)
def test_invalid_authorization_is_rejected_even_on_get(
    client: TestClient, authorization: str
) -> None:
    response = client.get("/api/brains", headers={"Authorization": authorization})

    assert response.status_code == 401
    assert response.json()["code"] == "auth"
    assert response.headers["www-authenticate"] == "Bearer"


def test_revoked_token_is_rejected(client: TestClient) -> None:
    token_id, secret = create_token(client)
    assert client.get("/api/brains", headers=bearer(secret)).status_code == 200

    client.delete(f"/api/tokens/{token_id}", headers=WEB)

    assert client.get("/api/brains", headers=bearer(secret)).status_code == 401


# --- /mcp ----------------------------------------------------------------------------------


class RecordingMcpApp:
    """An ASGI app in place of the MCP server: it records the requests it gets."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.principals: list[Any] = []
        self.started = False
        self.stopped = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.paths.append(scope["path"])
        self.principals.append(scope["state"].get("sbb_principal"))
        await JSONResponse({"path": scope["path"]})(scope, receive, send)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        self.started = True
        yield
        self.stopped = True


def test_mcp_needs_an_api_token_and_gets_the_path_unchanged(ctx: AppContext) -> None:
    mcp = RecordingMcpApp()
    app = create_app(context=ctx)
    mount_mcp(app, McpMount(app=mcp, lifespan=mcp.lifespan))

    with TestClient(app, base_url="http://localhost") as client:
        assert mcp.started
        _, secret = create_token(client, "mcp agent")
        missing = client.post("/mcp", json={})
        wrong_host = client.post("/mcp", json={}, headers={**bearer(secret), "Host": "evil.test"})
        root = client.post("/mcp", json={}, headers=bearer(secret))
        below = client.get("/mcp/messages", headers=bearer(secret))
    assert mcp.stopped

    assert (missing.status_code, missing.json()["code"]) == (401, "auth")
    assert wrong_host.status_code == 400
    assert (root.status_code, below.status_code) == (200, 200)
    assert mcp.paths == ["/mcp", "/mcp/messages"]
    principal = mcp.principals[0]
    assert isinstance(principal, Principal)
    assert principal.kind == "token"
    assert principal.token is not None and principal.token.name == "mcp agent"


def test_mcp_without_a_mounted_server_is_404(client: TestClient) -> None:
    _, secret = create_token(client)

    response = client.post("/mcp", json={}, headers=bearer(secret))

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_mcp_factory_gets_the_app_context(ctx: AppContext) -> None:
    contexts: list[AppContext] = []
    mcp = RecordingMcpApp()

    def factory(context: AppContext) -> McpMount:
        contexts.append(context)
        return McpMount(app=mcp)

    app = create_app(context=ctx, mcp=factory)

    assert contexts == [ctx]
    with pytest.raises(RuntimeError, match="mounted already"):
        mount_mcp(app, McpMount(app=mcp))


def test_checks_apply_behind_a_root_path(make_ctx: MakeContext) -> None:
    app = create_app(context=make_ctx(password=PASSWORD))

    with TestClient(app, base_url="http://localhost", root_path="/brain") as client:
        blocked = client.get("/brain/api/brains")
        cross_site = client.post("/brain/api/auth/login", json={"password": PASSWORD})
        client.post("/brain/api/auth/login", json={"password": PASSWORD}, headers=WEB)
        allowed = client.get("/brain/api/brains")

    assert (blocked.status_code, blocked.json()["code"]) == (401, "login_required")
    assert cross_site.status_code == 403
    assert allowed.status_code == 200


@pytest.mark.parametrize("path", ["/api/events", "/mcp", "/"])
def test_every_websocket_is_refused(client: TestClient, path: str) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect(path):
        pass
    assert closed.value.code == 1008
