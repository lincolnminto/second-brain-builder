"""Ingest: the LLM agent loop that writes Wiki Pages, Source removal, and saved answers.

See docs/architecture.md ("Module contracts", ingest) for the contract that this
package implements.
"""

from second_brain_builder.ingest._wiki import CanonicalSource
from second_brain_builder.ingest.answers import UNKNOWN_AGENT, save_answer
from second_brain_builder.ingest.chunks import (
    CHARS_PER_TOKEN,
    CHUNK_CHARS,
    CHUNK_TOKENS,
    Chunk,
    split_into_chunks,
)
from second_brain_builder.ingest.loop import MAX_PAGES_PER_PART, ingest_source
from second_brain_builder.ingest.map_reduce import (
    MAX_REQUEST_CHARS,
    MapReduceIngest,
    extract_part_notes,
    group_notes,
    write_group_page,
    write_summary_from_points,
)
from second_brain_builder.ingest.map_reduce_models import (
    NOTES_SCHEMA_VERSION,
    ExtractedNotes,
    GroupFact,
    MapReduceResult,
    MapReduceState,
    NoteFact,
    NoteGroup,
    NoteItem,
    NotesExtractionError,
    PageWriteResult,
    PartNotes,
    ReduceError,
    SummaryPoint,
)
from second_brain_builder.ingest.models import (
    IngestEvent,
    IngestResult,
    IngestStop,
    InvalidAnswerError,
)
from second_brain_builder.ingest.removal import remove_source

__all__ = [
    "CHARS_PER_TOKEN",
    "CHUNK_CHARS",
    "CHUNK_TOKENS",
    "MAX_PAGES_PER_PART",
    "MAX_REQUEST_CHARS",
    "NOTES_SCHEMA_VERSION",
    "UNKNOWN_AGENT",
    "CanonicalSource",
    "Chunk",
    "ExtractedNotes",
    "GroupFact",
    "IngestEvent",
    "IngestResult",
    "IngestStop",
    "InvalidAnswerError",
    "MapReduceIngest",
    "MapReduceResult",
    "MapReduceState",
    "NoteFact",
    "NoteGroup",
    "NoteItem",
    "NotesExtractionError",
    "PageWriteResult",
    "PartNotes",
    "ReduceError",
    "SummaryPoint",
    "extract_part_notes",
    "group_notes",
    "ingest_source",
    "remove_source",
    "save_answer",
    "split_into_chunks",
    "write_group_page",
    "write_summary_from_points",
]
