"""Brain use cases: create (job payloads), disconnect (keeps the remote), sync, LFS usage."""

from __future__ import annotations

import io
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event

from second_brain_builder.domain import BrainStatus, JobKind, JobStatus
from second_brain_builder.services import (
    AppContext,
    BrainCreate,
    ConflictError,
    NotConnectedError,
    NotFoundError,
    PublicRepoError,
    add_source,
    create_brain,
    disconnect_brain,
    get_brain,
    get_lfs_usage,
    list_jobs,
    resolve_brain,
    search,
    sync_brain,
)
from second_brain_builder.store import Job

if TYPE_CHECKING:
    from tests.services.conftest import BrainFixture, FakeGitHub, InsertBrain

API = "https://api.github.com"


def repo_body(full_name: str, *, private: bool) -> dict[str, Any]:
    return {
        "full_name": full_name,
        "private": private,
        "clone_url": f"https://github.com/{full_name}.git",
        "default_branch": "main",
    }


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


# --- create ---------------------------------------------------------------------------


def test_new_repo_brain_enqueues_brain_create_with_create_repo(
    ctx: AppContext, github: FakeGitHub, connected: Any
) -> None:
    github.on("GET", f"{API}/repos/test-user/shore-notes", {"message": "Not Found"}, status=404)

    created = create_brain(
        ctx, BrainCreate(name="  Shore   Notes ", language="pt-BR", new_repo_name="shore-notes")
    )

    brain = created.brain
    assert (brain.name, brain.language, brain.status) == (
        "Shore Notes",
        "pt-BR",
        BrainStatus.CREATING,
    )
    assert (brain.repo_full_name, brain.slug) == ("test-user/shore-notes", "shore-notes")
    assert created.job.kind is JobKind.BRAIN_CREATE
    assert created.job.payload == {
        "brain_id": brain.id,
        "repo_full_name": "test-user/shore-notes",
        "create_repo": True,
    }


def test_new_repo_name_that_exists_on_github_is_a_conflict(
    ctx: AppContext, github: FakeGitHub, connected: Any
) -> None:
    github.on("GET", f"{API}/repos/test-user/notes", repo_body("test-user/notes", private=True))

    with pytest.raises(ConflictError, match="exists already"):
        create_brain(ctx, BrainCreate(name="Notes", new_repo_name="notes"))
    assert list_jobs(ctx) == []


def test_existing_public_repo_needs_confirmation(
    ctx: AppContext, github: FakeGitHub, connected: Any
) -> None:
    github.on(
        "GET", f"{API}/repos/example-org/handbook", repo_body("Example-Org/Handbook", private=False)
    )

    with pytest.raises(PublicRepoError) as caught:
        create_brain(ctx, BrainCreate(name="Handbook", existing_repo="example-org/handbook"))
    assert caught.value.repo_full_name == "Example-Org/Handbook"
    assert list_jobs(ctx) == []

    created = create_brain(
        ctx,
        BrainCreate(name="Handbook", existing_repo="example-org/handbook", confirm_public=True),
    )

    assert created.job.payload == {
        "brain_id": created.brain.id,
        "repo_full_name": "Example-Org/Handbook",
        "create_repo": False,
    }


def test_a_repository_holds_at_most_one_brain(
    ctx: AppContext, github: FakeGitHub, connected: Any, insert_brain: InsertBrain
) -> None:
    insert_brain("test-user/notes")
    github.on("GET", f"{API}/repos/test-user/NOTES", repo_body("test-user/notes", private=True))

    with pytest.raises(ConflictError, match="uses the repository"):
        create_brain(ctx, BrainCreate(name="Notes", existing_repo="test-user/NOTES"))


def test_creating_a_brain_needs_github(ctx: AppContext) -> None:
    with pytest.raises(NotConnectedError):
        create_brain(ctx, BrainCreate(name="Notes", new_repo_name="notes"))


# --- disconnect -----------------------------------------------------------------------


def test_disconnect_removes_the_clone_and_index_and_keeps_the_remote(
    ctx: AppContext, brain: BrainFixture
) -> None:
    head = git(brain.remote, "rev-parse", "main")
    sync_brain(ctx, brain.id)
    assert search(ctx, brain.id, "heron")

    disconnect_brain(ctx, brain.id)

    assert not brain.root.exists()
    assert git(brain.remote, "rev-parse", "main") == head
    assert ctx.search.query(brain.id, "heron") == []
    assert list_jobs(ctx, brain.id) == []
    with pytest.raises(NotFoundError):
        get_brain(ctx, brain.id)


def test_disconnect_waits_for_a_running_job(ctx: AppContext, brain: BrainFixture) -> None:
    sync_brain(ctx, brain.id)
    ctx.jobs.claim("worker-1")

    with pytest.raises(ConflictError, match="running"):
        disconnect_brain(ctx, brain.id)
    assert brain.root.is_dir()


def test_disconnect_removes_the_staged_uploads_of_the_brain_only(
    ctx: AppContext, brain: BrainFixture, insert_brain: InsertBrain
) -> None:
    failed = add_source(ctx, brain.id, "a.txt", io.BytesIO(b"Notes that did not convert.\n"))
    ctx.jobs.claim("worker-1")
    ctx.jobs.fail(failed.job.id, "conversion failed", worker_id="worker-1")  # keeps the upload
    queued = add_source(ctx, brain.id, "b.txt", io.BytesIO(b"Notes in the queue.\n"))
    other_brain = insert_brain("test-user/other-notes", name="Other Notes")
    other = add_source(ctx, other_brain, "c.txt", io.BytesIO(b"Notes of another Brain.\n"))

    disconnect_brain(ctx, brain.id)

    for result in (failed, queued):
        assert not Path(result.job.payload["upload_path"]).parent.exists()
    assert Path(other.job.payload["upload_path"]).is_file()


def test_a_worker_cannot_start_a_job_of_a_brain_that_is_being_disconnected(
    ctx: AppContext, brain: BrainFixture
) -> None:
    added = add_source(ctx, brain.id, "notes.txt", io.BytesIO(b"Notes in the queue.\n"))
    claims: list[Job | None] = []
    workers: list[threading.Thread] = []

    def claim_during_disconnect(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        # When disconnect_brain first reads the jobs table, a worker tries to start the job.
        if workers or "FROM jobs" not in statement:
            return
        worker = threading.Thread(target=lambda: claims.append(ctx.jobs.claim("worker-1")))
        workers.append(worker)
        worker.start()
        worker.join(timeout=0.5)  # a claim that does not wait for the write lock ends sooner

    event.listen(ctx.engine, "after_cursor_execute", claim_during_disconnect)
    try:
        disconnect_brain(ctx, brain.id)
    finally:
        event.remove(ctx.engine, "after_cursor_execute", claim_during_disconnect)
        for worker in workers:
            worker.join()

    # The claim waited until the Brain and its jobs were gone: no job runs in a removed clone.
    assert claims == [None]
    assert not Path(added.job.payload["upload_path"]).parent.exists()
    assert not brain.root.exists()


# --- sync, lookup, and LFS usage ------------------------------------------------------


def test_sync_enqueues_brain_sync(ctx: AppContext, brain: BrainFixture) -> None:
    job = sync_brain(ctx, brain.id)

    assert (job.kind, job.status, job.payload) == (
        JobKind.BRAIN_SYNC,
        JobStatus.QUEUED,
        {"brain_id": brain.id},
    )


def test_brains_resolve_by_id_slug_name_or_repository(
    ctx: AppContext, insert_brain: InsertBrain
) -> None:
    brain_id = insert_brain("test-user/Shore-Notes", name="Shore Notes")
    insert_brain("test-user/other", name="Other")

    for ref in (brain_id, "shore-notes", "shore notes", "TEST-USER/shore-notes"):
        assert resolve_brain(ctx, ref).id == brain_id
    with pytest.raises(NotFoundError):
        resolve_brain(ctx, "missing")


def test_lfs_usage_counts_the_sources_in_git_lfs(
    ctx: AppContext, brain: BrainFixture, require_lfs: None
) -> None:
    sources = sorted((brain.root / "sources").glob("*/*/*.txt"))
    expected = sum(path.stat().st_size for path in sources)

    usage = get_lfs_usage(ctx, brain.id)

    assert usage.brain_bytes == expected > 0
    assert (usage.owner, usage.owner_bytes) == ("test-user", expected)
    assert usage.over_quota is False
