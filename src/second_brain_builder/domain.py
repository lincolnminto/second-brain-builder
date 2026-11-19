"""Cross-module types: ids, enums, and small value models."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class StrEnum(str, Enum):
    """A string enum whose ``str()`` and ``format()`` give the member value.

    Python 3.11 changed ``format()`` of ``(str, Enum)`` members from the value
    (``"source.add"``) to the member name (``"JobKind.SOURCE_ADD"``). This base
    class gives the value on every supported version, like ``enum.StrEnum``
    (Python 3.11+), so f-strings in commit messages, payloads, and logs agree.
    """

    def __str__(self) -> str:
        return str(self.value)


class JobKind(StrEnum):
    """What a job does. One worker handler exists per kind."""

    BRAIN_CREATE = "brain.create"
    BRAIN_SYNC = "brain.sync"
    SOURCE_ADD = "source.add"
    SOURCE_INGEST = "source.ingest"
    SOURCE_RECONVERT = "source.reconvert"
    SOURCE_MOVE = "source.move"
    SOURCE_DELETE = "source.delete"
    ANSWER_SAVE = "answer.save"


class JobStatus(StrEnum):
    """Job state. Flow: queued -> running -> succeeded | failed | cancelled."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceStatus(StrEnum):
    """Where a Source is in the Conversion and Ingest steps."""

    QUEUED = "queued"
    CONVERTING = "converting"
    CONVERTED = "converted"
    INGESTING = "ingesting"
    INGESTED = "ingested"
    FAILED = "failed"


class BrainStatus(StrEnum):
    """Brain state in the database."""

    CREATING = "creating"
    READY = "ready"
    ERROR = "error"


class TaskName(StrEnum):
    """A task that the user can assign to a local model or to a Provider."""

    INGEST = "ingest"
    TRANSCRIPTION = "transcription"


class TaskMode(StrEnum):
    """Where a task runs: in the worker (local) or through a Provider."""

    LOCAL = "local"
    PROVIDER = "provider"


class Usage(BaseModel):
    """LLM tokens that one or more Provider calls used.

    ``providers`` fills it, ``ingest`` adds it up per Source, and ``store``
    saves it on the job. It lives here because ``store`` imports only ``domain``.
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class Segment(BaseModel):
    """One timed part of a transcript. Times are seconds from the start of the audio."""

    start: float
    end: float
    text: str


@runtime_checkable
class Transcriber(Protocol):
    """Speech to text. ``providers`` implements it, and ``conversion`` calls it.

    It lives here because ``conversion`` imports no other internal module.
    It is runtime-checkable so that a Pydantic model with
    ``arbitrary_types_allowed`` can hold one as a field.
    """

    def transcribe(self, audio: Path, language: str | None) -> list[Segment]:
        """Return the transcript of ``audio``. ``language`` is a BCP 47 tag, or None to detect."""
        ...
