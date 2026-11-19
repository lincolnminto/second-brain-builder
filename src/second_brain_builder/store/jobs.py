"""The job queue and job events.

The ``app`` enqueues and cancels jobs. The ``worker`` claims jobs, emits events
while it runs them, and completes or fails them. Both containers use the same
SQLite file, so every write runs in its own short ``BEGIN IMMEDIATE`` transaction
and commits before the method returns.

Every status change also writes a ``status`` event (``{"status": "<JobStatus>"}``,
plus ``"error"`` for a failed job) in the same transaction. Handlers do not need
to emit status events themselves.
"""

import logging
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from second_brain_builder.domain import JobKind, JobStatus, Usage
from second_brain_builder.store.db import make_session_factory
from second_brain_builder.store.models import JobEventRow, JobRow
from second_brain_builder.store.types import utc_now

logger = logging.getLogger(__name__)

STATUS_EVENT = "status"
"""Event kind that ``JobQueue`` writes for every job status change."""

DEFAULT_MAX_ATTEMPTS = 3
"""``requeue_stale`` fails a job after the worker stopped this many times while it ran."""

DEFAULT_EVENT_LIMIT = 1000
"""Most events that one ``events_since`` call returns."""

STALE_JOB_ERROR = "The worker stopped while this job was running, too many times."


class Job(BaseModel):
    """A job, as stored when the ``JobQueue`` call returned."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    brain_id: str | None
    kind: JobKind
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    error: str | None
    worker_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    input_tokens: int | None
    output_tokens: int | None


class JobEvent(BaseModel):
    """An event that a job emitted. Ids increase in commit order."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    job_id: int
    brain_id: str | None
    created_at: datetime
    kind: str
    data: dict[str, Any]


class JobNotFoundError(LookupError):
    """No job has this id."""

    def __init__(self, job_id: int) -> None:
        super().__init__(f"Job {job_id} does not exist.")
        self.job_id = job_id


class JobStateError(Exception):
    """The job's status or worker does not allow the operation.

    Examples: cancel of a running job, or complete by a worker after
    ``requeue_stale`` gave the job to another worker. ``owner`` is the worker that
    runs the job when that is the reason, else None.
    """

    def __init__(
        self, job_id: int, status: JobStatus, operation: str, owner: str | None = None
    ) -> None:
        reason = f"its status is {status}" if owner is None else f"worker {owner!r} runs it"
        super().__init__(f"Cannot {operation} job {job_id}: {reason}.")
        self.job_id = job_id
        self.status = status
        self.operation = operation
        self.owner = owner


class JobQueue:
    """Jobs and job events in the store database.

    Jobs for one Brain run one at a time: ``claim`` never returns a job whose Brain
    already has a running job. Jobs without a Brain never wait for other jobs.
    """

    def __init__(self, engine: Engine) -> None:
        self._read = make_session_factory(engine, immediate=False)
        self._write = make_session_factory(engine, immediate=True)

    def enqueue(self, kind: JobKind, brain_id: str | None, payload: dict[str, Any]) -> Job:
        """Add a queued job. ``payload`` must be JSON-serializable."""
        with self._write_transaction() as (session, now):
            row = JobRow(
                kind=JobKind(kind),
                brain_id=brain_id,
                payload=payload,
                status=JobStatus.QUEUED,
                attempts=0,
                created_at=now,
            )
            session.add(row)
            session.flush()
            _add_status_event(session, row, now)
            return Job.model_validate(row)

    def claim(self, worker_id: str) -> Job | None:
        """Start the oldest queued job whose Brain has no running job.

        Returns None if no job can start now. The job becomes ``running`` with
        ``worker_id`` and ``started_at`` set. Claims are atomic across threads and
        processes: one statement selects and updates the job while it holds the
        database write lock, so two claimers never get the same job, and never
        two jobs of the same Brain.
        """
        pending = aliased(JobRow, name="pending")
        running = aliased(JobRow, name="running")
        brain_busy = (
            select(running.id)
            .where(running.brain_id == pending.brain_id, running.status == JobStatus.RUNNING)
            .exists()
        )
        next_job_id = (
            select(pending.id)
            .where(pending.status == JobStatus.QUEUED, or_(pending.brain_id.is_(None), ~brain_busy))
            .order_by(pending.id)
            .limit(1)
            .scalar_subquery()
        )
        with self._write_transaction() as (session, now):
            row = session.scalars(
                update(JobRow)
                .where(JobRow.id == next_job_id, JobRow.status == JobStatus.QUEUED)
                .values(status=JobStatus.RUNNING, started_at=now, worker_id=worker_id)
                .returning(JobRow)
            ).one_or_none()
            if row is None:
                return None
            _add_status_event(session, row, now)
            return Job.model_validate(row)

    def complete(self, job_id: int, usage: Usage | None, *, worker_id: str) -> None:
        """Mark a running job ``succeeded`` and store its token usage, if any.

        ``worker_id`` is the id that the worker gave to ``claim``. Raises
        ``JobNotFoundError``, or ``JobStateError`` if the job is not running or
        another worker runs it.
        """
        self._finish(job_id, worker_id, JobStatus.SUCCEEDED, None, usage, "complete")

    def fail(self, job_id: int, error: str, usage: Usage | None = None, *, worker_id: str) -> None:
        """Mark a running job ``failed`` with an error message for the user.

        ``usage`` records the tokens that the job used before it failed.
        ``worker_id`` is the id that the worker gave to ``claim``. Raises
        ``JobNotFoundError``, or ``JobStateError`` if the job is not running or
        another worker runs it.
        """
        self._finish(job_id, worker_id, JobStatus.FAILED, error, usage, "fail")

    def cancel(self, job_id: int) -> None:
        """Mark a queued job ``cancelled``. A job that started cannot be cancelled.

        Raises ``JobNotFoundError``, or ``JobStateError`` if the job is not queued.
        """
        with self._write_transaction() as (session, now):
            row = _transition(
                session,
                job_id,
                JobStatus.QUEUED,
                "cancel",
                {"status": JobStatus.CANCELLED, "finished_at": now},
            )
            _add_status_event(session, row, now)

    def requeue_stale(self, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> int:
        """Put jobs that a stopped worker left ``running`` back in the queue.

        Call it when the worker starts, before it claims jobs: every running job
        is then stale. Each stale job gets ``attempts + 1`` and keeps its place in
        the queue. A job whose attempts reach ``max_attempts`` is marked ``failed``
        instead, so a job that crashes the worker cannot run forever.

        It treats every running job as stale, so one worker process (with any
        number of threads) must run the jobs of a database. If a second worker
        process starts and calls it, it takes over the jobs that the first one
        still runs. ``complete`` and ``fail`` then reject the first worker, so the
        jobs of one Brain still start one at a time.

        Returns the number of jobs put back in the queue.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        is_running = JobRow.status == JobStatus.RUNNING
        next_attempts = JobRow.attempts + 1
        with self._write_transaction() as (session, now):
            exhausted = session.scalars(
                update(JobRow)
                .where(is_running, next_attempts >= max_attempts)
                .values(
                    status=JobStatus.FAILED,
                    attempts=next_attempts,
                    finished_at=now,
                    error=STALE_JOB_ERROR,
                )
                .returning(JobRow)
            ).all()
            requeued = session.scalars(
                update(JobRow)
                .where(is_running)
                .values(
                    status=JobStatus.QUEUED, attempts=next_attempts, started_at=None, worker_id=None
                )
                .returning(JobRow)
            ).all()
            for row in (*exhausted, *requeued):
                _add_status_event(session, row, now)
        for row in exhausted:
            logger.warning("Job %d (%s) failed after %d attempts.", row.id, row.kind, row.attempts)
        return len(requeued)

    def emit(self, job_id: int, kind: str, data: dict[str, Any]) -> None:
        """Add an event for a job, such as ``progress`` or ``page_written``.

        ``data`` must be JSON-serializable. Raises ``JobNotFoundError``.
        """
        with self._write_transaction() as (session, now):
            job = session.execute(select(JobRow.brain_id).where(JobRow.id == job_id)).one_or_none()
            if job is None:
                raise JobNotFoundError(job_id)
            session.add(
                JobEventRow(
                    job_id=job_id, brain_id=job.brain_id, created_at=now, kind=kind, data=data
                )
            )

    def events_since(
        self, after_id: int, brain_id: str | None = None, limit: int = DEFAULT_EVENT_LIMIT
    ) -> list[JobEvent]:
        """Return events with an id greater than ``after_id``, oldest first.

        With ``brain_id``, only that Brain's events. Without it, the events of
        every Brain and of jobs without a Brain. To stream events, call it again
        with the id of the last event received. Returns at most ``limit`` events.
        """
        statement = select(JobEventRow).where(JobEventRow.id > after_id)
        if brain_id is not None:
            statement = statement.where(JobEventRow.brain_id == brain_id)
        statement = statement.order_by(JobEventRow.id).limit(limit)
        with self._read() as session:
            return [JobEvent.model_validate(row) for row in session.scalars(statement)]

    def latest_event_id(self) -> int:
        """Return the id of the newest event, or 0 if there is none.

        A stream that should show only new events starts after this id.
        """
        with self._read() as session:
            return session.scalar(select(func.max(JobEventRow.id))) or 0

    def get(self, job_id: int) -> Job:
        """Return one job. Raises ``JobNotFoundError``."""
        with self._read() as session:
            row = session.get(JobRow, job_id)
            if row is None:
                raise JobNotFoundError(job_id)
            return Job.model_validate(row)

    def list_jobs(
        self,
        brain_id: str | None = None,
        statuses: Collection[JobStatus] | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """Return jobs, newest first, optionally only one Brain's or some statuses."""
        statement = select(JobRow)
        if brain_id is not None:
            statement = statement.where(JobRow.brain_id == brain_id)
        if statuses is not None:
            statement = statement.where(JobRow.status.in_(list(statuses)))
        statement = statement.order_by(JobRow.id.desc()).limit(limit)
        with self._read() as session:
            return [Job.model_validate(row) for row in session.scalars(statement)]

    @contextmanager
    def _write_transaction(self) -> Iterator[tuple[Session, datetime]]:
        """Yield a session in a ``BEGIN IMMEDIATE`` transaction and the current time.

        The time is read after the transaction holds the write lock. A time read
        before the wait for the lock could be older than writes that other
        processes committed during the wait, and timestamps would then disagree
        with the real order of claims and status changes.
        """
        with self._write.begin() as session:
            session.connection()  # sends BEGIN IMMEDIATE, which waits for the lock
            yield session, utc_now()

    def _finish(
        self,
        job_id: int,
        worker_id: str,
        status: JobStatus,
        error: str | None,
        usage: Usage | None,
        operation: str,
    ) -> None:
        values: dict[str, Any] = {"status": status, "error": error}
        if usage is not None:
            values["input_tokens"] = usage.input_tokens
            values["output_tokens"] = usage.output_tokens
        with self._write_transaction() as (session, now):
            values["finished_at"] = now
            row = _transition(
                session, job_id, JobStatus.RUNNING, operation, values, worker_id=worker_id
            )
            _add_status_event(session, row, now)


def _transition(
    session: Session,
    job_id: int,
    expected: JobStatus,
    operation: str,
    values: dict[str, Any],
    *,
    worker_id: str | None = None,
) -> JobRow:
    """Update the job if its status is ``expected``, else raise the matching error.

    With ``worker_id``, the job must also be claimed by that worker.
    """
    conditions = [JobRow.id == job_id, JobRow.status == expected]
    if worker_id is not None:
        conditions.append(JobRow.worker_id == worker_id)
    row = session.scalars(
        update(JobRow).where(*conditions).values(**values).returning(JobRow)
    ).one_or_none()
    if row is not None:
        return row
    current = session.execute(
        select(JobRow.status, JobRow.worker_id).where(JobRow.id == job_id)
    ).one_or_none()
    if current is None:
        raise JobNotFoundError(job_id)
    status, owner = current
    if status is expected:  # so the worker did not match
        raise JobStateError(job_id, status, operation, owner=owner)
    raise JobStateError(job_id, status, operation)


def _add_status_event(session: Session, row: JobRow, now: datetime) -> None:
    data: dict[str, Any] = {"status": row.status.value}
    if row.status is JobStatus.FAILED and row.error is not None:
        data["error"] = row.error
    session.add(
        JobEventRow(
            job_id=row.id, brain_id=row.brain_id, created_at=now, kind=STATUS_EVENT, data=data
        )
    )
