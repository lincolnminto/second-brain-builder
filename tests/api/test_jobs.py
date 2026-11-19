"""Cancellation responses distinguish immediate cancellation and cooperative stops."""

from second_brain_builder.domain import JobKind

from .conftest import WEB


def test_cancel_response_for_queued_and_running_jobs(ctx, client):
    queued = ctx.jobs.enqueue(JobKind.SOURCE_INGEST, None, {})
    response = client.post(f"/api/jobs/{queued.id}/cancel", headers=WEB)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["stop_requested_at"] is None

    running = ctx.jobs.enqueue(JobKind.SOURCE_INGEST, None, {})
    ctx.jobs.claim("worker-1")
    response = client.post(f"/api/jobs/{running.id}/cancel", headers=WEB)
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.json()["stop_requested_at"] is not None
    assert response.json()["finished_at"] is None
    events = ctx.jobs.events_since(0)
    assert events[-1].kind == "stop_requested"
    assert events[-1].job_id == running.id
