"""The production entry point serves the MCP server, not only the API."""

import httpx2
import pytest
from mcp.client import ClientSession
from mcp.client.streamable_http import streamable_http_client

from second_brain_builder import services
from second_brain_builder.config import Settings
from second_brain_builder.server import create_server


@pytest.mark.anyio
async def test_production_app_serves_the_mcp_tools(settings: Settings) -> None:
    owner = services.AppContext.create(settings)
    try:
        token = services.create_api_token(owner, "synthetic agent")
    finally:
        owner.close()
    app = create_server()  # reads the same SBB_* environment as the settings fixture

    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": f"Bearer {token.secret}"},
        ) as http,
        streamable_http_client("http://localhost:8484/mcp", http_client=http) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()

    assert {tool.name for tool in tools.tools} == {
        "list_brains",
        "search",
        "read_page",
        "add_source",
        "save_answer",
    }


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
