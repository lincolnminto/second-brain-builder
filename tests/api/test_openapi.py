"""The OpenAPI document: the contract that the web UI generates its client from.

Every route of the HTTP API table in docs/architecture.md must be in it, with a
stable operation id, and every error must be documented as ``ErrorResponse``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import MakeClient, MakeContext

ROUTES = {
    # Health and setup
    ("GET", "/api/health"): "get_health",
    ("GET", "/api/setup"): "get_setup",
    # Auth
    ("GET", "/api/auth/status"): "get_auth_status",
    ("POST", "/api/auth/login"): "login",
    ("POST", "/api/auth/logout"): "logout",
    # GitHub
    ("POST", "/api/github/device/start"): "start_device_flow",
    ("POST", "/api/github/device/poll"): "poll_device_flow",
    ("POST", "/api/github/pat"): "connect_pat",
    ("GET", "/api/github/account"): "get_github_account",
    ("DELETE", "/api/github/account"): "disconnect_github",
    ("GET", "/api/github/repos"): "list_github_repos",
    ("GET", "/api/github/install-url"): "get_install_url",
    # Providers
    ("GET", "/api/providers"): "list_providers",
    ("POST", "/api/providers"): "create_provider",
    ("PATCH", "/api/providers/{provider_id}"): "update_provider",
    ("DELETE", "/api/providers/{provider_id}"): "delete_provider",
    ("POST", "/api/providers/{provider_id}/test"): "test_provider",
    ("GET", "/api/providers/{provider_id}/models"): "list_provider_models",
    ("GET", "/api/settings/tasks"): "get_task_settings",
    ("PUT", "/api/settings/tasks"): "put_task_settings",
    # Brains
    ("GET", "/api/brains"): "list_brains",
    ("POST", "/api/brains"): "create_brain",
    ("GET", "/api/brains/{brain_id}"): "get_brain",
    ("DELETE", "/api/brains/{brain_id}"): "disconnect_brain",
    ("POST", "/api/brains/{brain_id}/sync"): "sync_brain",
    ("GET", "/api/brains/{brain_id}/lfs-usage"): "get_lfs_usage",
    # Sources
    ("GET", "/api/brains/{brain_id}/folders"): "list_folders",
    ("GET", "/api/brains/{brain_id}/sources"): "list_sources",
    ("POST", "/api/brains/{brain_id}/sources"): "add_source",
    ("GET", "/api/brains/{brain_id}/sources/{slug}"): "get_source",
    ("DELETE", "/api/brains/{brain_id}/sources/{slug}"): "delete_source",
    ("GET", "/api/brains/{brain_id}/sources/{slug}/original"): "download_original",
    ("POST", "/api/brains/{brain_id}/sources/{slug}/move"): "move_source",
    ("POST", "/api/brains/{brain_id}/sources/{slug}/reconvert"): "reconvert_source",
    ("POST", "/api/brains/{brain_id}/sources/{slug}/ingest"): "ingest_source",
    ("GET", "/api/brains/{brain_id}/sources/{slug}/ingest-estimate"): "ingest_estimate",
    # Pages
    ("GET", "/api/brains/{brain_id}/pages"): "list_pages",
    ("GET", "/api/brains/{brain_id}/pages/{path}"): "read_page",
    ("GET", "/api/brains/{brain_id}/files/{path}"): "get_file",
    ("GET", "/api/brains/{brain_id}/graph"): "get_graph",
    ("GET", "/api/brains/{brain_id}/search"): "search",
    # Jobs and events
    ("GET", "/api/jobs"): "list_jobs",
    ("GET", "/api/jobs/{job_id}"): "get_job",
    ("POST", "/api/jobs/{job_id}/retry"): "retry_job",
    ("POST", "/api/jobs/{job_id}/cancel"): "cancel_job",
    ("GET", "/api/events"): "stream_events",
    # Agents
    ("GET", "/api/tokens"): "list_tokens",
    ("POST", "/api/tokens"): "create_token",
    ("DELETE", "/api/tokens/{token_id}"): "delete_token",
    ("GET", "/api/agent-setup"): "get_agent_setup",
}


@pytest.fixture
def spec(make_ctx: MakeContext, make_client: MakeClient) -> dict[str, Any]:
    """The OpenAPI document, read without a login although a password is set."""
    client: TestClient = make_client(make_ctx(password="synthetic-test-passphrase"))
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    document: dict[str, Any] = response.json()
    return document


def operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (method.upper(), path): operation
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
    }


def test_every_route_of_the_api_table_has_its_operation_id(spec: dict[str, Any]) -> None:
    found = {key: operation["operationId"] for key, operation in operations(spec).items()}

    assert found == ROUTES


def test_errors_are_documented_as_error_response(spec: dict[str, Any]) -> None:
    error_ref = "#/components/schemas/ErrorResponse"
    for key, operation in operations(spec).items():
        default = operation["responses"]["default"]["content"]["application/json"]["schema"]
        assert default == {"$ref": error_ref}, key


def test_upload_and_event_stream_are_documented(spec: dict[str, Any]) -> None:
    upload = operations(spec)[("POST", "/api/brains/{brain_id}/sources")]
    events = operations(spec)[("GET", "/api/events")]

    form = upload["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert form["properties"]["file"]["format"] == "binary"
    assert {parameter["name"] for parameter in upload["parameters"]} >= {
        "folder",
        "confirm_over_quota",
    }
    assert list(events["responses"]["200"]["content"]) == ["text/event-stream"]
    item = events["responses"]["200"]["content"]["text/event-stream"]["itemSchema"]
    assert item["properties"]["data"]["contentSchema"] == {
        "$ref": "#/components/schemas/JobEventView"
    }
    assert "JobEventView" in spec["components"]["schemas"]


def test_map_reduce_request_and_progress_fields(spec: dict[str, Any]) -> None:
    schemas = spec["components"]["schemas"]
    assert schemas["SourceIngest"]["properties"]["mode"]["enum"] == [
        "auto",
        "sequential",
        "map_reduce",
    ]
    assert {
        "mode",
        "phase",
        "map_parts_done",
        "reduce_groups_done",
        "reduce_groups_total",
    } <= schemas["SourceView"]["properties"].keys()
    assert {"map_estimated_tokens", "reduce_estimated_tokens"} <= schemas["IngestEstimate"][
        "properties"
    ].keys()
