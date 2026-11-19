"""MCP tools through the SDK client against a real local Brain clone."""

import base64
from typing import Any

import httpx2
import pytest
from mcp_types import CallToolResult, TextContent

from second_brain_builder import services
from second_brain_builder.api import create_app
from second_brain_builder.domain import JobKind, JobStatus
from second_brain_builder.mcp import create_mcp_mount

from .conftest import BRAIN_NAME, sdk_session

pytestmark = pytest.mark.anyio


def result_data(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    return " ".join(item.text for item in result.content if isinstance(item, TextContent))


async def test_five_tools_read_and_enqueue(ctx: services.AppContext, brain: str) -> None:
    async with sdk_session(ctx) as client:
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == {
            "list_brains",
            "search",
            "read_page",
            "add_source",
            "save_answer",
        }
        assert all(tool.description for tool in tools)
        listed = result_data(await client.call_tool("list_brains", {}))["result"]
        assert [(item["id"], item["name"]) for item in listed] == [(brain, BRAIN_NAME)]
        hits = result_data(
            await client.call_tool("search", {"brain": BRAIN_NAME, "query": "sea star", "limit": 1})
        )["result"]
        assert len(hits) == 1
        assert hits[0]["path"] == "wiki/entities/sea-star.md"
        assert hits[0]["snippet"]
        for path in ("wiki/entities/sea-star.md", "/wiki/entities/sea-star.md"):
            page = result_data(await client.call_tool("read_page", {"brain": brain, "path": path}))
            assert page["title"] == "Ochre Sea Star"
            assert page["sources"][0]["id"] == "tide-pools"
            assert "[^tide-pools]" in page["body"]

        content = b"Synthetic notes about the example coast."
        added = result_data(
            await client.call_tool(
                "add_source",
                {
                    "brain": BRAIN_NAME,
                    "filename": "coast.txt",
                    "content_base64": base64.b64encode(content).decode(),
                    "folder": "nature",
                },
            )
        )
        job = ctx.jobs.get(added["job_id"])
        assert (job.kind, job.brain_id, job.status) == (JobKind.SOURCE_ADD, brain, JobStatus.QUEUED)
        assert job.payload["folder"] == "nature"
        assert (ctx.settings.data_dir / job.payload["upload_path"]).read_bytes() == content

        saved = result_data(
            await client.call_tool(
                "save_answer",
                {
                    "brain": brain,
                    "title": "Coastal life",
                    "body": "Sea stars live here.[^tide-pools]",
                    "source_ids": ["tide-pools"],
                    "description": "Coastal notes",
                    "tags": ["nature"],
                },
            )
        )
        job = ctx.jobs.get(saved["job_id"])
        assert (job.kind, job.brain_id, job.status) == (
            JobKind.ANSWER_SAVE,
            brain,
            JobStatus.QUEUED,
        )
        assert job.payload["source_ids"] == ["tide-pools"]
        assert job.payload["tags"] == ["nature"]
        assert not (ctx.clone_path(brain) / "wiki/syntheses/coastal-life.md").exists()


async def test_unknown_brain_is_a_tool_error(ctx: services.AppContext) -> None:
    async with sdk_session(ctx) as client:
        result = await client.call_tool("read_page", {"brain": "missing", "path": "index.md"})
        assert "not_found" in error_text(result)
        assert "missing" in error_text(result)


@pytest.mark.parametrize("path", ["../outside.md", "/.git/config", "wiki/../../outside.md"])
async def test_unsafe_paths_are_tool_errors(
    ctx: services.AppContext, brain: str, path: str
) -> None:
    async with sdk_session(ctx) as client:
        result = await client.call_tool("read_page", {"brain": brain, "path": path})
        assert "invalid_input" in error_text(result)


async def test_upload_limits_and_invalid_base64(ctx: services.AppContext, brain: str) -> None:
    ctx.settings = ctx.settings.model_copy(update={"max_upload_bytes": 5})
    async with sdk_session(ctx) as client:
        for encoded, code in (("YWJjZGVm", "upload_too_large"), ("bad!", "invalid_input")):
            result = await client.call_tool(
                "add_source", {"brain": brain, "filename": "test.txt", "content_base64": encoded}
            )
            assert code in error_text(result)
        assert ctx.jobs.list_jobs() == []
        if ctx.settings.uploads_dir.exists():
            assert list(ctx.settings.uploads_dir.iterdir()) == []
        # Eight base64 characters encode five bytes: the decoded size sets the limit.
        result_data(
            await client.call_tool(
                "add_source", {"brain": brain, "filename": "test.txt", "content_base64": "YWJjZGU="}
            )
        )


async def test_upload_can_exceed_the_sdk_default_http_limit(
    ctx: services.AppContext, brain: str
) -> None:
    content = b"Synthetic coastal notes.\n" * 150_000
    ctx.settings = ctx.settings.model_copy(update={"max_upload_bytes": len(content)})
    encoded = base64.b64encode(content).decode()
    assert len(encoded) > 4 * 1024**2
    async with sdk_session(ctx) as client:
        result = result_data(
            await client.call_tool(
                "add_source", {"brain": brain, "filename": "large.txt", "content_base64": encoded}
            )
        )
        job = ctx.jobs.get(result["job_id"])
        assert (ctx.settings.data_dir / job.payload["upload_path"]).read_bytes() == content


@pytest.mark.parametrize("authorization", [None, "Bearer sbb_wrong-synthetic-token"])
async def test_every_mcp_request_needs_a_token(
    ctx: services.AppContext, authorization: str | None
) -> None:
    app = create_app(context=ctx, mcp=create_mcp_mount)
    headers = {} if authorization is None else {"Authorization": authorization}
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://localhost", headers=headers
        ) as http,
    ):
        for method in ("POST", "GET", "DELETE"):
            response = await http.request(method, "/mcp")
            assert response.status_code == 401


async def test_configured_host_and_origin(ctx: services.AppContext) -> None:
    ctx.settings = ctx.settings.model_copy(update={"allowed_hosts": ["brain.example.com"]})
    async with sdk_session(ctx, host="brain.example.com:8484") as client:
        result_data(await client.call_tool("list_brains", {}))
    token = services.create_api_token(ctx, "host check")
    app = create_app(context=ctx, mcp=create_mcp_mount)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://brain.example.com:8484",
            headers={
                "Authorization": f"Bearer {token.secret}",
                "Accept": "application/json, text/event-stream",
            },
        ) as http,
    ):
        for origin, expected in (
            ("https://brain.example.com:444", 200),
            ("https://evil.test", 403),
        ):
            response = await http.post(
                "/mcp",
                headers={"Origin": origin},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "synthetic", "version": "1"},
                    },
                },
            )
            assert response.status_code == expected
        response = await http.post("/mcp", headers={"Host": "evil.test"})
        assert response.status_code == 400
