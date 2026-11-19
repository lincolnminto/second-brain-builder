"""Fixtures for the worker tests.

The worker runs for real: a SQLite database and search index in tmp_path, real git
against local bare remotes (the clone URL that the scripted GitHub API gives is a
local path), real Docling on small HTML and Markdown Sources, and a scripted
``ChatModel`` for Ingest, passed through ``AppContext.chat_model_factory``.

Test modules get the helpers through fixtures: with the importlib import mode,
they cannot import from this file.
"""

from __future__ import annotations

import base64
import io
import itertools
import json
import shutil
import struct
import subprocess
import zlib
from collections import deque
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from second_brain_builder.config import Settings
from second_brain_builder.domain import JobStatus, TaskMode
from second_brain_builder.okf import Frontmatter, Page, Stamp, write_page
from second_brain_builder.providers import (
    ChatResponse,
    Message,
    ProviderConfig,
    ProviderKind,
    ToolCall,
    ToolSpec,
    Usage,
)
from second_brain_builder.services import (
    AddSourceResult,
    AppContext,
    BrainCreate,
    ProviderInput,
    TaskSettingInput,
    TaskSettingsInput,
    connect_github_pat,
    create_brain,
    create_provider,
    get_brain,
    put_task_settings,
)
from second_brain_builder.services import add_source as stage_source
from second_brain_builder.store import Job, JobEvent
from second_brain_builder.worker import run_once

FIXTURES = Path(__file__).parents[1] / "fixtures"
API = "https://api.github.com"
# Synthetic value, built from parts so secret scanners do not flag a test literal.
PAT = "ghp_" + "syntheticWorkerToken0000000000000001"
LOGIN = "test-user"
USER_ID = 1000001
NOREPLY = f"{USER_ID}+{LOGIN}@users.noreply.github.com"
WORKER_ID = "test-worker"


# --- GitHub ------------------------------------------------------------------------------


class FakeGitHub:
    """A scripted api.github.com with repositories whose clone URLs are local bare repos."""

    def __init__(self, remotes_dir: Path) -> None:
        self.remotes_dir = remotes_dir
        self.repos: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def add_repo(self, full_name: str, *, private: bool = True) -> Path:
        """Make a repository with an empty bare remote, and return the remote's path."""
        remote = self.remotes_dir / f"{full_name.replace('/', '__')}.git"
        subprocess.run(
            ["git", "init", "--quiet", "--bare", "--initial-branch=main", str(remote)],
            check=True,
            capture_output=True,
        )
        body = json.loads((FIXTURES / "github" / "repository.json").read_text())
        body.update(
            {
                "name": full_name.partition("/")[2],
                "full_name": full_name,
                "private": private,
                "clone_url": str(remote),
            }
        )
        self.repos[full_name.lower()] = body
        return remote

    def remote(self, full_name: str) -> Path:
        return Path(self.repos[full_name.lower()]["clone_url"])

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/user":
            return httpx.Response(
                200, json=json.loads((FIXTURES / "github" / "user.json").read_text())
            )
        if request.method == "GET" and path.startswith("/repos/"):
            repo = self.repos.get(path.removeprefix("/repos/").lower())
            if repo is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=repo)
        if request.method == "POST" and path == "/user/repos":
            name = json.loads(request.content)["name"]
            full_name = f"{LOGIN}/{name}"
            if full_name.lower() in self.repos:
                return httpx.Response(422, json={"message": "name already exists on this account"})
            self.add_repo(full_name)
            return httpx.Response(201, json=self.repos[full_name.lower()])
        raise AssertionError(f"Unscripted request: {request.method} {request.url}")


# --- the scripted model ----------------------------------------------------------------


class ScriptedChatModel:
    """A ChatModel that replays planned answers in order.

    ``before`` runs before the answer to each request, with the request number
    (1-based): a test uses it to change the remote while the job runs. An answer
    can be an exception, which the request then raises.
    """

    def __init__(
        self, *answers: ChatResponse | Exception, before: Callable[[int], None] | None = None
    ) -> None:
        self._answers = deque(answers)
        self._before = before
        self.requests = 0

    def chat(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> ChatResponse:
        self.requests += 1
        if self._before is not None:
            self._before(self.requests)
        if not self._answers:
            raise AssertionError("the model got more requests than the script has answers")
        answer = self._answers.popleft()
        if isinstance(answer, Exception):
            raise answer
        return answer


class ScriptedModels:
    """The ``chat_model_factory`` of the test context: each Ingest gets the next model."""

    def __init__(self) -> None:
        self.queue: deque[ScriptedChatModel] = deque()
        self.configs: list[tuple[ProviderConfig, str]] = []

    def add(self, model: ScriptedChatModel) -> ScriptedChatModel:
        self.queue.append(model)
        return model

    def factory(self, config: ProviderConfig, model: str) -> ScriptedChatModel:
        self.configs.append((config, model))
        if not self.queue:
            raise AssertionError("an Ingest started, but no scripted model is left")
        return self.queue.popleft()


_call_ids = itertools.count(1)


def answer(*calls: ToolCall) -> ChatResponse:
    return ChatResponse(
        text=None,
        tool_calls=list(calls),
        usage=Usage(input_tokens=100, output_tokens=50),
        stop_reason="tool_use",
    )


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"call-{next(_call_ids)}", name=name, arguments=arguments)


def write_wiki_page(
    page_type: str, slug: str, title: str, body: str, description: str = "A page."
) -> ToolCall:
    directory = {"summary": "summaries", "entity": "entities", "concept": "concepts"}[page_type]
    return call(
        "write_page",
        path=f"wiki/{directory}/{slug}.md",
        type=page_type,
        title=title,
        description=description,
        tags=["test"],
        body=body,
    )


def ingest_answers(
    source_id: str, title: str, entities: list[tuple[str, str]]
) -> list[ChatResponse]:
    """A one-part Ingest: the summary page, one entity page each, then finish."""
    links = ", ".join(f"[{name}](/wiki/entities/{slug}.md)" for slug, name in entities)
    summary_body = f"# {title}\n\nThe Source describes {links or 'the coast'}.[^{source_id}]\n"
    calls = [write_wiki_page("summary", source_id, title, summary_body, f"Summary of {title}.")]
    for slug, name in entities:
        body = f"# {name}\n\n{name} lives in the rock pools.[^{source_id}]\n"
        calls.append(write_wiki_page("entity", slug, name, body, f"{name}, an animal."))
    return [answer(*calls), answer(call("finish", note="Done."))]


# --- synthetic Sources -------------------------------------------------------------------


def png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A solid-color PNG, encoded without Pillow."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


TIDE_POOLS_HTML = (
    "<!DOCTYPE html><html><head><title>Tide Pools of the Example Coast</title></head><body>"
    "<h1>Tide Pools of the Example Coast</h1>"
    "<p>Rock pools of the example coast hold the ochre sea star and hermit crabs.</p>"
    f'<p><img src="data:image/png;base64,{base64.b64encode(png(8, 8, (20, 90, 160))).decode()}"'
    ' alt="A rock pool"></p>'
    "</body></html>"
).encode()

COASTAL_BIRDS_MD = (
    b"# Coastal Birds\n\nGrey herons wade in the shallows at low tide.\n\n"
    b"## Gulls\n\nGulls follow the fishing boats.\n"
)


# --- the harness ----------------------------------------------------------------------------


class Harness:
    """Drives the services and the worker the way the app and the worker process do."""

    pat = PAT
    noreply = NOREPLY
    worker_id = WORKER_ID
    tide_pools_html = TIDE_POOLS_HTML
    coastal_birds_md = COASTAL_BIRDS_MD
    answer = staticmethod(answer)
    call = staticmethod(call)
    write_wiki_page = staticmethod(write_wiki_page)

    def __init__(self, ctx: AppContext, github: FakeGitHub, models: ScriptedModels, tmp: Path):
        self.ctx = ctx
        self.github = github
        self.models = models
        self.tmp = tmp
        self._clones = itertools.count(1)

    # the ingest model

    def script(
        self, *answers: ChatResponse | Exception, before: Callable[[int], None] | None = None
    ) -> ScriptedChatModel:
        """The model of the next Ingest: it replays ``answers``."""
        return self.models.add(ScriptedChatModel(*answers, before=before))

    def script_ingest(
        self,
        source_id: str,
        title: str,
        entities: list[tuple[str, str]],
        before: Callable[[int], None] | None = None,
    ) -> ScriptedChatModel:
        """The model of the next Ingest writes the summary and one page per entity."""
        return self.script(*ingest_answers(source_id, title, entities), before=before)

    # jobs

    def run(self) -> Job:
        """Run the next job; fail the test when no job could start."""
        job = run_once(self.ctx, WORKER_ID)
        assert job is not None, "no job could start"
        return job

    def run_ok(self) -> Job:
        """Run the next job and check that it succeeded."""
        job = self.run()
        assert job.status is JobStatus.SUCCEEDED, job.error
        return job

    def events(self, job_id: int, kind: str | None = None) -> list[JobEvent]:
        events = [event for event in self.ctx.jobs.events_since(0) if event.job_id == job_id]
        return [event for event in events if kind is None or event.kind == kind]

    def queued(self) -> list[Job]:
        return self.ctx.jobs.list_jobs(statuses=[JobStatus.QUEUED])

    # Brains and Sources

    def create_brain(self, repo_name: str = "shore-notes", name: str = "Shore Notes") -> str:
        """A new Brain in a new private repository, created by the worker."""
        created = create_brain(self.ctx, BrainCreate(name=name, new_repo_name=repo_name))
        self.run_ok()
        return created.brain.id

    def repo(self, brain_id: str) -> str:
        return get_brain(self.ctx, brain_id).repo_full_name

    def remote(self, brain_id: str) -> Path:
        return self.github.remote(self.repo(brain_id))

    def clone_path(self, brain_id: str) -> Path:
        return self.ctx.clone_path(brain_id)

    def add_file(
        self, brain_id: str, filename: str, data: bytes, folder: str = ""
    ) -> AddSourceResult:
        """Stage an upload and enqueue ``source.add``, as the API does."""
        return stage_source(self.ctx, brain_id, filename, io.BytesIO(data), folder)

    def enable_ingest(self, api_key: str = "sk-syntheticProviderKey000000000001") -> None:
        """An ingest Provider and model, so ``source.add`` enqueues ``source.ingest``."""
        provider = create_provider(
            self.ctx,
            ProviderInput(
                name="Example LLM",
                kind=ProviderKind.OPENAI,
                base_url="https://llm.example.com/v1",
                api_key=SecretStr(api_key),
            ),
        )
        put_task_settings(
            self.ctx,
            TaskSettingsInput(
                ingest=TaskSettingInput(
                    mode=TaskMode.PROVIDER, provider_id=provider.id, model="test-model"
                )
            ),
        )

    # git

    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def fresh_clone(self, brain_id: str) -> Path:
        """A new plain clone of the Brain's remote. Git LFS files stay pointers."""
        target = self.tmp / f"fresh-{next(self._clones)}"
        subprocess.run(
            ["git", "clone", "--quiet", str(self.remote(brain_id)), str(target)],
            check=True,
            capture_output=True,
        )
        return target

    def remote_head(self, brain_id: str) -> str:
        return self.git(self.remote(brain_id), "rev-parse", "main")

    def commit_messages(self, brain_id: str) -> list[str]:
        return self.git(self.remote(brain_id), "log", "--format=%s", "main").splitlines()

    def seed_source(
        self, brain_id: str, slug: str, title: str, folder: str = "nature", *, use_lfs: bool = True
    ) -> PurePosixPath:
        """Push a Source and its Canonical Document from another clone, without Conversion.

        With ``use_lfs`` the Source goes to Git LFS, as the worker stores it; without
        it, it goes to plain git, as a GitHub web upload does. Run ``brain.sync``
        afterwards to see it in the worker's clone and the rows.
        """
        clone = self.fresh_clone(brain_id)
        if use_lfs and lfs_installed():
            self.git(clone, "lfs", "install", "--local")
        directory = PurePosixPath("sources", *PurePosixPath(folder).parts, slug)
        (clone / directory).mkdir(parents=True)
        (clone / directory / f"{slug}.txt").write_text(f"{title}. Synthetic text.\n")
        canonical = directory / f"{slug}.md"
        write_page(
            clone,
            Page(
                path=canonical,
                frontmatter=Frontmatter(
                    type="source",
                    title=title,
                    description="Plain text.",
                    resource=f"./{slug}.txt",
                    generated=Stamp(
                        by="process:sbb-conversion",
                        at=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
                    ),
                    extensions={
                        "x-sbb-source": {
                            "original_filename": f"{title}.txt",
                            "sha256": "0" * 64,
                            "media_type": "text/plain",
                            "size_bytes": 40,
                        }
                    },
                ),
                body=f"# {title}\n\n{title} is a synthetic Source about the coast.\n",
            ),
        )
        self.git(clone, "add", "--all")
        self.git(clone, "commit", "--quiet", "--message", f"Seed {slug}")
        self.git(clone, "push", "--quiet", "origin", "HEAD:main")
        shutil.rmtree(clone)
        return canonical


@pytest.fixture
def github(tmp_path: Path) -> FakeGitHub:
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    return FakeGitHub(remotes)


@pytest.fixture
def models() -> ScriptedModels:
    return ScriptedModels()


@pytest.fixture
def ctx(settings: Settings, github: FakeGitHub, models: ScriptedModels) -> Iterator[AppContext]:
    context = AppContext.create(
        settings, github_transport=github.transport, chat_model_factory=models.factory
    )
    yield context
    context.close()


@pytest.fixture
def harness(ctx: AppContext, github: FakeGitHub, models: ScriptedModels, tmp_path: Path) -> Harness:
    """A connected GitHub account (a personal access token) and the worker harness."""
    connect_github_pat(ctx, SecretStr(PAT))
    return Harness(ctx, github, models, tmp_path)


def lfs_installed() -> bool:
    try:
        return subprocess.run(["git", "lfs", "version"], capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.fixture
def lfs() -> bool:
    """True when git-lfs is installed. Tests check LFS storage only then."""
    return lfs_installed()
