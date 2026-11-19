"""Claims from several worker processes on one SQLite file."""

import json
import subprocess
import sys
import time
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path

from second_brain_builder.config import Settings
from second_brain_builder.domain import JobKind, JobStatus
from second_brain_builder.store import Job, JobQueue

CLAIMER = Path(__file__).parents[1] / "fixtures" / "store" / "claim_jobs.py"
PROCESSES = 4
HOLD_SECONDS = 0.005


def run_claimers(settings: Settings, tmp_path: Path) -> list[list[int]]:
    """Start the claimer processes together and return the job ids each one claimed."""
    start = tmp_path / "start"
    workers = [f"worker-{n}" for n in range(PROCESSES)]
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                str(CLAIMER),
                str(settings.database_path),
                worker,
                str(start),
                str(HOLD_SECONDS),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for worker in workers
    ]
    try:
        deadline = time.monotonic() + 60
        ready = [start.with_name(f"start.{worker}.ready") for worker in workers]
        while not all(path.exists() for path in ready):
            assert all(process.poll() is None for process in processes), "a claimer exited"
            assert time.monotonic() < deadline, "the claimers did not start"
            time.sleep(0.01)
        start.touch()
        claimed: list[list[int]] = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            assert process.returncode == 0, stderr
            claimed.append(json.loads(stdout))
        return claimed
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()


def test_concurrent_claimers_never_share_a_job_or_a_brain(
    settings: Settings,
    tmp_path: Path,
    queue: JobQueue,
    add_brain: Callable[[str], str],
) -> None:
    brains = [add_brain(f"brain-{n}") for n in range(3)]
    jobs: list[Job] = []
    for round_number in range(8):
        for brain in [*brains, None]:
            jobs.append(queue.enqueue(JobKind.SOURCE_ADD, brain, {"round": round_number}))

    claimed = run_claimers(settings, tmp_path)

    every_claim = [job_id for ids in claimed for job_id in ids]
    assert len(every_claim) == len(set(every_claim)), "a job was claimed twice"
    assert sorted(every_claim) == [job.id for job in jobs]
    assert sum(1 for ids in claimed if ids) >= 2, "only one process claimed jobs"
    finished = [queue.get(job.id) for job in jobs]
    assert all(job.status is JobStatus.SUCCEEDED for job in finished)
    for brain in brains:
        runs = sorted(
            (job for job in finished if job.brain_id == brain),
            key=lambda job: job.started_at or job.created_at,
        )
        # One job at a time per Brain, in the order they were enqueued.
        assert [job.id for job in runs] == sorted(job.id for job in runs)
        for earlier, later in pairwise(runs):
            assert earlier.finished_at is not None and later.started_at is not None
            assert later.started_at >= earlier.finished_at
