"""JSON contracts for map extraction, deterministic grouping, and worker checkpoints."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from second_brain_builder.ingest.models import IngestStop
from second_brain_builder.providers import Usage

NOTES_SCHEMA_VERSION: Literal[1] = 1
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Kind = Literal["entity", "concept"]
Subtype = Literal["person", "organization", "place", "event", "work", "idea", "method", "term"]


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NoteFact(_Document):
    """A short claim in the Brain language, with an optional Source locator."""

    text: Text
    locator: Text | None = None

    @model_validator(mode="before")
    @classmethod
    def _plain_fact(cls, value: object) -> object:
        return {"text": value} if isinstance(value, str) else value


class NoteItem(_Document):
    kind: Kind
    subtype: Subtype
    name: Text
    aliases: list[Text] = Field(default_factory=list)
    facts: list[NoteFact] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _unknown_subtype(cls, value: object) -> object:
        if isinstance(value, dict):
            value = {
                key: field.strip()
                if key in {"kind", "subtype"} and isinstance(field, str)
                else field
                for key, field in value.items()
            }
            if "subtype" in value and value["subtype"] not in get_args(Subtype):
                return {**value, "subtype": "term", "kind": "concept"}
        return value

    @model_validator(mode="after")
    def _kind_matches_subtype(self) -> NoteItem:
        expected = "concept" if self.subtype in {"idea", "method", "term"} else "entity"
        if self.kind != expected:
            raise ValueError("kind does not match subtype")
        return self


class ExtractedNotes(_Document):
    """The exact schema of the sole map tool; metadata is supplied by code."""

    items: list[NoteItem]
    summary_points: list[Text]


class PartNotes(ExtractedNotes):
    """A reusable map document. ``chunk_chars`` is the configured Part size.

    Reuse requires the same schema version, document digest, configured size, and
    Part number. Usage includes corrective retries and is useful for estimates.
    """

    schema_version: Literal[1] = NOTES_SCHEMA_VERSION
    canonical_sha256: Digest
    chunk_chars: int = Field(gt=0)
    part_number: int = Field(ge=1)
    usage: Usage = Field(default_factory=Usage)


class GroupFact(NoteFact):
    part_number: int = Field(ge=1)


class NoteGroup(_Document):
    key: Text
    kind: Kind
    subtype: Subtype
    name: Text
    aliases: list[Text]
    facts: list[GroupFact] = Field(min_length=1)


class SummaryPoint(_Document):
    text: Text
    part_numbers: list[Annotated[int, Field(ge=1)]] = Field(min_length=1)


class PageWriteResult(_Document):
    path: PurePosixPath
    updates: int = Field(ge=1)
    usage: Usage


class MapPartFailure(_Document):
    part_number: int = Field(ge=1)
    error_kind: Text


class ReduceGroupFailure(_Document):
    group_index: int = Field(ge=1)
    fact_count: int = Field(ge=1)
    error_kind: Text


class MapReduceState(_Document):
    """Worker-owned checkpoint, independent of any storage format or location.

    Map documents are passed separately to ``run``. Persist them before this
    checkpoint; the completed map numbers must have matching documents on resume.
    Groups and disjoint targets are fixed before reduce dispatch. The position
    counts attempts and skips; failures are rolled back and retried on Continue.
    """

    schema_version: Literal[1] = NOTES_SCHEMA_VERSION
    canonical_sha256: Digest
    chunk_chars: int = Field(gt=0)
    parts_total: int = Field(ge=0)
    phase: Literal["map", "reduce", "complete"] = "map"
    map_parts_done: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    map_failed_parts: list[MapPartFailure] = Field(default_factory=list)
    groups: list[NoteGroup] = Field(default_factory=list)
    reduce_groups_done: int = Field(default=0, ge=0)
    reduce_groups_skipped: int = Field(default=0, ge=0)
    reduce_failed_groups: list[ReduceGroupFailure] = Field(default_factory=list)
    reduce_targets: list[PurePosixPath] = Field(default_factory=list)
    summary_done: bool = False
    written: list[PurePosixPath] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    map_usage: Usage = Field(default_factory=Usage)
    reduce_usage: Usage = Field(default_factory=Usage)

    @property
    def reduce_groups_total(self) -> int:
        return len(self.groups)

    @model_validator(mode="after")
    def _positions(self) -> MapReduceState:
        failed = [part.part_number for part in self.map_failed_parts]
        if failed != sorted(set(failed)):
            raise ValueError("map_failed_parts must be sorted and unique")
        if any(number > self.parts_total or number in self.map_parts_done for number in failed):
            raise ValueError("failed map Part must be in range and incomplete")
        if failed and self.phase != "map":
            raise ValueError("reduce requires no failed map Parts")
        if self.map_parts_done != sorted(set(self.map_parts_done)):
            raise ValueError("map_parts_done must be sorted and unique")
        if any(number > self.parts_total for number in self.map_parts_done):
            raise ValueError("map Part exceeds parts_total")
        if self.reduce_groups_done > len(self.groups):
            raise ValueError("reduce position exceeds group count")
        if self.reduce_targets and len(self.reduce_targets) != len(self.groups):
            raise ValueError("each reduce group requires one target")
        if len(set(self.reduce_targets)) != len(self.reduce_targets):
            raise ValueError("reduce targets must be unique")
        failed_groups = [group.group_index for group in self.reduce_failed_groups]
        if failed_groups != sorted(set(failed_groups)) or any(
            number > self.reduce_groups_done for number in failed_groups
        ):
            raise ValueError("failed reduce groups must be unique, sorted and already attempted")
        if self.reduce_groups_skipped > self.reduce_groups_done:
            raise ValueError("skipped groups exceed reduce position")
        if self.phase == "map":
            if self.groups or self.reduce_groups_done or self.summary_done:
                raise ValueError("map phase cannot have reduce progress")
        elif len(self.map_parts_done) != self.parts_total:
            raise ValueError("reduce requires every map Part")
        if self.summary_done and self.reduce_groups_done != len(self.groups):
            raise ValueError("summary requires every reduce group")
        if self.phase == "complete" and not self.summary_done:
            raise ValueError("complete phase requires the summary")
        if self.phase == "complete" and self.reduce_failed_groups:
            raise ValueError("complete phase requires no failed groups")
        return self


class MapReduceResult(_Document):
    state: MapReduceState
    map_results: list[PartNotes]
    written: list[PurePosixPath] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    stopped: IngestStop = IngestStop.COMPLETED


class NotesExtractionError(ValueError):
    """The model could not produce usable structured notes within its limits."""


class ReduceError(RuntimeError):
    """A group/summary could not finish safely; its page writes were rolled back."""
