"""SQLite FTS5 search index over Canonical Documents and Wiki Pages.

See docs/architecture.md ("Module contracts", search) for the contract that this
package implements.
"""

from second_brain_builder.search.index import SearchIndex
from second_brain_builder.search.models import HIGHLIGHT_MARK, SearchHit
from second_brain_builder.search.schema import SCHEMA_VERSION

__all__ = [
    "HIGHLIGHT_MARK",
    "SCHEMA_VERSION",
    "SearchHit",
    "SearchIndex",
]
