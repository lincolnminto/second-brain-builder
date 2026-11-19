"""The web UI at ``/``: build files, ``index.html`` for client routes, and what it never serves."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from .conftest import WEB

if TYPE_CHECKING:
    from .conftest import MakeClient, MakeContext

INDEX = "<!doctype html><title>Second Brain</title><div id=root></div>"
SCRIPT = "console.log('synthetic build');"


@pytest.fixture
def web_dir(tmp_path: Path) -> Path:
    """A synthetic UI build, next to a file that must never be served."""
    build = tmp_path / "web" / "dist"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text(INDEX)
    (build / "assets" / "app-3f2a1b.js").write_text(SCRIPT)
    (build / ".env").write_text("SECRET=synthetic")
    (tmp_path / "web" / "outside.txt").write_text("not part of the build")
    return build


@pytest.fixture
def ui(make_ctx: MakeContext, make_client: MakeClient, web_dir: Path) -> TestClient:
    return make_client(make_ctx(web_dir=web_dir))


@pytest.mark.parametrize("path", ["/", "/brains/b1", "/brains/b1/pages/wiki/entities/x.md"])
def test_client_routes_get_index_html(ui: TestClient, path: str) -> None:
    response = ui.get(path)

    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_build_assets_are_served_with_a_long_cache(ui: TestClient) -> None:
    response = ui.get("/assets/app-3f2a1b.js")

    assert response.status_code == 200
    assert response.text == SCRIPT
    assert "immutable" in response.headers["cache-control"]


def test_missing_asset_is_404_not_index_html(ui: TestClient) -> None:
    response = ui.get("/assets/app-old.js")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_api_and_mcp_paths_never_fall_back_to_the_ui(ui: TestClient) -> None:
    api = ui.get("/api/unknown")
    api_root = ui.get("/api")

    for response in (api, api_root):
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"


@pytest.mark.parametrize("path", ["/..%2Foutside.txt", "/%2e%2e/outside.txt", "/.env"])
def test_files_outside_the_build_or_hidden_are_not_served(ui: TestClient, path: str) -> None:
    response = ui.get(path)

    assert response.status_code == 200
    assert response.text == INDEX


def test_ui_only_answers_get(ui: TestClient) -> None:
    response = ui.post("/brains/b1", headers=WEB)

    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"


def test_without_a_build_the_root_is_404_json(
    make_ctx: MakeContext, make_client: MakeClient, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    client = make_client(make_ctx(web_dir=empty))

    response = client.get("/")

    assert response.status_code == 404
    assert "web UI is not built" in response.json()["message"]
