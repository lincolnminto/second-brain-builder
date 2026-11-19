"""The worker heartbeat. The worker records it; the UI shows when the worker is offline."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ValidationError

from second_brain_builder.services._common import read_setting, write_setting
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.models import WorkerStatus

HEARTBEAT_SETTING = "worker.heartbeat"
HEARTBEAT_INTERVAL = timedelta(seconds=15)
"""How often the worker should call ``record_worker_heartbeat``."""
OFFLINE_AFTER = timedelta(seconds=60)
"""The worker counts as offline when its last heartbeat is older than this."""


class _Heartbeat(BaseModel):
    worker_id: str
    at: datetime


def record_worker_heartbeat(ctx: AppContext, worker_id: str) -> None:
    """Record that the worker ``worker_id`` runs now."""
    heartbeat = _Heartbeat(worker_id=worker_id, at=ctx.clock())
    with ctx.sessions.begin() as session:
        write_setting(session, HEARTBEAT_SETTING, heartbeat.model_dump(mode="json"))


def get_worker_status(ctx: AppContext) -> WorkerStatus:
    """Whether a worker recorded a heartbeat within ``OFFLINE_AFTER``."""
    with ctx.read_sessions() as session:
        value = read_setting(session, HEARTBEAT_SETTING)
    if value is None:
        return WorkerStatus(online=False)
    try:
        heartbeat = _Heartbeat.model_validate(value)
    except ValidationError:
        return WorkerStatus(online=False)
    online = ctx.clock() - heartbeat.at <= OFFLINE_AFTER
    return WorkerStatus(online=online, worker_id=heartbeat.worker_id, last_seen_at=heartbeat.at)
