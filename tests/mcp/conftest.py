"""Synthetic Brain clones and the real SDK client over ASGI HTTP."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from shutil import copytree

import httpx2
import pytest
from mcp.client import ClientSession
from mcp.client.streamable_http import streamable_http_client

from second_brain_builder import services
from second_brain_builder.api import create_app
from second_brain_builder.brains import GitIdentity, GitRepo
from second_brain_builder.config import Settings
from second_brain_builder.domain import BrainStatus, SourceStatus
from second_brain_builder.mcp import create_mcp_mount
from second_brain_builder.store import BrainRow, SourceRow

BRAIN_NAME = "Shore Notes"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def ctx(settings: Settings) -> Iterator[services.AppContext]:
    context = services.AppContext.create(settings)
    yield context
    context.close()


@pytest.fixture
def brain(ctx: services.AppContext, bare_remote: Path) -> str:
    with ctx.sessions.begin() as session:
        row = BrainRow(
            name=BRAIN_NAME, repo_full_name="test-user/shore-notes", status=BrainStatus.READY
        )
        session.add(row)
        session.flush()
        brain_id = row.id
        session.add(
            SourceRow(
                brain_id=brain_id,
                slug="tide-pools",
                folder="nature",
                path="sources/nature/tide-pools/tide-pools.txt",
                sha256="0" * 64,
                title="Tide Pools",
                media_type="text/plain",
                size_bytes=10,
                status=SourceStatus.INGESTED,
            )
        )
    root = ctx.clone_path(brain_id)
    repo = GitRepo(root, GitIdentity(name="test-user", email="test-user@example.com"), None)
    repo.clone(str(bare_remote))
    copytree(Path(__file__).parents[1] / "fixtures/ingest/brain", root, dirs_exist_ok=True)
    repo.commit_all("Add synthetic Brain pages")
    repo.push()
    ctx.search.rebuild(brain_id, root)
    return brain_id


@asynccontextmanager
async def sdk_session(
    ctx: services.AppContext, *, host: str = "localhost:8484"
) -> AsyncIterator[ClientSession]:
    """Exercise authentication, dispatch, lifespan and HTTP transport together."""
    token = services.create_api_token(ctx, "synthetic MCP agent")
    app = create_app(context=ctx, mcp=create_mcp_mount)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": f"Bearer {token.secret}"},
        ) as http,
        streamable_http_client(f"http://{host}/mcp", http_client=http) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        yield session
