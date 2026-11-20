"""The tables of the search index file.

Each Brain has its own tables. FTS5 computes BM25 from the statistics of one table
(the number of rows, the average length, and how many rows have each term), so with
one table per Brain, the ranking inside a Brain does not depend on other Brains,
and a query reads only the index of its own Brain.

The ``brains`` table gives each Brain id an integer key, which names its tables:

``brain_<key>_documents``
    One row per page: path, type, title, description, tags, and a digest.
``brain_<key>_passages``
    The plain body text of each page (``search.text.markdown_to_text``), cut into
    passages (``search.text.split_passages``). A passage id is
    ``(document id << PASSAGE_BITS) + ordinal``, so the passages of a document
    are one id range, in order.
``brain_<key>_documents_fts``
    FTS5 over title, description, tags, and body. It finds and ranks the pages.
    It keeps no copy of the text (``content=''``): the body is in the passages.
``brain_<key>_fields_fts``
    FTS5 over title, description, and tags, read from the documents table. It
    tells which fields match, and gives description snippets.
``brain_<key>_passages_fts``
    FTS5 over the passages. It gives body snippets: the cost of FTS5
    ``snippet()`` grows with the square of the matches in the text, so it runs on
    one passage, not on a whole body.
``brain_<key>_source_passages``
    FTS5 over the passage index of the Canonical Documents (``type: source``)
    only: one row per segment of ``search.segments.segment_markdown`` with
    ``PASSAGE_CHARS``. The ``trail`` column holds the heading trail (one heading
    per line) and ``text`` the plain text of the segment; ``segment``,
    ``char_start``, and ``char_end`` locate it in the body. The table stores its
    own copy of the text, because the text is returned as the passage. A row id is
    ``(document id << PASSAGE_BITS) + segment index``, as for the passages.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

SCHEMA_VERSION = 3
"""The schema of the index file, kept in ``PRAGMA user_version``.

Increase it when the tables, the tokenizer, or the text that is indexed for a page
change. ``SearchIndex`` then empties the index, and each Brain needs a ``rebuild``.
"""

PASSAGE_BITS = 20
"""The low bits of a passage id that hold its ordinal in the document."""
MAX_PASSAGES = 1 << PASSAGE_BITS
"""The most passages that one document can have."""

_TOKENIZE = "tokenize = 'unicode61 remove_diacritics 2'"

_SHARED_TABLES = (
    """
    CREATE TABLE brains (
        key INTEGER PRIMARY KEY AUTOINCREMENT,
        brain_id TEXT NOT NULL UNIQUE
    )
    """,
)


def first_passage_id(document_id: int) -> int:
    """The id of the first passage of a document."""
    return document_id << PASSAGE_BITS


def last_passage_id(document_id: int) -> int:
    """The highest id that a passage of the document can have."""
    return first_passage_id(document_id) + MAX_PASSAGES - 1


def passage_ordinal(passage_id: int) -> int:
    """The position of a passage in its document, from 0."""
    return passage_id & (MAX_PASSAGES - 1)


@dataclass(frozen=True)
class BrainTables:
    """The names of the tables of one Brain.

    The names come from the integer ``key`` only, so SQL can contain them; Brain
    ids never go into SQL text.
    """

    key: int

    @property
    def documents(self) -> str:
        return f"brain_{self.key:d}_documents"

    @property
    def passages(self) -> str:
        return f"brain_{self.key:d}_passages"

    @property
    def documents_fts(self) -> str:
        return f"brain_{self.key:d}_documents_fts"

    @property
    def fields_fts(self) -> str:
        return f"brain_{self.key:d}_fields_fts"

    @property
    def passages_fts(self) -> str:
        return f"brain_{self.key:d}_passages_fts"

    @property
    def source_passages(self) -> str:
        return f"brain_{self.key:d}_source_passages"

    def sql(self, template: str) -> str:
        """``template`` with the names of these tables in place of ``{documents}``,
        ``{passages}``, ``{documents_fts}``, ``{fields_fts}``, ``{passages_fts}``, and
        ``{source_passages}``.

        Only table names go into SQL text this way; values are always parameters.
        """
        return template.format(
            documents=self.documents,
            passages=self.passages,
            documents_fts=self.documents_fts,
            fields_fts=self.fields_fts,
            passages_fts=self.passages_fts,
            source_passages=self.source_passages,
        )

    def create_statements(self) -> tuple[str, ...]:
        """The statements that create the tables."""
        return (
            f"""
            CREATE TABLE {self.documents} (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                tags TEXT NOT NULL,
                passage_count INTEGER NOT NULL,
                digest TEXT NOT NULL
            )
            """,
            f"CREATE TABLE {self.passages} (id INTEGER PRIMARY KEY, text TEXT NOT NULL)",
            f"""
            CREATE VIRTUAL TABLE {self.documents_fts} USING fts5(
                title, description, tags, body, content = '', {_TOKENIZE}
            )
            """,
            f"""
            CREATE VIRTUAL TABLE {self.fields_fts} USING fts5(
                title, description, tags,
                content = '{self.documents}', content_rowid = 'id', {_TOKENIZE}
            )
            """,
            f"""
            CREATE VIRTUAL TABLE {self.passages_fts} USING fts5(
                text, content = '{self.passages}', content_rowid = 'id', {_TOKENIZE}
            )
            """,
            f"""
            CREATE VIRTUAL TABLE {self.source_passages} USING fts5(
                trail, text, segment UNINDEXED, char_start UNINDEXED, char_end UNINDEXED,
                {_TOKENIZE}
            )
            """,
        )

    def drop_statements(self) -> tuple[str, ...]:
        """The statements that drop the tables."""
        return tuple(
            f"DROP TABLE IF EXISTS {name}"
            for name in (
                self.documents_fts,
                self.fields_fts,
                self.passages_fts,
                self.source_passages,
                self.documents,
                self.passages,
            )
        )


def schema_version(connection: sqlite3.Connection) -> int:
    """The ``SCHEMA_VERSION`` of the file, or 0 for a new file."""
    (version,) = connection.execute("PRAGMA user_version").fetchone()
    return int(version)


def reset_schema(connection: sqlite3.Connection) -> None:
    """Drop every table of the file, and create the shared tables of ``SCHEMA_VERSION``.

    Call it inside a write transaction.
    """
    # Virtual tables first: dropping one also drops its shadow tables.
    for kind in ("virtual", "table", "view"):
        for name in _schema_objects(connection, kind):
            statement = "DROP VIEW" if kind == "view" else "DROP TABLE"
            connection.execute(f"{statement} IF EXISTS {_quote(name)}")
    for statement in _SHARED_TABLES:
        connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION:d}")


def _schema_objects(connection: sqlite3.Connection, kind: str) -> list[str]:
    if kind == "virtual":
        rows = connection.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'table' AND sql LIKE 'CREATE VIRTUAL TABLE%'"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ?", (kind,)
        ).fetchall()
    # SQLite owns the sqlite_ names (sqlite_sequence, for example).
    return [name for (name,) in rows if not name.startswith("sqlite_")]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
