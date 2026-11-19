"""The worker process: claim jobs, run them, and complete or fail them.

On start, the worker puts the jobs that a stopped worker left ``running`` back in
the queue. Then ``SBB_WORKER_CONCURRENCY`` threads each claim a job, run it, and
complete or fail it, and sleep a short time when the queue has nothing for them.
``JobQueue.claim`` never gives out two jobs of one Brain at the same time. A
heartbeat thread tells the UI that the worker runs. SIGTERM and SIGINT stop the
threads after their current job.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
from collections.abc import Callable
from types import FrameType

from second_brain_builder.config import get_settings
from second_brain_builder.services import (
    HEARTBEAT_INTERVAL,
    AppContext,
    record_worker_heartbeat,
)
from second_brain_builder.store import Job, JobNotFoundError, JobStateError
from second_brain_builder.worker.context import JobRun
from second_brain_builder.worker.errors import JobError, describe_error
from second_brain_builder.worker.handlers import HANDLERS

_log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.0
"""How long an idle worker thread waits before it asks the queue again."""


def default_worker_id() -> str:
    """``<host>-<pid>``: one id for all threads of this process."""
    return f"{socket.gethostname()}-{os.getpid()}"


def run_once(ctx: AppContext, worker_id: str) -> Job | None:
    """Claim one job, run it, and complete or fail it.

    Returns the finished job as stored, or ``None`` when no job could start: the
    queue is empty, or every queued job waits for a running job of its Brain.
    """
    job = ctx.jobs.claim(worker_id)
    if job is None:
        return None
    execute_job(ctx, job, worker_id)
    try:
        return ctx.jobs.get(job.id)
    except JobNotFoundError:  # its Brain was disconnected meanwhile
        return job


def execute_job(ctx: AppContext, job: Job, worker_id: str) -> None:
    """Run a claimed job with the handler of its kind, then complete or fail it.

    On failure the clone is reset to the remote state and the failure hooks run
    before the job is marked failed, so the next job of the Brain, which can start
    as soon as this one leaves ``running``, finds a clean clone.
    """
    run = JobRun(ctx=ctx, job=job, worker_id=worker_id)
    handler = HANDLERS.get(job.kind)
    _log.info("Job %d (%s) started.", job.id, job.kind)
    try:
        if handler is None:
            raise JobError(f"This worker has no handler for {job.kind} jobs.")
        handler(run)
    except Exception as exc:
        error = describe_error(exc, run.secrets)
        _log.warning("Job %d (%s) failed: %s", job.id, job.kind, error)
        run.clean_up_after_failure()
        _finish(job, lambda: ctx.jobs.fail(job.id, error, run.usage, worker_id=worker_id))
        return
    _log.info("Job %d (%s) succeeded.", job.id, job.kind)
    _finish(job, lambda: ctx.jobs.complete(job.id, run.usage, worker_id=worker_id))


def _finish(job: Job, finish: Callable[[], None]) -> None:
    try:
        finish()
    except JobStateError as exc:
        # requeue_stale of another worker process took the job over.
        _log.warning("Job %d (%s): %s", job.id, job.kind, exc)
    except JobNotFoundError:
        _log.warning("Job %d (%s) is gone: its Brain was disconnected.", job.id, job.kind)


class Worker:
    """Runs the jobs of one database in ``concurrency`` threads until ``stop``."""

    def __init__(
        self,
        ctx: AppContext,
        *,
        worker_id: str | None = None,
        concurrency: int | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self.ctx = ctx
        self.worker_id = worker_id or default_worker_id()
        self.concurrency = ctx.settings.worker_concurrency if concurrency is None else concurrency
        if self.concurrency < 1:
            raise ValueError("concurrency must be 1 or more")
        self.poll_interval = poll_interval
        self._stop = threading.Event()

    def start(self) -> int:
        """Put the jobs that a stopped worker left ``running`` back in the queue.

        Returns how many jobs went back. A job that reached the attempt limit fails
        instead (see ``JobQueue.requeue_stale``).
        """
        requeued = self.ctx.jobs.requeue_stale()
        if requeued:
            _log.info("Put %d job(s) back in the queue.", requeued)
        return requeued

    def run_once(self) -> Job | None:
        """Claim, run, and finish one job; see the module function ``run_once``."""
        return run_once(self.ctx, self.worker_id)

    def run(self) -> None:
        """Start, then run jobs in ``concurrency`` threads until ``stop`` is called.

        Returns when every thread has finished its current job.
        """
        self.start()
        threads = [
            threading.Thread(target=self._work, name=f"sbb-worker-{number}")
            for number in range(1, self.concurrency + 1)
        ]
        threads.append(threading.Thread(target=self._heartbeat, name="sbb-worker-heartbeat"))
        for thread in threads:
            thread.start()
        _log.info("Worker %s runs %d thread(s).", self.worker_id, self.concurrency)
        # Wait with a timeout, so the main thread can run signal handlers.
        while not self._stop.wait(timeout=0.5):
            pass
        for thread in threads:
            thread.join()
        _log.info("Worker %s stopped.", self.worker_id)

    def stop(self) -> None:
        """Ask the threads to stop after their current job. Safe from a signal handler."""
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.run_once()
            except Exception as exc:
                # The queue itself failed, for example a database error.
                _log.error(
                    "The worker could not claim or finish a job: %s", describe_error(exc, [])
                )
                job = None
            if job is None:
                self._stop.wait(self.poll_interval)

    def _heartbeat(self) -> None:
        while True:
            try:
                record_worker_heartbeat(self.ctx, self.worker_id)
            except Exception as exc:
                _log.error("The worker could not record its heartbeat: %s", describe_error(exc, []))
            if self._stop.wait(HEARTBEAT_INTERVAL.total_seconds()):
                return


def main() -> int:
    """``python -m second_brain_builder.worker``: run jobs until SIGTERM or SIGINT."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    # Hugging Face reads HF_HOME when it is first imported, so set it before any job.
    os.environ["HF_HOME"] = str(settings.models_dir)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    ctx = AppContext.create(settings)
    worker = Worker(ctx)

    def request_stop(signum: int, frame: FrameType | None) -> None:
        _log.info("Got %s: stopping after the running jobs.", signal.Signals(signum).name)
        worker.stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        worker.run()
    finally:
        ctx.close()
    return 0
