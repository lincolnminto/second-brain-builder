"""Error responses: each service error has its status code and an ``ErrorResponse`` body."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from .conftest import WEB

if TYPE_CHECKING:
    from .conftest import BrainFixture, MakeClient, MakeContext

API_KEY = "sk-synthetic-provider-key-0001"
PAT = "ghp_syntheticPersonalToken000000000001"


def test_unknown_resource_is_404(client: TestClient) -> None:
    response = client.get("/api/brains/no-such-brain")

    assert response.status_code == 404
    assert set(response.json()) == {"code", "message"}
    assert response.json()["code"] == "not_found"


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_unknown_api_route_is_404_json(client: TestClient, method: str) -> None:
    response = client.request(method, "/api/no/such/route", headers=WEB)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json()["code"] == "not_found"


def test_wrong_method_on_a_known_route_is_405_json(client: TestClient) -> None:
    response = client.put("/api/brains", headers=WEB)

    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"


def test_missing_setup_is_409_with_its_code(client: TestClient, brain: BrainFixture) -> None:
    ingest = client.post(f"/api/brains/{brain.id}/sources/tide-pools/ingest", headers=WEB)
    create = client.post(
        "/api/brains", json={"name": "Notes", "new_repo_name": "notes"}, headers=WEB
    )

    assert (ingest.status_code, ingest.json()["code"]) == (409, "not_configured")
    assert (create.status_code, create.json()["code"]) == (409, "not_connected")


def test_invalid_request_lists_the_issues_without_the_input(client: TestClient) -> None:
    both_repos = client.post(
        "/api/brains",
        json={"name": "Notes", "new_repo_name": "notes", "existing_repo": "a/b"},
        headers=WEB,
    )
    bad_kind = client.post(
        "/api/providers",
        json={"name": "Local", "kind": "other", "base_url": "http://x", "api_key": API_KEY},
        headers=WEB,
    )

    assert both_repos.status_code == 422
    assert both_repos.json()["code"] == "invalid_request"
    assert both_repos.json()["issues"][0]["location"] == ["body"]
    assert bad_kind.status_code == 422
    assert ["body", "kind"] in [issue["location"] for issue in bad_kind.json()["issues"]]
    assert API_KEY not in bad_kind.text


def test_input_that_services_reject_is_422_without_the_secret(client: TestClient) -> None:
    response = client.post(
        "/api/providers",
        json={"name": "Local", "kind": "openai", "base_url": "ftp://bad", "api_key": API_KEY},
        headers=WEB,
    )

    assert (response.status_code, response.json()["code"]) == (422, "invalid_input")
    assert API_KEY not in response.text


def test_github_rate_limit_is_503_with_retry_after(
    make_ctx: MakeContext, make_client: MakeClient
) -> None:
    reset = int(datetime.now().timestamp()) + 600

    def github(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
        )

    client = make_client(make_ctx(github_transport=httpx.MockTransport(github)))

    response = client.post("/api/github/pat", json={"token": PAT}, headers=WEB)

    assert response.status_code == 503
    body = response.json()
    assert (body["code"], body["service"]) == ("upstream", "github")
    assert TypeAdapter(datetime).validate_python(body["retry_at"]).timestamp() == reset
    assert 500 <= int(response.headers["retry-after"]) <= 600
    assert PAT not in response.text


def test_provider_that_rejects_its_key_is_502(
    make_ctx: MakeContext, make_client: MakeClient
) -> None:
    def provider(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"error": {"message": "Incorrect API key provided."}})

    client = make_client(make_ctx(provider_transport=httpx2.MockTransport(provider)))
    created = client.post(
        "/api/providers",
        json={
            "name": "Hosted",
            "kind": "openai",
            "base_url": "https://llm.example.com/v1",
            "api_key": API_KEY,
        },
        headers=WEB,
    )
    provider_id = created.json()["id"]

    models = client.get(f"/api/providers/{provider_id}/models")
    test = client.post(f"/api/providers/{provider_id}/test", headers=WEB)

    assert created.json()["api_key_last4"] == API_KEY[-4:]
    assert models.status_code == 502
    assert (models.json()["code"], models.json()["service"]) == ("auth", "provider")
    assert "www-authenticate" not in models.headers
    assert (test.status_code, test.json()["ok"]) == (200, False)
    assert API_KEY not in models.text + test.text
