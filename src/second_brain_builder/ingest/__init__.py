"""Ingest: the LLM agent loop that writes Wiki Pages, Source removal, and saved answers.

See docs/architecture.md ("Module contracts", ingest) for the contract that this
package implements.
"""

from second_brain_builder.ingest.answers import UNKNOWN_AGENT, save_answer
from second_brain_builder.ingest.chunks import (
    CHARS_PER_TOKEN,
    CHUNK_CHARS,
    CHUNK_TOKENS,
    Chunk,
    split_into_chunks,
)
from second_brain_builder.ingest.loop import MAX_PAGES_PER_SOURCE, ingest_source
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
    "MAX_PAGES_PER_SOURCE",
    "UNKNOWN_AGENT",
    "Chunk",
    "IngestEvent",
    "IngestResult",
    "IngestStop",
    "InvalidAnswerError",
    "ingest_source",
    "remove_source",
    "save_answer",
    "split_into_chunks",
]
