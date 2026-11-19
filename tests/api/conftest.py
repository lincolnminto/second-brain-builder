"""Fixtures for the HTTP API tests.

Every test gets a real SQLite database and search index in tmp_path, and the app
from ``create_app``. ``brain`` is a real Brain clone of a local bare remote. The
``TestClient`` sends ``Host: localhost``, which the default allowlist accepts.
``call_asgi`` drives the app directly at the ASGI level, for tests that must see
how much of a request body the app reads.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient

from second_brain_builder.api import create_app
from second_brain_builder.brains import BrainSettings, GitIdentity, GitRepo, scaffold_brain
from second_brain_builder.config import Settings
from second_brain_builder.domain import BrainStatus, SourceStatus
from second_brain_builder.services import AppContext
from second_brain_builder.store import BrainRow, SourceRow

FIXTURES = Path(__file__).parents[1] / "fixtures"
INGEST_BRAIN = FIXTURES / "ingest" / "brain"
REPO = "test-user/shore-notes"
WEB = {"X-SBB-Client": "web"}
"""The header that the web UI sends with every request that changes data."""
EVENT_POLL_SECONDS = 0.05

MakeContext = Callable[..., AppContext]
MakeClient = Callable[..., TestClient]


@pytest.fixture
def make_ctx(settings: Settings) -> Iterator[MakeContext]:
    """Build an ``AppContext`` on the test database, with settings changed by keyword.

    ``github_transport`` and ``provider_transport`` go to ``AppContext.create``.
    """
    contexts: list[AppContext] = []

    def make(
        *,
        github_transport: httpx.BaseTransport | None = None,
        provider_transport: httpx2.BaseTransport | None = None,
        **changes: Any,
    ) -> AppContext:
        context = AppContext.create(
            Settings(**{**settings.model_dump(), **changes}),  # validates the changes
            github_transport=github_transport,
            provider_transport=provider_transport,
        )
        contexts.append(context)
        return context

    yield make
    for context in contexts:
        context.close()


@pytest.fixture
def ctx(make_ctx: MakeContext) -> AppContext:
    return make_ctx()


@pytest.fixture
def make_client() -> Iterator[MakeClient]:
    """Build a started ``TestClient`` for ``create_app(context=ctx, **options)``."""
    with ExitStack() as stack:

        def make(context: AppContext, **options: Any) -> TestClient:
            app = create_app(context=context, event_poll_seconds=EVENT_POLL_SECONDS, **options)
            return stack.enter_context(TestClient(app, base_url="http://localhost"))

        yield make


@pytest.fixture
def client(make_client: MakeClient, ctx: AppContext) -> TestClient:
    return make_client(ctx)


def insert_brain(ctx: AppContext, repo_full_name: str, name: str = "Shore Notes") -> str:
    """Insert a ready Brain row without a clone and return its id."""
    with ctx.sessions.begin() as session:
        row = BrainRow(name=name, repo_full_name=repo_full_name, status=BrainStatus.READY)
        session.add(row)
        session.flush()
        return row.id


@dataclass(frozen=True)
class BrainFixture:
    id: str
    root: Path


@pytest.fixture
def brain(ctx: AppContext, bare_remote: Path) -> BrainFixture:
    """A ready Brain ``test-user/shore-notes``: a real clone of a local bare remote.

    The content is tests/fixtures/ingest/brain, scaffolded, committed, and pushed.
    Sources (Folder ``nature``): ``tide-pools`` and ``coastal-birds``, with the
    SHA-256 of their files. The search index has the Brain's pages.
    """
    brain_id = insert_brain(ctx, REPO)
    root = ctx.clone_path(brain_id)
    repo = GitRepo(root, GitIdentity(name="test-user", email="test-user@example.com"), None)
    repo.clone(str(bare_remote))
    shutil.copytree(INGEST_BRAIN, root, dirs_exist_ok=True)
    scaffold_brain(
        root,
        BrainSettings(
            name="Shore Notes",
            language="en",
            created_at=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
        ),
    )
    repo.commit_all("Create brain: Shore Notes")
    repo.push()
    with ctx.sessions.begin() as session:
        for slug, title in (
            ("tide-pools", "Tide Pools of the Example Coast"),
            ("coastal-birds", "Coastal Birds"),
        ):
            source_file = root / "sources" / "nature" / slug / f"{slug}.txt"
            session.add(
                SourceRow(
                    brain_id=brain_id,
                    slug=slug,
                    folder="nature",
                    path=f"sources/nature/{slug}/{slug}.txt",
                    sha256=hashlib.sha256(source_file.read_bytes()).hexdigest(),
                    title=title,
                    media_type="text/plain",
                    size_bytes=source_file.stat().st_size,
                    status=SourceStatus.INGESTED,
                )
            )
    ctx.search.rebuild(brain_id, root)
    return BrainFixture(id=brain_id, root=root)


def staged_uploads(ctx: AppContext) -> list[Path]:
    uploads = ctx.settings.uploads_dir
    return sorted(uploads.iterdir()) if uploads.is_dir() else []


# --- raw ASGI calls ------------------------------------------------------------------------


@dataclass
class AsgiResponse:
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    chunks_read: int = 0
    """How many body chunks the app took from ``receive``."""


def call_asgi(
    app: Any,
    method: str,
    path: str,
    headers: dict[str, str],
    chunks: Iterable[bytes],
) -> AsgiResponse:
    """Send one HTTP request to ``app`` with the body in ``chunks``, one per ``receive``.

    Unlike ``TestClient``, which reads the whole body first, the app here gets each
    chunk only when it asks for it, as from a real server.
    """
    result = AsgiResponse()
    pending = iter(chunks)
    done = False

    async def receive() -> dict[str, Any]:
        nonlocal done
        if done:
            return {"type": "http.disconnect"}
        chunk = next(pending, None)
        if chunk is None:
            done = True
            return {"type": "http.request", "body": b"", "more_body": False}
        result.chunks_read += 1
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            result.status = message["status"]
            result.headers = {
                key.decode("latin-1"): value.decode("latin-1")
                for key, value in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            result.body += message.get("body", b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in {"host": "localhost", **headers}.items()
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("localhost", 80),
        "state": {},
    }

    async def main() -> None:
        await app(scope, receive, send)

    anyio.run(main)
    return result
