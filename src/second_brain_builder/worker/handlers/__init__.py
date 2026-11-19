"""One handler per job kind (docs/architecture.md, "Jobs").

A handler gets the ``JobRun`` of its job. It returns when the job succeeded and
raises when it failed; the runner then restores the clone and stores the error.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from second_brain_builder.domain import JobKind
from second_brain_builder.worker.context import JobRun
from second_brain_builder.worker.handlers.answers import save_answer
from second_brain_builder.worker.handlers.brains import create_brain, sync_brain
from second_brain_builder.worker.handlers.sources import (
    add_source,
    delete,
    ingest,
    move,
    reconvert,
)

Handler = Callable[[JobRun], None]

HANDLERS: Mapping[JobKind, Handler] = MappingProxyType(
    {
        JobKind.BRAIN_CREATE: create_brain,
        JobKind.BRAIN_SYNC: sync_brain,
        JobKind.SOURCE_ADD: add_source,
        JobKind.SOURCE_INGEST: ingest,
        JobKind.SOURCE_RECONVERT: reconvert,
        JobKind.SOURCE_MOVE: move,
        JobKind.SOURCE_DELETE: delete,
        JobKind.ANSWER_SAVE: save_answer,
    }
)
"""The handler of each job kind."""

__all__ = ["HANDLERS", "Handler"]
