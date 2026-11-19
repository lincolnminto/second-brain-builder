"""The runner: recovery from crashes, failures, and push conflicts; one job per Brain at a time.

Git, the job queue, and the database are real. Failures come from real causes: a
remote that rejects pushes, a Provider error from the scripted model, and commits
that another clone pushes while a job runs.
"""

from __future__ import annotations

import io
import itertools
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.domain import JobKind, JobStatus, SourceStatus
from second_brain_builder.okf import LogKind, log_entry, prepend_log, validate_bundle
from second_brain_builder.providers import ProviderConnectionError
from second_brain_builder.services import (
    AnswerInput,
    get_worker_status,
    list_jobs,
    list_sources,
    retry_job,
    save_answer,
    search,
    sync_brain,
)
from second_brain_builder.services.uploads import stage_upload
from second_brain_builder.worker import Worker
from second_brain_builder.worker.context import JobRun

SEA_STAR = PurePosixPath("wiki/entities/sea-star.md")
SUMMARY = PurePosixPath("wiki/summaries/tide-pools.md")


def errors(root: Path) -> list[str]:
    return [f"{i.path}: {i.message}" for i in validate_bundle(root) if i.severity == "error"]


def is_clean(harness, brain_id: str) -> bool:
    """The clone has no changes and its HEAD is the remote's main."""
    clone = harness.clone_path(brain_id)
    head = harness.git(clone, "rev-parse", "HEAD")
    status = harness.git(clone, "status", "--porcelain", "--untracked-files=all")
    return status == "" and head == harness.remote_head(brain_id)


@pytest.fixture
def seeded(harness) -> str:
    """A Brain with the Source tide-pools, pushed from another clone, and synced."""
    brain_id = harness.create_brain()
    harness.seed_source(brain_id, "tide-pools", "Tide Pools")
    sync_brain(harness.ctx, brain_id)
    harness.run_ok()
    return brain_id


def push_from_other_clone(harness, brain_id: str, change) -> None:
    """Clone the remote elsewhere, let ``change(root)`` edit it, commit, and push."""
    other = harness.fresh_clone(brain_id)
    change(other)
    harness.git(other, "add", "--all")
    harness.git(other, "commit", "--quiet", "--message", "Edit on another computer")
    harness.git(other, "push", "--quiet", "origin", "HEAD:main")


# --- crashes ---------------------------------------------------------------------------


def test_a_job_that_a_crashed_worker_left_running_runs_again_from_the_remote_state(harness, seeded):
    save_answer(
        harness.ctx,
        seeded,
        AnswerInput(
            title="Pools",
            body="Pools hold sea stars.[^tide-pools]\n",
            source_ids=["tide-pools"],
        ),
    )
    crashed = harness.ctx.jobs.claim("crashed-worker")
    assert crashed is not None
    # The crashed attempt had written and committed the page, but not pushed it.
    clone = harness.clone_path(seeded)
    (clone / "wiki" / "syntheses").mkdir(parents=True)
    (clone / "wiki" / "syntheses" / "pools.md").write_text("half a page\n")
    harness.git(clone, "add", "--all")
    harness.git(clone, "commit", "--quiet", "--message", "Save answer: Pools")
    (clone / "index.md").write_text("damaged\n")

    worker = Worker(harness.ctx, worker_id="new-worker")
    assert worker.start() == 1
    job = worker.run_once()

    assert job is not None and job.id == crashed.id
    assert (job.status, job.attempts, job.worker_id) == (JobStatus.SUCCEEDED, 1, "new-worker")
    assert is_clean(harness, seeded)
    fresh = harness.fresh_clone(seeded)
    assert sorted(p.name for p in (fresh / "wiki" / "syntheses").iterdir()) == ["pools.md"]
    assert "half a page" not in (fresh / "wiki" / "syntheses" / "pools.md").read_text()
    assert errors(fresh) == []


def test_a_source_add_whose_earlier_attempt_pushed_does_not_add_the_source_twice(harness):
    brain_id = harness.create_brain()
    harness.add_file(brain_id, "Coastal Birds.md", harness.coastal_birds_md)
    first = harness.run_ok()
    head = harness.remote_head(brain_id)
    # The same job again, as after a crash between the push and the end of the job.
    staged = stage_upload(harness.ctx, "Coastal Birds.md", io.BytesIO(harness.coastal_birds_md))
    payload = {**first.payload, "upload_path": str(staged.path)}
    harness.ctx.jobs.enqueue(JobKind.SOURCE_ADD, brain_id, payload)

    harness.run_ok()

    assert harness.remote_head(brain_id) == head
    assert [row.slug for row in list_sources(harness.ctx, brain_id)] == ["coastal-birds"]
    assert not staged.path.exists()


def test_source_add_recovers_after_the_upload_was_deleted_before_completion(harness):
    brain_id = harness.create_brain()
    harness.add_file(brain_id, "Coastal Birds.md", harness.coastal_birds_md)
    first = harness.run_ok()
    head = harness.remote_head(brain_id)
    assert not Path(first.payload["upload_path"]).exists()
    harness.enable_ingest()
    harness.ctx.jobs.enqueue(JobKind.SOURCE_ADD, brain_id, first.payload)

    harness.run_ok()

    assert harness.remote_head(brain_id) == head
    assert [row.slug for row in list_sources(harness.ctx, brain_id)] == ["coastal-birds"]
    assert [job.kind for job in harness.queued()] == [JobKind.SOURCE_INGEST]


def test_three_crashed_attempts_fail_instead_of_requeueing(harness, seeded):
    queued = sync_brain(harness.ctx, seeded)
    worker = Worker(harness.ctx, worker_id="recovery")
    for attempt in range(3):
        claimed = harness.ctx.jobs.claim("crashed")
        assert claimed is not None and claimed.id == queued.id
        assert worker.start() == (1 if attempt < 2 else 0)
    failed = harness.ctx.jobs.get(queued.id)
    assert failed.status is JobStatus.FAILED
    assert failed.attempts == 3
    assert worker.run_once() is None


# --- failures ---------------------------------------------------------------------------


def test_a_rejected_push_fails_the_job_and_leaves_the_clone_at_the_remote_state(harness):
    brain_id = harness.create_brain()
    hook = harness.remote(brain_id) / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'pushes are paused' >&2\nexit 1\n")
    hook.chmod(0o755)
    head = harness.remote_head(brain_id)
    harness.add_file(brain_id, "Coastal Birds.md", harness.coastal_birds_md)

    failed = harness.run()

    assert failed.status is JobStatus.FAILED
    assert failed.error is not None and "pushes are paused" in failed.error
    assert harness.remote_head(brain_id) == head
    assert is_clean(harness, brain_id)  # the commit and the converted files are gone
    assert list_sources(harness.ctx, brain_id) == []
    assert any(harness.ctx.settings.uploads_dir.iterdir())  # kept for a retry

    hook.unlink()
    retry_job(harness.ctx, failed.id)
    harness.run_ok()

    assert [row.slug for row in list_sources(harness.ctx, brain_id)] == ["coastal-birds"]
    assert not any(harness.ctx.settings.uploads_dir.iterdir())
    assert is_clean(harness, brain_id)


def test_a_provider_error_fails_the_ingest_without_tokens_and_cleans_the_clone(harness, seeded):
    harness.enable_ingest()
    harness.script(
        harness.answer(
            harness.write_wiki_page(
                "entity", "sea-star", "Sea Star", "Sea stars live in pools.[^tide-pools]\n"
            )
        ),
        ProviderConnectionError(f"connection reset; the request had token={harness.pat}"),
    )
    harness.ctx.jobs.enqueue(
        JobKind.SOURCE_INGEST, seeded, {"brain_id": seeded, "source_slug": "tide-pools"}
    )

    job = harness.run()

    assert job.status is JobStatus.FAILED
    assert job.error is not None
    assert job.error.startswith("The LLM Provider failed: connection reset")
    assert harness.pat not in job.error
    assert "***" in job.error
    assert is_clean(harness, seeded)
    assert not (harness.clone_path(seeded) / SEA_STAR).exists()
    assert search(harness.ctx, seeded, "Sea Star", types=["entity"]) == []
    assert list_sources(harness.ctx, seeded)[0].status is SourceStatus.FAILED


def test_unexpected_model_factory_errors_never_log_the_provider_key(harness, seeded, caplog):
    key = "synthetic-provider-secret-without-prefix"
    harness.enable_ingest(api_key=key)

    def fail(config, model):
        raise RuntimeError(f"Cannot build the model with {key}")

    harness.ctx.chat_model_factory = fail
    harness.ctx.jobs.enqueue(
        JobKind.SOURCE_INGEST, seeded, {"brain_id": seeded, "source_slug": "tide-pools"}
    )

    failed = harness.run()

    assert failed.status is JobStatus.FAILED
    assert key not in (failed.error or "")
    assert key not in caplog.text
    assert "Cannot build the model with ***" in caplog.text


def test_cleanup_errors_are_scrubbed_before_logging(harness, seeded, caplog):
    queued = sync_brain(harness.ctx, seeded)
    run = JobRun(harness.ctx, queued, "cleanup", secrets=[harness.pat])

    def fail():
        raise RuntimeError(f"Cleanup failed with {harness.pat}")

    run.on_failure(fail)
    run.clean_up_after_failure()

    assert harness.pat not in caplog.text
    assert "Cleanup failed with ***" in caplog.text


def test_zero_concurrency_is_rejected(ctx):
    with pytest.raises(ValueError, match="concurrency must be 1 or more"):
        Worker(ctx, concurrency=0)


def test_a_source_pushed_without_git_lfs_is_moved_to_lfs_instead_of_blocking_jobs(harness, lfs):
    if not lfs:
        pytest.skip("git-lfs is not installed")
    brain_id = harness.create_brain()
    harness.seed_source(brain_id, "tide-pools", "Tide Pools", use_lfs=False)
    sync_brain(harness.ctx, brain_id)
    harness.run_ok()
    save_answer(
        harness.ctx,
        brain_id,
        AnswerInput(title="Pools", body="Pools.[^tide-pools]\n", source_ids=["tide-pools"]),
    )

    harness.run_ok()

    assert harness.commit_messages(brain_id)[:2] == [
        "Save answer: Pools",
        "Store Sources in Git LFS",
    ]
    assert is_clean(harness, brain_id)
    fresh = harness.fresh_clone(brain_id)
    stored = (fresh / "sources/nature/tide-pools/tide-pools.txt").read_bytes()
    assert stored.startswith(b"version https://git-lfs.github.com/spec/v1\n")


# --- push conflicts -------------------------------------------------------------------------


def test_a_conflict_in_index_and_log_with_another_clone_is_resolved(harness, seeded):
    def colleague_pushes(request: int) -> None:
        if request != 1:
            return

        def change(root: Path) -> None:
            index = (root / "index.md").read_text()
            (root / "index.md").write_text(
                index.replace(
                    "Sources and the wiki pages written from them.", "Notes from the shore."
                )
            )
            log = (root / "log.md").read_text()
            entry = log_entry(LogKind.ANSWER, "Field trip plan")
            (root / "log.md").write_text(prepend_log(log, harness.ctx.clock().date(), [entry]))
            (root / "README.md").write_text("# Shore Notes\n")

        push_from_other_clone(harness, seeded, change)

    harness.enable_ingest()
    harness.script_ingest(
        "tide-pools", "Tide Pools", [("sea-star", "Ochre Sea Star")], colleague_pushes
    )
    harness.ctx.jobs.enqueue(
        JobKind.SOURCE_INGEST, seeded, {"brain_id": seeded, "source_slug": "tide-pools"}
    )

    harness.run_ok()

    assert harness.commit_messages(seeded)[:2] == [
        "Ingest: Tide Pools (2 pages)",
        "Edit on another computer",
    ]
    assert is_clean(harness, seeded)
    fresh = harness.fresh_clone(seeded)
    assert (fresh / "README.md").is_file()
    index = (fresh / "index.md").read_text()
    assert "description: Notes from the shore." in index  # the colleague's description
    assert f"(/{SEA_STAR})" in index and f"(/{SUMMARY})" in index
    today = harness.ctx.clock().date().isoformat()
    log = (fresh / "log.md").read_text()
    section = log.split(f"## {today}\n", 1)[1]
    assert section.index("**Ingest**: [Tide Pools]") < section.index("**Answer**: Field trip plan")
    assert errors(fresh) == []


def test_a_conflict_in_a_wiki_page_fails_the_job_and_keeps_the_other_clones_page(harness, seeded):
    def write_sea_star(root: Path) -> None:
        (root / "wiki" / "entities").mkdir(parents=True)
        (root / SEA_STAR).write_text("# Sea Star\n\nWritten by hand.\n")

    def colleague_pushes(request: int) -> None:
        if request == 1:
            push_from_other_clone(harness, seeded, write_sea_star)

    harness.enable_ingest()
    harness.script_ingest(
        "tide-pools", "Tide Pools", [("sea-star", "Ochre Sea Star")], colleague_pushes
    )
    harness.ctx.jobs.enqueue(
        JobKind.SOURCE_INGEST, seeded, {"brain_id": seeded, "source_slug": "tide-pools"}
    )

    job = harness.run()

    assert job.status is JobStatus.FAILED
    assert job.error is not None and str(SEA_STAR) in job.error
    assert harness.commit_messages(seeded)[0] == "Edit on another computer"
    assert is_clean(harness, seeded)
    assert "Written by hand." in (harness.clone_path(seeded) / SEA_STAR).read_text()


# --- concurrency ----------------------------------------------------------------------------


def test_jobs_of_one_brain_never_overlap_when_threads_run_jobs_in_parallel(harness):
    first = harness.create_brain("first-notes", "First Notes")
    second = harness.create_brain("second-notes", "Second Notes")
    for brain_id in (first, second, first, second, first, first):
        sync_brain(harness.ctx, brain_id)

    worker = Worker(harness.ctx, worker_id="threads", concurrency=3, poll_interval=0.02)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        deadline = time.monotonic() + 60
        while list_jobs(harness.ctx, statuses=[JobStatus.QUEUED, JobStatus.RUNNING]):
            assert time.monotonic() < deadline, "the jobs did not finish"
            time.sleep(0.05)
    finally:
        worker.stop()
        thread.join(timeout=30)
    assert not thread.is_alive()

    syncs = [job for job in list_jobs(harness.ctx) if job.kind is JobKind.BRAIN_SYNC]
    assert len(syncs) == 6
    assert all(job.status is JobStatus.SUCCEEDED for job in syncs)
    for brain_id in (first, second):
        runs = sorted(
            (job.started_at, job.finished_at) for job in syncs if job.brain_id == brain_id
        )
        for (_, previous_end), (next_start, _) in itertools.pairwise(runs):
            assert previous_end is not None and next_start is not None
            assert previous_end <= next_start
    assert get_worker_status(harness.ctx).worker_id == "threads"


# --- the process --------------------------------------------------------------------------


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
def test_the_worker_process_stops_cleanly_on_a_signal(ctx, stop_signal):
    process = subprocess.Popen(
        [sys.executable, "-m", "second_brain_builder.worker"],
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not get_worker_status(ctx).online:
            assert process.poll() is None, process.communicate()[0]
            assert time.monotonic() < deadline, "the worker sent no heartbeat"
            time.sleep(0.1)
        process.send_signal(stop_signal)
        output, _ = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0, output
    assert "stopped" in output
