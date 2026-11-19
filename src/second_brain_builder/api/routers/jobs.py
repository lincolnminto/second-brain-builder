"""Jobs and the live event stream (Server-Sent Events)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import anyio
import anyio.to_thread
from fastapi import APIRouter, Header, Query
from sse_starlette import EventSourceResponse, ServerSentEvent

from second_brain_builder import services
from second_brain_builder.api.schemas import JobEventView, JobView
from second_brain_builder.api.state import Ctx, State
from second_brain_builder.services import AppContext, JobStatus

router = APIRouter(tags=["jobs"])

EVENT_BATCH = 500
"""Events read per poll. A full batch is followed by the next read without a pause."""
RETRY_MILLISECONDS = 2000
"""How long a browser waits before it reconnects a closed stream."""
PING_SECONDS = 15
SEND_TIMEOUT_SECONDS = 30


@router.get("/jobs")
def list_jobs(
    ctx: Ctx,
    brain_id: str | None = None,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[JobView]:
    """Jobs, newest first, of one Brain or of all, optionally with some statuses."""
    return services.list_jobs(ctx, brain_id, status, limit)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, ctx: Ctx) -> JobView:
    return services.get_job(ctx, job_id)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: int, ctx: Ctx) -> JobView:
    """Enqueue a new job with the kind and payload of a failed or cancelled job."""
    return services.retry_job(ctx, job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, ctx: Ctx) -> JobView:
    """Cancel a queued job. A job that started cannot be cancelled."""
    return services.cancel_job(ctx, job_id)


@router.get(
    "/events",
    # The route class stays JSON so that errors are documented as JSON; api.openapi
    # moves the JobEventView of the 200 response to text/event-stream.
    responses={
        200: {
            "model": JobEventView,
            "description": "A Server-Sent Events stream. Each event has the event id as "
            "`id` and a JobEventView as JSON `data`.",
        }
    },
)
async def stream_events(
    state: State,
    brain_id: str | None = None,
    after: Annotated[
        int | None, Query(ge=0, description="Send the events with a greater id.")
    ] = None,
    last_event_id: Annotated[
        str | None, Header(description="Set by the browser when it reconnects.")
    ] = None,
) -> EventSourceResponse:
    """Job events as they happen, oldest first.

    The stream starts after ``Last-Event-ID`` (a reconnect), else after ``after``,
    else with the next new event. ``brain_id`` keeps only the events of one Brain.
    """
    ctx = state.context
    if brain_id is not None:
        await anyio.to_thread.run_sync(services.get_brain, ctx, brain_id)
    start = _start_id(last_event_id, after)
    if start is None:
        start = await anyio.to_thread.run_sync(services.latest_event_id, ctx)
    return EventSourceResponse(
        _events(ctx, start, brain_id, state.event_poll_seconds),
        ping=PING_SECONDS,
        send_timeout=SEND_TIMEOUT_SECONDS,
    )


def _start_id(last_event_id: str | None, after: int | None) -> int | None:
    if last_event_id is not None and last_event_id.strip().isdigit():
        return int(last_event_id.strip())
    return after


async def _events(
    ctx: AppContext, after_id: int, brain_id: str | None, poll_seconds: float
) -> AsyncIterator[ServerSentEvent]:
    yield ServerSentEvent(retry=RETRY_MILLISECONDS)
    last = after_id
    while True:
        events = await anyio.to_thread.run_sync(
            services.events_since, ctx, last, brain_id, EVENT_BATCH
        )
        for event in events:
            last = event.id
            yield ServerSentEvent(data=event.model_dump_json(), id=str(event.id))
        if len(events) < EVENT_BATCH:
            await anyio.sleep(poll_seconds)
