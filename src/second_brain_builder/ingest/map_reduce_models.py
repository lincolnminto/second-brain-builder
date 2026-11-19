"""JSON contracts for map extraction, deterministic grouping, and worker checkpoints."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from second_brain_builder.ingest.models import IngestStop
from second_brain_builder.providers import Usage

NOTES_SCHEMA_VERSION: Literal[1] = 1
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Kind = Literal["entity", "concept"]
Subtype = Literal["person", "organization", "place", "event", "work", "idea", "method", "term"]


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoteFact(_Document):
    """A short claim in the Brain language, with an optional Source locator."""

    text: Text
    locator: Text | None = None


class NoteItem(_Document):
    kind: Kind
    subtype: Subtype
    name: Text
    aliases: list[Text]
    facts: list[NoteFact] = Field(min_length=1)

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


class MapReduceState(_Document):
    """Worker-owned checkpoint, independent of any storage format or location.

    Map documents are passed separately to ``run``. Persist them before this
    checkpoint; the completed map numbers must have matching documents on resume.
    Groups are fixed once map completes. Reduce advances only after a whole group;
    a failed group is rolled back and is retried from its first fact batch.
    """

    schema_version: Literal[1] = NOTES_SCHEMA_VERSION
    canonical_sha256: Digest
    chunk_chars: int = Field(gt=0)
    parts_total: int = Field(ge=0)
    phase: Literal["map", "reduce", "complete"] = "map"
    map_parts_done: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    groups: list[NoteGroup] = Field(default_factory=list)
    reduce_groups_done: int = Field(default=0, ge=0)
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
        if self.map_parts_done != sorted(set(self.map_parts_done)):
            raise ValueError("map_parts_done must be sorted and unique")
        if any(number > self.parts_total for number in self.map_parts_done):
            raise ValueError("map Part exceeds parts_total")
        if self.reduce_groups_done > len(self.groups):
            raise ValueError("reduce position exceeds group count")
        if self.phase == "map":
            if self.groups or self.reduce_groups_done or self.summary_done:
                raise ValueError("map phase cannot have reduce progress")
        elif len(self.map_parts_done) != self.parts_total:
            raise ValueError("reduce requires every map Part")
        if self.summary_done and self.reduce_groups_done != len(self.groups):
            raise ValueError("summary requires every reduce group")
        if self.phase == "complete" and not self.summary_done:
            raise ValueError("complete phase requires the summary")
        return self


class MapReduceResult(_Document):
    state: MapReduceState
    map_results: list[PartNotes]
    written: list[PurePosixPath] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    stopped: IngestStop = IngestStop.COMPLETED


class NotesExtractionError(ValueError):
    """The model failed the structured tool contract twice (including a retry)."""


class ReduceError(RuntimeError):
    """A group/summary could not finish safely; its page writes were rolled back."""
