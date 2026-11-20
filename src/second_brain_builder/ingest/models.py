"""Models that cross the boundary of the ingest module."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field

from second_brain_builder.domain import StrEnum
from second_brain_builder.providers import Usage

EventKind = Literal["progress", "page_written", "page_deleted"]


class IngestEvent(BaseModel):
    """One progress report for the job that runs an Ingest or a removal.

    ``data`` is JSON-ready (strings, numbers, lists), so ``store`` can save it as is:

    - ``progress``: ``source`` (the Source id), ``chunk`` (1-based), ``chunks`` (count).
    - ``page_written``: ``path``, ``title``, ``type``, and ``links``: the bundle paths
      (no leading ``/``) that the page body links to, citations included.
    - ``page_deleted``: ``path``, ``title``, ``type``.
    """

    kind: EventKind
    data: dict[str, Any]


class IngestStop(StrEnum):
    """Why an Ingest stopped.

    Only ``COMPLETED`` means that the model worked on the whole Canonical Document.
    Completed Parts stay written. Refusal and missing summary discard the current Part.
    """

    COMPLETED = "completed"
    """Every chunk of the Canonical Document was sent to the model, and the first chunk
    wrote the summary page. A Canonical Document without text has no chunks: its
    Ingest is completed without a model request and without pages."""
    USER_REQUESTED = "user_requested"
    """The caller requested a stop at a safe point; completed work is kept."""
    TOKEN_BUDGET = "token_budget"  # noqa: S105 - a stop reason, not a password
    """Starting the next Part would exceed the estimated run budget."""
    PAGE_LIMIT = "page_limit"
    """Legacy stop reason, retained for existing progress files."""
    EXTRACTION_FAILED = "extraction_failed"
    """Map Parts failed extraction; Continue retries failed and missing Parts."""
    REDUCE_FAILED = "reduce_failed"
    """Reduce groups failed; Continue retries failed groups, then remaining groups."""
    ERROR = "error"
    """An unexpected error interrupted the run."""
    REFUSED = "refused"
    """The model refused to answer (``stop_reason`` ``refusal``). Later chunks were not sent."""
    NO_SUMMARY = "no_summary"
    """The first chunk ended without the summary page: the model stopped calling tools,
    or it used all its requests for the chunk. Later chunks were not sent."""


class IngestResult(BaseModel):
    """What an Ingest or a removal changed in the Brain.

    ``written`` holds each page that was written (created or changed) one time: in
    the order of its first write for an Ingest, sorted by path for a removal.
    ``deleted`` is sorted by path. Paths are relative to the Brain root.
    ``parts_done`` is the total completed prefix, including Parts before this run.
    An interrupted Part is retried on the next run; it does not advance the prefix.
    """

    written: list[PurePosixPath] = Field(default_factory=list)
    deleted: list[PurePosixPath] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    stopped: IngestStop = IngestStop.COMPLETED
    parts_done: int = 0
    parts_total: int = 0
    completed_usage: Usage = Field(default_factory=Usage)
    notes: list[str] = Field(default_factory=list)
    context_written: list[PurePosixPath] = Field(default_factory=list)


class InvalidAnswerError(ValueError):
    """``save_answer`` got a page that it cannot write. ``problems`` says why, one item each."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems
