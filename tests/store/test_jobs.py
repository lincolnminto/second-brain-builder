from collections.abc import Callable

import pytest

from second_brain_builder.config import Settings
from second_brain_builder.domain import JobKind, JobStatus, Usage
from second_brain_builder.store import (
    STATUS_EVENT,
    Job,
    JobEvent,
    JobNotFoundError,
    JobQueue,
    JobStateError,
    make_engine,
)

WORKER = "worker-1"


def test_usage_checkpoints_publish_run_totals_while_job_is_running(queue: JobQueue) -> None:
    job = queue.enqueue(JobKind.SOURCE_INGEST, None, {})
    data = {"source": "survey", "parts_done": 1, "parts_total": 3}
    with pytest.raises(JobStateError):
        queue.update_usage(job.id, Usage(input_tokens=100), data, worker_id=WORKER)
    queue.claim(WORKER)
    with pytest.raises(JobStateError):
        queue.update_usage(job.id, Usage(input_tokens=100), data, worker_id="other")
    for count in (100, 200):
        queue.update_usage(
            job.id, Usage(input_tokens=count, output_tokens=50), data, worker_id=WORKER
        )
        running = queue.get(job.id)
        assert running.status is JobStatus.RUNNING
        assert (running.input_tokens, running.output_tokens) == (count, 50)
        event = queue.events_since(0)[-1]
        assert event.kind == "progress"
        assert event.data == {**data, "input_tokens": count, "output_tokens": 50}
    queue.complete(job.id, None, worker_id=WORKER)
    with pytest.raises(JobStateError):
        queue.update_usage(job.id, Usage(), data, worker_id=WORKER)
    assert queue.get(job.id).input_tokens == 200
    assert len([event for event in queue.events_since(0) if event.kind == "progress"]) == 2
    with pytest.raises(JobNotFoundError):
        queue.update_usage(job.id + 100, Usage(), data, worker_id=WORKER)


def claim_id(queue: JobQueue) -> int | None:
    job = queue.claim(WORKER)
    return None if job is None else job.id


def status_events(events: list[JobEvent], job_id: int) -> list[str]:
    return [
        event.data["status"]
        for event in events
        if event.job_id == job_id and event.kind == STATUS_EVENT
    ]


def test_claim_takes_the_oldest_job_whose_brain_is_idle(
    queue: JobQueue, add_brain: Callable[[str], str]
) -> None:
    brain_a, brain_b = add_brain("a"), add_brain("b")
    a1 = queue.enqueue(JobKind.SOURCE_ADD, brain_a, {"original_filename": "one.pdf"})
    a2 = queue.enqueue(JobKind.SOURCE_INGEST, brain_a, {"source_slug": "one"})
    b1 = queue.enqueue(JobKind.BRAIN_SYNC, brain_b, {"brain_id": brain_b})
    free1 = queue.enqueue(JobKind.BRAIN_SYNC, None, {})
    free2 = queue.enqueue(JobKind.BRAIN_SYNC, None, {})

    claimed = queue.claim(WORKER)
    assert claimed is not None
    assert claimed.id == a1.id
    assert claimed.status is JobStatus.RUNNING
    assert claimed.worker_id == WORKER
    assert claimed.started_at is not None
    assert claimed.payload == {"original_filename": "one.pdf"}
    # a2 waits for a1, because both write to Brain a. Jobs without a Brain never wait.
    assert [claim_id(queue) for _ in range(4)] == [b1.id, free1.id, free2.id, None]

    queue.complete(a1.id, None, worker_id=WORKER)
    assert claim_id(queue) == a2.id


def test_complete_and_fail_record_the_outcome(
    queue: JobQueue, add_brain: Callable[[str], str]
) -> None:
    brain = add_brain("notes")
    ingest = queue.enqueue(JobKind.SOURCE_INGEST, brain, {"source_slug": "dune"})
    add = queue.enqueue(JobKind.SOURCE_ADD, None, {})
    queue.claim(WORKER)
    queue.claim(WORKER)

    queue.complete(ingest.id, Usage(input_tokens=1200, output_tokens=340), worker_id=WORKER)
    queue.fail(
        add.id, "Unsupported format: .xyz", Usage(input_tokens=5, output_tokens=0), worker_id=WORKER
    )

    done = queue.get(ingest.id)
    assert done.status is JobStatus.SUCCEEDED
    assert (done.input_tokens, done.output_tokens) == (1200, 340)
    assert done.started_at is not None and done.finished_at is not None
    assert done.finished_at >= done.started_at
    failed = queue.get(add.id)
    assert failed.status is JobStatus.FAILED
    assert failed.error == "Unsupported format: .xyz"
    assert failed.input_tokens == 5
    events = queue.events_since(0)
    assert status_events(events, ingest.id) == ["queued", "running", "succeeded"]
    assert [e.data for e in events if e.job_id == add.id][-1] == {
        "status": "failed",
        "error": "Unsupported format: .xyz",
    }


def test_only_running_jobs_can_complete_or_fail(queue: JobQueue) -> None:
    job = queue.enqueue(JobKind.BRAIN_SYNC, None, {})

    with pytest.raises(JobStateError, match="queued"):
        queue.complete(job.id, None, worker_id=WORKER)
    with pytest.raises(JobStateError):
        queue.fail(job.id, "boom", worker_id=WORKER)
    with pytest.raises(JobNotFoundError):
        queue.complete(9999, None, worker_id=WORKER)
    assert queue.get(job.id).status is JobStatus.QUEUED


def test_cancel_works_only_for_queued_jobs(
    queue: JobQueue, add_brain: Callable[[str], str]
) -> None:
    brain = add_brain("notes")
    running = queue.enqueue(JobKind.SOURCE_ADD, brain, {})
    queued = queue.enqueue(JobKind.SOURCE_ADD, brain, {})
    assert claim_id(queue) == running.id

    queue.cancel(queued.id)

    cancelled = queue.get(queued.id)
    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.finished_at is not None
    with pytest.raises(JobStateError, match="running") as raised:
        queue.cancel(running.id)
    assert raised.value.status is JobStatus.RUNNING
    assert queue.get(running.id).status is JobStatus.RUNNING
    with pytest.raises(JobStateError):
        queue.cancel(queued.id)
    with pytest.raises(JobNotFoundError):
        queue.cancel(9999)
    queue.complete(running.id, None, worker_id=WORKER)
    assert claim_id(queue) is None  # the cancelled job never runs
    assert status_events(queue.events_since(0), queued.id) == ["queued", "cancelled"]


def test_requeue_stale_puts_running_jobs_back_at_their_place(
    queue: JobQueue, add_brain: Callable[[str], str]
) -> None:
    brain = add_brain("notes")
    first = queue.enqueue(JobKind.SOURCE_ADD, brain, {})
    free = queue.enqueue(JobKind.BRAIN_SYNC, None, {})
    later = queue.enqueue(JobKind.SOURCE_INGEST, brain, {})
    assert [claim_id(queue), claim_id(queue)] == [first.id, free.id]

    # The worker crashed and starts again.
    assert queue.requeue_stale() == 2

    for job_id in (first.id, free.id):
        job = queue.get(job_id)
        assert job.status is JobStatus.QUEUED
        assert job.attempts == 1
        assert job.worker_id is None
        assert job.started_at is None
    assert queue.get(later.id).attempts == 0
    assert [claim_id(queue), claim_id(queue), claim_id(queue)] == [first.id, free.id, None]
    assert queue.requeue_stale() == 2
    assert queue.get(first.id).attempts == 2


def test_only_the_worker_that_runs_a_job_can_finish_it(
    settings: Settings, queue: JobQueue, add_brain: Callable[[str], str]
) -> None:
    brain = add_brain("notes")
    first = queue.enqueue(JobKind.SOURCE_ADD, brain, {})
    second = queue.enqueue(JobKind.SOURCE_INGEST, brain, {})
    assert queue.claim("worker-a") is not None
    # A second worker process starts while worker-a still runs the first job.
    other_engine = make_engine(settings)
    other = JobQueue(other_engine)
    assert other.requeue_stale() == 1
    taken = other.claim("worker-b")
    assert taken is not None and taken.id == first.id

    # worker-a ends its old run. The job is not marked done, so the next job of
    # the Brain cannot start while worker-b still runs the first one.
    with pytest.raises(JobStateError, match="worker-b") as raised:
        queue.complete(first.id, Usage(input_tokens=10, output_tokens=2), worker_id="worker-a")
    assert raised.value.owner == "worker-b"
    with pytest.raises(JobStateError, match="worker-b"):
        queue.fail(first.id, "Interrupted", worker_id="worker-a")
    assert queue.get(first.id).status is JobStatus.RUNNING
    assert queue.claim("worker-a") is None

    other.complete(first.id, None, worker_id="worker-b")
    done = queue.get(first.id)
    assert (done.status, done.worker_id, done.input_tokens) == (
        JobStatus.SUCCEEDED,
        "worker-b",
        None,
    )
    assert claim_id(queue) == second.id
    other_engine.dispose()


def test_requeue_stale_fails_a_job_that_keeps_stopping_the_worker(queue: JobQueue) -> None:
    job = queue.enqueue(JobKind.SOURCE_ADD, None, {})

    queue.claim(WORKER)
    assert queue.requeue_stale(max_attempts=2) == 1
    queue.claim(WORKER)
    assert queue.requeue_stale(max_attempts=2) == 0

    failed = queue.get(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.attempts == 2
    assert failed.error is not None and failed.finished_at is not None
    assert queue.claim(WORKER) is None
    assert status_events(queue.events_since(0), job.id) == [
        "queued",
        "running",
        "queued",
        "running",
        "failed",
    ]


def test_events_since_filters_by_id_and_brain(
    settings: Settings, queue: JobQueue, add_brain: Callable[[str], str]
) -> None:
    brain_a, brain_b = add_brain("a"), add_brain("b")
    start = queue.latest_event_id()
    job_a = queue.enqueue(JobKind.SOURCE_INGEST, brain_a, {})
    job_b = queue.enqueue(JobKind.SOURCE_INGEST, brain_b, {})
    job_free = queue.enqueue(JobKind.BRAIN_SYNC, None, {})
    # The worker writes through its own engine, as in its own container.
    worker_engine = make_engine(settings)
    worker = JobQueue(worker_engine)
    page = {"path": "wiki/entities/paul-atreides.md", "title": "Paul Atreides"}
    worker.emit(job_a.id, "page_written", page)
    worker.emit(job_b.id, "progress", {"percent": 50})
    worker.emit(job_free.id, "progress", {"percent": 10})

    every = queue.events_since(start)
    assert [(e.job_id, e.kind) for e in every] == [
        (job_a.id, STATUS_EVENT),
        (job_b.id, STATUS_EVENT),
        (job_free.id, STATUS_EVENT),
        (job_a.id, "page_written"),
        (job_b.id, "progress"),
        (job_free.id, "progress"),
    ]
    assert [e.id for e in every] == sorted(e.id for e in every)
    assert queue.latest_event_id() == every[-1].id

    only_a = queue.events_since(start, brain_a)
    assert [(e.kind, e.brain_id) for e in only_a] == [
        (STATUS_EVENT, brain_a),
        ("page_written", brain_a),
    ]
    assert only_a[1].data == page

    # A client polls again with the last id it received and gets only newer events.
    worker.emit(job_a.id, "progress", {"percent": 90})
    newer = queue.events_since(only_a[-1].id, brain_a)
    assert [(e.kind, e.data) for e in newer] == [("progress", {"percent": 90})]
    assert queue.events_since(newer[-1].id, brain_a) == []
    assert len(queue.events_since(start, limit=2)) == 2

    with pytest.raises(JobNotFoundError):
        worker.emit(9999, "progress", {})
    worker_engine.dispose()


def test_list_jobs_filters_by_brain_and_status(
    queue: JobQueue, add_brain: Callable[[str], str]
) -> None:
    brain_a, brain_b = add_brain("a"), add_brain("b")
    a1 = queue.enqueue(JobKind.SOURCE_ADD, brain_a, {})
    a2 = queue.enqueue(JobKind.SOURCE_ADD, brain_a, {})
    queue.enqueue(JobKind.SOURCE_ADD, brain_b, {})
    queue.claim(WORKER)

    def ids(jobs: list[Job]) -> list[int]:
        return [job.id for job in jobs]

    assert ids(queue.list_jobs(brain_id=brain_a)) == [a2.id, a1.id]
    assert ids(queue.list_jobs(brain_id=brain_a, statuses=[JobStatus.QUEUED])) == [a2.id]
    assert len(queue.list_jobs(limit=2)) == 2
    with pytest.raises(JobNotFoundError):
        queue.get(9999)
