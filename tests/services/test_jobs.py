"""Job use cases: retry, cancel (and staged upload cleanup), and the event stream."""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete

from second_brain_builder.domain import BrainStatus, JobKind, JobStatus
from second_brain_builder.services import (
    AnswerInput,
    AppContext,
    BrainCreatePayload,
    ConflictError,
    DuplicateSourceError,
    InvalidInputError,
    NotFoundError,
    QuotaExceededError,
    add_source,
    cancel_job,
    delete_source,
    events_since,
    get_brain,
    get_job,
    get_lfs_usage,
    latest_event_id,
    list_jobs,
    move_source,
    reconvert_source,
    retry_job,
    save_answer,
    sync_brain,
)
from second_brain_builder.store import BrainRow, SourceRow

if TYPE_CHECKING:
    from tests.services.conftest import AddSourceRow, BrainFixture, InsertBrain

NOTES = b"Synthetic notes about sea grass.\n"
ANSWER_BODY = (
    "Grey herons hunt in tide pools at low tide.[^tide-pools] "
    "They also wade along the shore.[^coastal-birds]"
)


def fail(ctx: AppContext, job_id: int) -> None:
    """A worker claims the job, which must be the oldest queued one, and fails it."""
    claimed = ctx.jobs.claim("worker-1")
    assert claimed is not None
    assert claimed.id == job_id
    ctx.jobs.fail(job_id, "synthetic failure", worker_id="worker-1")


def test_retry_of_a_failed_job_enqueues_the_same_work(ctx: AppContext, brain: BrainFixture) -> None:
    failed = sync_brain(ctx, brain.id)
    ctx.jobs.claim("worker-1")
    ctx.jobs.fail(failed.id, "network down", worker_id="worker-1")

    retried = retry_job(ctx, failed.id)

    assert retried.id != failed.id
    assert (retried.kind, retried.brain_id, retried.payload, retried.status) == (
        JobKind.BRAIN_SYNC,
        brain.id,
        failed.payload,
        JobStatus.QUEUED,
    )


def test_only_failed_or_cancelled_jobs_can_be_retried(ctx: AppContext, brain: BrainFixture) -> None:
    queued = sync_brain(ctx, brain.id)

    with pytest.raises(ConflictError, match="Only a failed or cancelled job"):
        retry_job(ctx, queued.id)
    with pytest.raises(NotFoundError):
        retry_job(ctx, 9999)


def test_cancelled_upload_loses_its_staged_file_and_cannot_be_retried(
    ctx: AppContext, brain: BrainFixture
) -> None:
    added = add_source(ctx, brain.id, "notes.txt", io.BytesIO(NOTES))
    staged = Path(added.job.payload["upload_path"])

    cancelled = cancel_job(ctx, added.job.id)

    assert cancelled.status is JobStatus.CANCELLED
    assert not staged.parent.exists()
    with pytest.raises(ConflictError, match="gone"):
        retry_job(ctx, added.job.id)


def test_failed_upload_with_its_staged_file_can_be_retried(
    ctx: AppContext, brain: BrainFixture
) -> None:
    added = add_source(ctx, brain.id, "notes.txt", io.BytesIO(NOTES))
    ctx.jobs.claim("worker-1")
    ctx.jobs.fail(added.job.id, "conversion failed", worker_id="worker-1")

    retried = retry_job(ctx, added.job.id)

    assert retried.payload == added.job.payload


def test_a_job_is_not_retried_while_its_retry_is_pending_or_after_it_succeeded(
    ctx: AppContext, brain: BrainFixture
) -> None:
    failed = sync_brain(ctx, brain.id)
    fail(ctx, failed.id)
    retried = retry_job(ctx, failed.id)

    with pytest.raises(ConflictError, match=f"Job {retried.id} does the work"):
        retry_job(ctx, failed.id)  # a double click
    ctx.jobs.claim("worker-1")
    ctx.jobs.complete(retried.id, None, worker_id="worker-1")
    with pytest.raises(ConflictError, match=f"Job {retried.id} did the work"):
        retry_job(ctx, failed.id)

    assert [job.id for job in list_jobs(ctx, brain.id)] == [retried.id, failed.id]


def test_retry_of_an_upload_that_was_added_again_is_a_duplicate(
    ctx: AppContext, brain: BrainFixture, add_source_row: AddSourceRow
) -> None:
    first = add_source(ctx, brain.id, "notes.txt", io.BytesIO(NOTES))
    fail(ctx, first.job.id)
    again = add_source(ctx, brain.id, "notes copy.txt", io.BytesIO(NOTES))  # allowed: A failed

    with pytest.raises(DuplicateSourceError) as pending:
        retry_job(ctx, first.job.id)
    assert pending.value.pending_job is not None
    assert pending.value.pending_job.id == again.job.id

    # The worker adds the Source of the second upload.
    ctx.jobs.claim("worker-1")
    ctx.jobs.complete(again.job.id, None, worker_id="worker-1")
    add_source_row(brain.id, "notes-copy", "", sha256=first.sha256, title="Notes copy")
    with pytest.raises(DuplicateSourceError) as existing:
        retry_job(ctx, first.job.id)
    assert existing.value.existing is not None
    assert existing.value.existing.slug == "notes-copy"


def test_retry_of_an_upload_checks_the_lfs_quota_again(
    ctx: AppContext, brain: BrainFixture
) -> None:
    used = get_lfs_usage(ctx, brain.id).owner_bytes
    tight = replace(ctx, settings=ctx.settings.model_copy(update={"lfs_quota_bytes": used + 50}))
    first = add_source(tight, brain.id, "a.txt", io.BytesIO(NOTES))  # 33 bytes
    fail(tight, first.job.id)
    add_source(tight, brain.id, "b.txt", io.BytesIO(NOTES.upper()))  # the failed one does not count

    with pytest.raises(QuotaExceededError):
        retry_job(tight, first.job.id)

    retried = retry_job(tight, first.job.id, confirm_over_quota=True)
    assert (retried.kind, retried.payload) == (JobKind.SOURCE_ADD, first.job.payload)


def test_retry_of_a_source_job_runs_the_checks_of_its_use_case_again(
    ctx: AppContext, brain: BrainFixture
) -> None:
    reconvert = reconvert_source(ctx, brain.id, "tide-pools")
    fail(ctx, reconvert.id)
    move = move_source(ctx, brain.id, "coastal-birds", "archive")
    fail(ctx, move.id)
    answer = save_answer(
        ctx,
        brain.id,
        AnswerInput(title="Herons", body=ANSWER_BODY, source_ids=["tide-pools", "coastal-birds"]),
    )
    fail(ctx, answer.id)
    delete_source(ctx, brain.id, "tide-pools")

    with pytest.raises(ConflictError, match="being deleted"):
        retry_job(ctx, reconvert.id)

    # brain.sync found that coastal-birds is gone from the repository.
    with ctx.sessions.begin() as session:
        session.execute(delete(SourceRow).where(SourceRow.slug == "coastal-birds"))
    with pytest.raises(ConflictError, match="does not exist"):
        retry_job(ctx, move.id)  # a ConflictError, not a NotFoundError: the job exists
    with pytest.raises(ConflictError, match="coastal-birds"):
        retry_job(ctx, answer.id)


def test_retry_of_brain_create_needs_a_brain_that_is_not_ready(
    ctx: AppContext, insert_brain: InsertBrain
) -> None:
    brain_id = insert_brain("test-user/field-notes")
    payload = BrainCreatePayload(
        brain_id=brain_id, repo_full_name="test-user/field-notes", create_repo=True
    )
    job = ctx.jobs.enqueue(JobKind.BRAIN_CREATE, brain_id, payload.to_json())
    fail(ctx, job.id)

    with pytest.raises(ConflictError, match="created already"):
        retry_job(ctx, job.id)

    with ctx.sessions.begin() as session:
        row = session.get(BrainRow, brain_id)
        assert row is not None
        row.status = BrainStatus.ERROR
    retry_job(ctx, job.id)
    assert get_brain(ctx, brain_id).status is BrainStatus.CREATING


def test_a_running_job_cannot_be_cancelled(ctx: AppContext, brain: BrainFixture) -> None:
    job = sync_brain(ctx, brain.id)
    ctx.jobs.claim("worker-1")

    with pytest.raises(ConflictError, match="running"):
        cancel_job(ctx, job.id)
    assert get_job(ctx, job.id).status is JobStatus.RUNNING


def test_events_stream_from_an_id(ctx: AppContext, brain: BrainFixture) -> None:
    start = latest_event_id(ctx)
    job = sync_brain(ctx, brain.id)
    ctx.jobs.claim("worker-1")
    ctx.jobs.emit(job.id, "progress", {"step": "pull"})

    events = events_since(ctx, start, brain_id=brain.id)

    assert [(event.kind, event.data) for event in events] == [
        ("status", {"status": "queued"}),
        ("status", {"status": "running"}),
        ("progress", {"step": "pull"}),
    ]
    assert events_since(ctx, events[-1].id) == []
    with pytest.raises(InvalidInputError):
        events_since(ctx, -1)
