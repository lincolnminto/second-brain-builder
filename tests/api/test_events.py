"""``GET /api/events``: job events as Server-Sent Events, with ``?after=`` and ``Last-Event-ID``."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from second_brain_builder.services import AppContext, sync_brain

from .conftest import insert_brain

if TYPE_CHECKING:
    from .conftest import BrainFixture

pytestmark = pytest.mark.timeout(60)


@dataclass(frozen=True)
class Event:
    id: int
    data: dict[str, Any]


def read_events(client: TestClient, count: int, **request: Any) -> tuple[list[Event], int | None]:
    """Open the stream, read ``count`` events, and close it. Also returns the ``retry`` value."""
    events: list[Event] = []
    retry: int | None = None
    with client.stream("GET", "/api/events", **request) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        event_id: int | None = None
        for line in response.iter_lines():
            if line.startswith("retry:"):
                retry = int(line.removeprefix("retry:").strip())
            elif line.startswith("id:"):
                event_id = int(line.removeprefix("id:").strip())
            elif line.startswith("data:"):
                assert event_id is not None
                events.append(Event(id=event_id, data=json.loads(line.removeprefix("data:"))))
                if len(events) == count:
                    break
    return events, retry


def event_ids(ctx: AppContext, brain_id: str | None = None) -> list[int]:
    return [event.id for event in ctx.jobs.events_since(0, brain_id)]


def test_stream_sends_events_after_the_given_id(
    ctx: AppContext, client: TestClient, brain: BrainFixture
) -> None:
    first = sync_brain(ctx, brain.id)
    second = sync_brain(ctx, brain.id)
    ids = event_ids(ctx)

    events, retry = read_events(client, 2, params={"after": 0})

    assert [event.id for event in events] == ids
    assert [event.data["job_id"] for event in events] == [first.id, second.id]
    assert events[0].data["kind"] == "status"
    assert events[0].data["data"] == {"status": "queued"}
    assert events[0].data["brain_id"] == brain.id
    assert retry is not None and retry > 0


def test_last_event_id_takes_precedence_over_after(
    ctx: AppContext, client: TestClient, brain: BrainFixture
) -> None:
    for _ in range(3):
        sync_brain(ctx, brain.id)
    ids = event_ids(ctx)

    events, _ = read_events(client, 1, params={"after": 0}, headers={"Last-Event-ID": str(ids[1])})

    assert [event.id for event in events] == [ids[2]]


def test_stream_without_a_start_sends_only_new_events(
    ctx: AppContext, client: TestClient, brain: BrainFixture
) -> None:
    sync_brain(ctx, brain.id)
    old_ids = event_ids(ctx)
    later = threading.Timer(0.3, sync_brain, args=(ctx, brain.id))
    later.start()
    try:
        events, _ = read_events(client, 1)
    finally:
        later.join()

    assert events[0].id > max(old_ids)
    assert events[0].id == event_ids(ctx)[-1]


def test_brain_filter_leaves_out_other_brains(
    ctx: AppContext, client: TestClient, brain: BrainFixture
) -> None:
    other = insert_brain(ctx, "test-user/other-notes", name="Other Notes")
    sync_brain(ctx, brain.id)
    sync_brain(ctx, other)
    sync_brain(ctx, brain.id)

    events, _ = read_events(client, 2, params={"after": 0, "brain_id": brain.id})

    assert [event.id for event in events] == event_ids(ctx, brain.id)
    assert {event.data["brain_id"] for event in events} == {brain.id}


def test_unknown_brain_is_404_before_the_stream_starts(client: TestClient) -> None:
    response = client.get("/api/events", params={"brain_id": "no-such-brain"})

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
