"""A worker process for the store concurrency tests.

Usage: python claim_jobs.py <database> <worker-id> <start-file> <hold-seconds>

The process opens the database, writes ``<start-file>.<worker-id>.ready``, waits
until ``<start-file>`` exists, then claims and completes jobs until no job is
queued. Each claimed job stays running for ``hold-seconds``. The process prints
the claimed job ids as a JSON list.
"""

import json
import sys
import time
from pathlib import Path

from second_brain_builder.domain import JobStatus
from second_brain_builder.store import JobQueue, sqlite_engine


def main() -> None:
    database, worker_id, start_file, hold = sys.argv[1:5]
    engine = sqlite_engine(Path(database))
    queue = JobQueue(engine)
    queue.latest_event_id()  # open a connection before the start signal

    start = Path(start_file)
    start.with_name(f"{start.name}.{worker_id}.ready").touch()
    deadline = time.monotonic() + 60
    while not start.exists():
        if time.monotonic() > deadline:
            raise SystemExit("No start signal.")
        time.sleep(0.001)

    claimed: list[int] = []
    while True:
        job = queue.claim(worker_id)
        if job is None:
            # Queued jobs can remain while other workers hold their Brains.
            if not queue.list_jobs(statuses=[JobStatus.QUEUED], limit=1):
                break
            time.sleep(0.001)
            continue
        claimed.append(job.id)
        time.sleep(float(hold))
        queue.complete(job.id, None, worker_id=worker_id)
    engine.dispose()
    print(json.dumps(claimed))


if __name__ == "__main__":
    main()
