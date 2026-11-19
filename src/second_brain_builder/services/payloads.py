"""Job payloads: one model per job kind (docs/architecture.md, "Jobs").

Services build every payload with these models, and the worker can parse
``Job.payload`` with ``parse_payload``, so both sides agree on the keys. A payload
is stored as JSON: paths and folders are strings, and ``folder`` is a slug path
(``""`` is the root of ``sources/``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from second_brain_builder.domain import JobKind


class JobPayload(BaseModel):
    """Base of every payload: each job belongs to one Brain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    brain_id: str

    def to_json(self) -> dict[str, Any]:
        """The payload as a JSON-compatible dict for ``JobQueue.enqueue``."""
        return self.model_dump(mode="json")


class BrainCreatePayload(JobPayload):
    repo_full_name: str
    create_repo: bool
    """Create the private repository first. False for an existing repository."""


class BrainSyncPayload(JobPayload):
    pass


class SourceAddPayload(JobPayload):
    upload_path: str
    """Absolute path of the staged upload: ``<data_dir>/uploads/<uuid>/upload.<ext>``."""
    original_filename: str
    folder: str
    sha256: str


class SourceJobPayload(JobPayload):
    """``source.ingest``, ``source.reconvert``, and ``source.delete``."""

    source_slug: str


class SourceMovePayload(JobPayload):
    source_slug: str
    folder: str


class AnswerSavePayload(JobPayload):
    title: str
    body: str
    source_ids: list[str]
    description: str
    tags: list[str]


PAYLOAD_MODELS: dict[JobKind, type[JobPayload]] = {
    JobKind.BRAIN_CREATE: BrainCreatePayload,
    JobKind.BRAIN_SYNC: BrainSyncPayload,
    JobKind.SOURCE_ADD: SourceAddPayload,
    JobKind.SOURCE_INGEST: SourceJobPayload,
    JobKind.SOURCE_RECONVERT: SourceJobPayload,
    JobKind.SOURCE_MOVE: SourceMovePayload,
    JobKind.SOURCE_DELETE: SourceJobPayload,
    JobKind.ANSWER_SAVE: AnswerSavePayload,
}


def parse_payload(kind: JobKind, payload: dict[str, Any]) -> JobPayload:
    """Validate a stored payload against the model of its job kind.

    Raises:
        pydantic.ValidationError: The payload does not match.
    """
    return PAYLOAD_MODELS[kind].model_validate(payload)
