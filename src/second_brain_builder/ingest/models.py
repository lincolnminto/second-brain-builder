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
    After any other value, the pages that the model wrote stay written, and the
    caller decides what to do with them.
    """

    COMPLETED = "completed"
    """Every chunk of the Canonical Document was sent to the model, and the first chunk
    wrote the summary page. A Canonical Document without text has no chunks: its
    Ingest is completed without a model request and without pages."""
    TOKEN_BUDGET = "token_budget"  # noqa: S105 - a stop reason, not a password
    """The tokens used reached ``max_tokens_budget``. Later chunks were not sent."""
    PAGE_LIMIT = "page_limit"
    """The model wrote ``MAX_PAGES_PER_SOURCE`` pages. Later chunks were not sent."""
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
    """

    written: list[PurePosixPath] = Field(default_factory=list)
    deleted: list[PurePosixPath] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    stopped: IngestStop = IngestStop.COMPLETED


class InvalidAnswerError(ValueError):
    """``save_answer`` got a page that it cannot write. ``problems`` says why, one item each."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems
