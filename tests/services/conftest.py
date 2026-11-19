"""Fixtures for the services tests.

Every test gets a real SQLite database and search index in tmp_path. GitHub is a
scripted ``httpx.MockTransport`` and Providers a scripted ``httpx2.MockTransport``.
The ``brain`` fixture is a real Brain clone of a local bare remote, with its
Sources in Git LFS when git-lfs is installed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import httpx2
import pytest
from pydantic import SecretStr

from second_brain_builder.brains import (
    BrainLayout,
    BrainSettings,
    GitIdentity,
    GitRepo,
    scaffold_brain,
)
from second_brain_builder.config import Settings
from second_brain_builder.domain import BrainStatus, SourceStatus
from second_brain_builder.services import AppContext, GitHubAccountView, connect_github_pat
from second_brain_builder.store import BrainRow, SourceRow, utc_now

FIXTURES = Path(__file__).parents[1] / "fixtures"
INGEST_BRAIN = FIXTURES / "ingest" / "brain"
API = "https://api.github.com"
OAUTH = "https://github.com"
PAT = "ghp_syntheticPersonalToken000000000001"
LOGIN = "test-user"
USER_ID = 1000001
REPO = f"{LOGIN}/shore-notes"

Handler = Callable[[httpx.Request], httpx.Response]


class FakeGitHub:
    """A scripted github.com and api.github.com, keyed by method and full URL.

    A route holds a fixed JSON answer or a handler function. A request without a
    route fails the test.
    """

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], Handler] = {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def on(self, method: str, url: str, body: Any, *, status: int = 200) -> None:
        self._routes[(method, url)] = lambda request: httpx.Response(status, json=body)

    def on_call(self, method: str, url: str, handler: Handler) -> None:
        self._routes[(method, url)] = handler

    def user(self) -> None:
        """Script ``GET /user`` with the synthetic user fixture."""
        self.on("GET", f"{API}/user", json.loads((FIXTURES / "github" / "user.json").read_text()))

    def form(self, index: int) -> dict[str, str]:
        """The form fields of a recorded request."""
        return dict(httpx.QueryParams(self.requests[index].content.decode()))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self._routes.get((request.method, str(request.url)))
        if handler is None:
            raise AssertionError(f"Unscripted request: {request.method} {request.url}")
        return handler(request)


class FakeProvider:
    """A scripted Provider: one reply per (method, path); unscripted requests get 418."""

    def __init__(self) -> None:
        self._replies: dict[tuple[str, str], tuple[int, Any]] = {}
        self.requests: list[httpx2.Request] = []
        self.transport = httpx2.MockTransport(self._handle)

    def reply(self, method: str, path: str, body: Any, *, status: int = 200) -> None:
        self._replies[(method, path)] = (status, body)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        reply = self._replies.get((request.method, request.url.path))
        if reply is None:
            return httpx2.Response(418, text=f"unexpected {request.method} {request.url}")
        status, body = reply
        return httpx2.Response(status, json=body)


class FakeClock:
    """A clock that starts at the real time and moves only when a test advances it."""

    def __init__(self) -> None:
        self.now = utc_now()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def provider_api() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_ctx(
    settings: Settings, github: FakeGitHub, provider_api: FakeProvider, clock: FakeClock
) -> Iterator[Callable[..., AppContext]]:
    """Build an ``AppContext`` on the test database, with settings changed by keyword."""
    contexts: list[AppContext] = []

    def make(**changes: Any) -> AppContext:
        values: dict[str, Any] = {
            "github_client_id": "Iv1.synthetic0client",
            "github_app_slug": "synthetic-app",
            **changes,
        }
        context = AppContext.create(
            settings.model_copy(update=values),
            github_transport=github.transport,
            provider_transport=provider_api.transport,
            clock=clock,
        )
        contexts.append(context)
        return context

    yield make
    for context in contexts:
        context.close()


@pytest.fixture
def ctx(make_ctx: Callable[..., AppContext]) -> AppContext:
    return make_ctx()


@pytest.fixture
def connected(ctx: AppContext, github: FakeGitHub) -> GitHubAccountView:
    """GitHub connected with a personal access token."""
    github.user()
    account = connect_github_pat(ctx, SecretStr(PAT))
    github.requests.clear()
    return account


@dataclass(frozen=True)
class BrainFixture:
    id: str
    root: Path
    remote: Path


AddSourceRow = Callable[..., None]
InsertBrain = Callable[..., str]


@pytest.fixture
def require_lfs() -> None:
    """Skip the test when git-lfs is not installed."""
    try:
        installed = subprocess.run(["git", "lfs", "version"], capture_output=True).returncode == 0
    except OSError:
        installed = False
    if not installed:
        pytest.skip("git-lfs is not installed")


@pytest.fixture
def add_source_row(ctx: AppContext) -> AddSourceRow:
    """Insert a Source row, as the worker does after ``source.add``.

    Call it as ``add_source_row(brain_id, slug, folder, sha256=..., title=...)``.
    """

    def add(brain_id: str, slug: str, folder: str, *, sha256: str, title: str) -> None:
        folder_path = PurePosixPath(folder) if folder else PurePosixPath()
        with ctx.sessions.begin() as session:
            session.add(
                SourceRow(
                    brain_id=brain_id,
                    slug=slug,
                    folder=folder,
                    path=BrainLayout().source_file_path(folder_path, slug, "txt").as_posix(),
                    sha256=sha256,
                    title=title,
                    media_type="text/plain",
                    size_bytes=100,
                    status=SourceStatus.INGESTED,
                )
            )

    return add


@pytest.fixture
def insert_brain(ctx: AppContext) -> InsertBrain:
    """Insert a ready Brain row without a clone: ``insert_brain("owner/repo")`` gives its id."""

    def insert(repo_full_name: str, name: str = "Shore Notes") -> str:
        with ctx.sessions.begin() as session:
            row = BrainRow(name=name, repo_full_name=repo_full_name, status=BrainStatus.READY)
            session.add(row)
            session.flush()
            return row.id

    return insert


@pytest.fixture
def brain(
    ctx: AppContext, bare_remote: Path, insert_brain: InsertBrain, add_source_row: AddSourceRow
) -> BrainFixture:
    """A ready Brain ``test-user/shore-notes``: a clone of a local bare remote.

    The content is tests/fixtures/ingest/brain, scaffolded, committed, and pushed.
    Sources (Folder ``nature``): ``tide-pools`` and ``coastal-birds``. The search
    index has the Brain's pages.
    """
    brain_id = insert_brain(REPO)
    root = ctx.clone_path(brain_id)
    repo = GitRepo(root, GitIdentity(name=LOGIN, email=f"{USER_ID}+{LOGIN}@example.com"), None)
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
    for slug, title in (
        ("tide-pools", "Tide Pools of the Example Coast"),
        ("coastal-birds", "Coastal Birds"),
    ):
        source_file = root / "sources" / "nature" / slug / f"{slug}.txt"
        digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
        add_source_row(brain_id, slug, "nature", sha256=digest, title=title)
    ctx.search.rebuild(brain_id, root)
    return BrainFixture(id=brain_id, root=root, remote=bare_remote)
