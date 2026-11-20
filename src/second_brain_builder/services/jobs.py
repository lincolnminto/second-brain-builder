"""Jobs and job events: list, get, retry, cancel, and the event stream."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError
from sqlalchemy import and_, or_, select

from second_brain_builder.domain import BrainStatus, JobKind, JobStatus
from second_brain_builder.services._common import brain_row, job_event_view, job_view
from second_brain_builder.services.agents import save_answer
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import ConflictError, InvalidInputError, NotFoundError
from second_brain_builder.services.models import AnswerInput, JobEventView, JobView
from second_brain_builder.services.payloads import (
    AnswerSavePayload,
    BrainCreatePayload,
    JobPayload,
    SourceAddPayload,
    SourceIngestPayload,
    SourceJobPayload,
    SourceMovePayload,
    parse_payload,
)
from second_brain_builder.services.sources import (
    admit_source_add,
    delete_source,
    ingest_source,
    move_source,
    reconvert_source,
)
from second_brain_builder.services.uploads import remove_staged_upload, staged_upload_path
from second_brain_builder.store import Job, JobNotFoundError, JobRow, JobStateError

MAX_JOB_LIMIT = 500
MAX_EVENT_LIMIT = 1000
RETRYABLE_STATUSES = frozenset({JobStatus.FAILED, JobStatus.CANCELLED})


def list_jobs(
    ctx: AppContext,
    brain_id: str | None = None,
    statuses: Collection[JobStatus] | None = None,
    limit: int = 100,
) -> list[JobView]:
    """Jobs, newest first, optionally of one Brain or with some statuses.

    Raises:
        InvalidInputError: ``limit`` is not between 1 and ``MAX_JOB_LIMIT``.
    """
    if not 1 <= limit <= MAX_JOB_LIMIT:
        raise InvalidInputError(f"The limit must be between 1 and {MAX_JOB_LIMIT}.")
    return [job_view(job) for job in ctx.jobs.list_jobs(brain_id, statuses, limit)]


def get_job(ctx: AppContext, job_id: int) -> JobView:
    """One job. Raises ``NotFoundError``."""
    return job_view(_job(ctx, job_id))


def retry_job(ctx: AppContext, job_id: int, *, confirm_over_quota: bool = False) -> JobView:
    """Enqueue a new job with the kind, Brain, and payload of a failed or cancelled job.

    The checks of the use case that enqueued the job run again, because the Brain
    can have changed since: a retried upload is checked for duplicates and
    against the Git LFS quota (``confirm_over_quota`` as in ``add_source``), a
    Source job needs its Source and no conflicting pending job, and an answer
    needs its Sources. A job is not retried while a job with the same kind and
    payload is queued or running, or when such a job succeeded after it.

    Raises:
        NotFoundError: No such job.
        ConflictError: The job did not fail and was not cancelled; the same work
            is queued, running, or done; it added a Source whose staged upload is
            gone (add the file again); its Brain was created already; or its
            checks fail now, for example because its Source was deleted.
        DuplicateSourceError: The retried upload is in the Brain now, or is
            being added again.
        QuotaExceededError: The retried upload would take the Git LFS usage over
            the quota, and ``confirm_over_quota`` is False.
        NotConfiguredError: A retried Ingest has no ingest Provider and model.
    """
    job = _job(ctx, job_id)
    if job.status not in RETRYABLE_STATUSES:
        raise ConflictError(
            f"Only a failed or cancelled job can be retried; job {job_id} is {job.status}."
        )
    # The add_source lock also serializes retries, so that a double click cannot
    # pass the checks twice before either job is in the queue.
    with ctx.add_source_lock:
        _check_not_redone(ctx, job)
        try:
            return _enqueue_again(ctx, job, confirm_over_quota=confirm_over_quota)
        except (NotFoundError, InvalidInputError) as exc:
            # The job exists; the state of its Brain no longer allows it.
            raise ConflictError(f"Job {job.id} cannot run again. {exc.message}") from None


def cancel_job(ctx: AppContext, job_id: int) -> JobView:
    """Cancel a queued job, or request a stop at a running job's next safe point.

    The staged upload of a cancelled ``source.add`` job is removed, so such a job
    cannot be retried.

    Raises:
        NotFoundError: No such job.
        ConflictError: The job has finished.
    """
    try:
        job = ctx.jobs.cancel(job_id)
    except JobNotFoundError:
        raise NotFoundError(f"Job {job_id} does not exist.") from None
    except JobStateError as exc:
        raise ConflictError(str(exc)) from None
    if job.kind is JobKind.SOURCE_ADD and job.status is JobStatus.CANCELLED:
        remove_staged_upload(ctx, job.payload)
    return job_view(job)


def events_since(
    ctx: AppContext, after_id: int, brain_id: str | None = None, limit: int = MAX_EVENT_LIMIT
) -> list[JobEventView]:
    """Job events with an id greater than ``after_id``, oldest first.

    For Server-Sent Events: call it again with the id of the last event. Without
    ``brain_id``, the events of every Brain.

    Raises:
        InvalidInputError: ``after_id`` is negative, or ``limit`` is not between 1
            and ``MAX_EVENT_LIMIT``.
    """
    if after_id < 0:
        raise InvalidInputError("The event id must be 0 or more.")
    if not 1 <= limit <= MAX_EVENT_LIMIT:
        raise InvalidInputError(f"The limit must be between 1 and {MAX_EVENT_LIMIT}.")
    return [job_event_view(event) for event in ctx.jobs.events_since(after_id, brain_id, limit)]


def latest_event_id(ctx: AppContext) -> int:
    """The id of the newest event, where a stream of new events starts; 0 if none."""
    return ctx.jobs.latest_event_id()


def _job(ctx: AppContext, job_id: int) -> Job:
    try:
        return ctx.jobs.get(job_id)
    except JobNotFoundError:
        raise NotFoundError(f"Job {job_id} does not exist.") from None


def _check_not_redone(ctx: AppContext, job: Job) -> None:
    """Refuse a retry when another job does the same work, or did it after ``job``."""
    with ctx.read_sessions() as session:
        others = session.execute(
            select(JobRow.id, JobRow.status, JobRow.payload).where(
                JobRow.brain_id == job.brain_id,
                JobRow.kind == job.kind,
                JobRow.id != job.id,
                or_(
                    JobRow.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                    and_(JobRow.status == JobStatus.SUCCEEDED, JobRow.id > job.id),
                ),
            )
        ).all()
    for other_id, status, payload in others:
        if payload != job.payload:
            continue
        if status is JobStatus.SUCCEEDED:
            raise ConflictError(f"Job {other_id} did the work of job {job.id} already.")
        raise ConflictError(f"Job {other_id} does the work of job {job.id} already ({status}).")


def _enqueue_again(ctx: AppContext, job: Job, *, confirm_over_quota: bool) -> JobView:
    """Enqueue the work of ``job`` through the checks of the use case that made it."""
    try:
        parse_payload(job.kind, job.payload)
    except ValidationError:
        raise ConflictError(f"Job {job.id} has a payload that is not valid.") from None
    if job.kind is JobKind.SOURCE_INGEST:
        ingest = _payload(SourceIngestPayload, job)
        return ingest_source(
            ctx,
            ingest.brain_id,
            ingest.source_slug,
            resume=ingest.resume,
            max_tokens=ingest.max_tokens,
            mode=ingest.mode,
        )
    if job.kind is JobKind.SOURCE_ADD:
        upload = _payload(SourceAddPayload, job)
        path: Path | None = staged_upload_path(ctx, job.payload)
        if path is None or not path.is_file():
            raise ConflictError("The uploaded file of this job is gone. Add the file again.")
        size_bytes = path.stat().st_size
        return job_view(
            admit_source_add(ctx, upload, size_bytes, confirm_over_quota=confirm_over_quota)
        )
    if job.kind is JobKind.SOURCE_MOVE:
        move = _payload(SourceMovePayload, job)
        return move_source(ctx, move.brain_id, move.source_slug, move.folder)
    source_jobs = {
        JobKind.SOURCE_DELETE: delete_source,
        JobKind.SOURCE_RECONVERT: reconvert_source,
    }
    if job.kind in source_jobs:
        source = _payload(SourceJobPayload, job)
        return source_jobs[job.kind](ctx, source.brain_id, source.source_slug)
    if job.kind is JobKind.ANSWER_SAVE:
        answer = _payload(AnswerSavePayload, job)
        data = AnswerInput(
            title=answer.title,
            body=answer.body,
            source_ids=answer.source_ids,
            description=answer.description,
            tags=answer.tags,
        )
        return save_answer(ctx, answer.brain_id, data)
    if job.kind is JobKind.BRAIN_CREATE:
        create = _payload(BrainCreatePayload, job)
        with ctx.sessions.begin() as session:
            brain = brain_row(session, create.brain_id)
            if brain.status is BrainStatus.READY:
                raise ConflictError("The Brain was created already.")
            brain.status = BrainStatus.CREATING
    return job_view(ctx.jobs.enqueue(job.kind, job.brain_id, job.payload))


_PayloadT = TypeVar("_PayloadT", bound=JobPayload)


def _payload(model: type[_PayloadT], job: Job) -> _PayloadT:
    try:
        return model.model_validate(job.payload)
    except ValidationError:
        raise ConflictError(f"Job {job.id} has a payload that is not valid.") from None
