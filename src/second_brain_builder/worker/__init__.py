"""Job runner and one handler per job kind.

Run it as ``python -m second_brain_builder.worker``. Tests drive it one job at a
time with ``run_once``. See docs/architecture.md ("Runtime" and "Jobs").
"""

from second_brain_builder.worker.errors import JobError
from second_brain_builder.worker.handlers import HANDLERS, Handler
from second_brain_builder.worker.runner import (
    POLL_INTERVAL_SECONDS,
    Worker,
    default_worker_id,
    execute_job,
    main,
    run_once,
)

__all__ = [
    "HANDLERS",
    "POLL_INTERVAL_SECONDS",
    "Handler",
    "JobError",
    "Worker",
    "default_worker_id",
    "execute_job",
    "main",
    "run_once",
]
