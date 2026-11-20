"""SQLite FTS5 search index over Canonical Documents and Wiki Pages.

Also the segmentation of Markdown bodies that the document viewer and the passage
index share (``segment_markdown``), and the text helpers of the citation resolver
(``cited_sentence``, ``parse_locator``, ``key_terms``).

See docs/architecture.md ("Module contracts", search) for the contract that this
package implements.
"""

from second_brain_builder.search.citations import (
    MAX_KEY_TERMS,
    CitedSentence,
    Locator,
    cited_sentence,
    key_terms,
    parse_locator,
)
from second_brain_builder.search.index import SearchIndex
from second_brain_builder.search.models import HIGHLIGHT_MARK, PassageHit, SearchHit
from second_brain_builder.search.schema import SCHEMA_VERSION
from second_brain_builder.search.segments import (
    PASSAGE_CHARS,
    SEGMENT_CHARS,
    Heading,
    Segment,
    Segmentation,
    segment_markdown,
)

__all__ = [
    "HIGHLIGHT_MARK",
    "MAX_KEY_TERMS",
    "PASSAGE_CHARS",
    "SCHEMA_VERSION",
    "SEGMENT_CHARS",
    "CitedSentence",
    "Heading",
    "Locator",
    "PassageHit",
    "SearchHit",
    "SearchIndex",
    "Segment",
    "Segmentation",
    "cited_sentence",
    "key_terms",
    "parse_locator",
    "segment_markdown",
]
