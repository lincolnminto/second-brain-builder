"""The SQLite FTS5 index over the Canonical Documents and Wiki Pages of every Brain."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import (
    ASSETS_DIR_NAME,
    SOURCES_DIR,
    read_pages,
)
from second_brain_builder.okf import OKFError, Page, is_concept, read_page
from second_brain_builder.search.models import SearchHit
from second_brain_builder.search.query import QueryTerms, in_columns
from second_brain_builder.search.schema import (
    MAX_PASSAGES,
    PASSAGE_BITS,
    SCHEMA_VERSION,
    BrainTables,
    first_passage_id,
    last_passage_id,
    passage_ordinal,
    reset_schema,
    schema_version,
)
from second_brain_builder.search.snippets import (
    ELLIPSIS,
    MATCH_END,
    MATCH_START,
    SNIPPET_TOKENS,
    leading_snippet,
    render_snippet,
)
from second_brain_builder.search.text import clean_text, markdown_to_text, split_passages

_log = logging.getLogger(__name__)

_BUSY_TIMEOUT_SECONDS = 30.0

# BM25 weight per documents_fts column: title > description > tags > body.
_BM25_WEIGHTS = {"title_weight": 10.0, "description_weight": 5.0, "tags_weight": 3.0}

# SQL for the tables of one Brain. BrainTables.sql puts in the table names
# ({documents} and the others); every value is a parameter.

_SELECT_EVERY_DIGEST = "SELECT path, digest FROM {documents}"
_SELECT_DIGESTS = (
    "SELECT path, digest FROM {documents} WHERE path IN (SELECT value FROM json_each(?))"
)
_INSERT_DOCUMENT = """
    INSERT INTO {documents} (path, type, title, description, tags, passage_count, digest)
    VALUES (?, ?, ?, ?, ?, ?, ?)
"""
_INSERT_DOCUMENT_FTS = (
    "INSERT INTO {documents_fts} (rowid, title, description, tags, body) VALUES (?, ?, ?, ?, ?)"
)
_INSERT_FIELDS_FTS = (
    "INSERT INTO {fields_fts} (rowid, title, description, tags) VALUES (?, ?, ?, ?)"
)
_INSERT_PASSAGE = "INSERT INTO {passages} (id, text) VALUES (?, ?)"
_INSERT_PASSAGE_FTS = "INSERT INTO {passages_fts} (rowid, text) VALUES (?, ?)"

# FTS5 removes a row of a table without content (documents_fts), or with external
# content (fields_fts, passages_fts), only when it gets the indexed values again.
_SELECT_FIELDS = "SELECT id, title, description, tags FROM {documents} WHERE path = ?"
_SELECT_PASSAGE_TEXTS = "SELECT text FROM {passages} WHERE id BETWEEN ? AND ? ORDER BY id"
_DELETE_DOCUMENT_FTS = """
    INSERT INTO {documents_fts} ({documents_fts}, rowid, title, description, tags, body)
    VALUES ('delete', ?, ?, ?, ?, ?)
"""
_DELETE_FIELDS_FTS = """
    INSERT INTO {fields_fts} ({fields_fts}, rowid, title, description, tags)
    VALUES ('delete', ?, ?, ?, ?)
"""
_DELETE_PASSAGES_FTS = """
    INSERT INTO {passages_fts} ({passages_fts}, rowid, text)
    SELECT 'delete', id, text FROM {passages} WHERE id BETWEEN ? AND ?
"""
_DELETE_PASSAGES = "DELETE FROM {passages} WHERE id BETWEEN ? AND ?"
_DELETE_DOCUMENT = "DELETE FROM {documents} WHERE id = ?"

# The best pages, in order. Each field CTE runs once, on the small fields_fts
# table; a level bit is set when the whole query matches inside that field.
_RANK = """
    WITH
        in_title(id) AS (SELECT rowid FROM {fields_fts} WHERE {fields_fts} MATCH :title),
        in_description(id) AS (
            SELECT rowid FROM {fields_fts} WHERE {fields_fts} MATCH :description
        ),
        in_tags(id) AS (SELECT rowid FROM {fields_fts} WHERE {fields_fts} MATCH :tags)
    SELECT
        document.id,
        document.path,
        document.title,
        document.type,
        document.description,
        document.passage_count,
        (document.id IN in_title) * 4
            + (document.id IN in_description) * 2
            + (document.id IN in_tags) AS level,
        bm25({documents_fts}, :title_weight, :description_weight, :tags_weight, 1.0) AS rank
    FROM {documents_fts}
        CROSS JOIN {documents} AS document ON document.id = {documents_fts}.rowid
    WHERE {documents_fts} MATCH :expression
        AND (:types IS NULL OR document.type IN (SELECT value FROM json_each(:types)))
    ORDER BY level DESC, rank, document.path
    LIMIT :limit
"""
# For each document, its passage with the best matches.
_BEST_PASSAGES = """
    SELECT passage FROM (
        SELECT
            rowid AS passage,
            row_number() OVER (
                PARTITION BY rowid >> :bits ORDER BY bm25({passages_fts}), rowid
            ) AS position
        FROM {passages_fts}
        WHERE {passages_fts} MATCH :expression
            AND (rowid >> :bits) IN (SELECT value FROM json_each(:documents))
    )
    WHERE position = 1
"""
# The snippets of some rows. The rowid filter has a "+", so FTS5 reads the matches
# once and SQLite keeps the wanted rows. With a plain "rowid IN", FTS5 evaluates the
# expression again for each rowid, and a short prefix term is costly to evaluate.
_PASSAGE_SNIPPETS = """
    SELECT rowid, snippet({passages_fts}, 0, :start, :end, :ellipsis, :tokens)
    FROM {passages_fts}
    WHERE {passages_fts} MATCH :expression AND +rowid IN (SELECT value FROM json_each(:rowids))
"""
_DESCRIPTION_SNIPPETS = """
    SELECT rowid, snippet({fields_fts}, 1, :start, :end, :ellipsis, :tokens)
    FROM {fields_fts}
    WHERE {fields_fts} MATCH :expression AND +rowid IN (SELECT value FROM json_each(:rowids))
"""
_SELECT_PASSAGES = "SELECT id, text FROM {passages} WHERE id IN (SELECT value FROM json_each(?))"


@dataclass(frozen=True)
class _Document:
    """The indexed form of one page."""

    path: str
    type: str
    title: str
    description: str
    tags: str
    body: str
    digest: str


@dataclass(frozen=True)
class _RankedHit:
    """One hit of a query, before it has a snippet."""

    id: int
    path: str
    title: str
    type: str
    description: str
    passage_count: int
    score: float


class SearchIndex:
    """A full-text index over the pages of every Brain, in one SQLite file.

    The pages are the OKF pages that ``brains.read_pages`` reads: Canonical
    Documents (``type: source``) and Wiki Pages. The index stores the title,
    description, tags, and body of each page as plain text (see
    ``search.text.markdown_to_text``). Matches ignore case and diacritics, so
    ``acao`` finds "ação".

    Each Brain has its own tables (see ``search.schema``). A query reads only the
    pages of its Brain, and the pages of other Brains do not change its results or
    their order.

    Hits are sorted first by the fields that contain the whole query: a hit whose
    title matches comes before every hit whose title does not, then the
    description decides in the same way, then the tags. BM25 sorts the hits that
    match in the same fields, with the column weights title > description > tags >
    body. So a long Canonical Document whose title matches stays above short pages
    that only mention its title in their text.

    The index is a cache: ``rebuild`` makes it again from a Brain clone. Each method
    opens its own connection, so one instance can serve many threads, and the ``app``
    and ``worker`` processes can share the file. Writes run in one transaction each,
    so a query sees the rows from before or after a write, never a part of it. The
    file uses WAL mode, so queries do not wait for writes.
    """

    def __init__(self, db_path: Path) -> None:
        """Open the index at ``db_path``, and create the file and its tables if needed.

        If the file has tables from another ``SCHEMA_VERSION``, they are dropped and
        created empty. When the file already has the current schema, this only
        reads, so it does not wait for a write in another process.
        """
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            if schema_version(connection) == SCHEMA_VERSION:
                return
            connection.execute("PRAGMA journal_mode = WAL")
            with _write_transaction(connection):
                # Another process can have made the schema since the check above.
                if schema_version(connection) != SCHEMA_VERSION:
                    reset_schema(connection)

    def rebuild(self, brain_id: str, root: Path) -> None:
        """Replace the rows of one Brain with the pages of the clone at ``root``.

        Pages whose text did not change since the last write are not indexed again.
        A page that cannot be indexed is left out with a warning; the other pages
        are indexed.

        Raises:
            NotADirectoryError: ``root`` is not a directory. The index does not change.
        """
        _require_directory(root)
        pages: dict[str, Page | None] = {str(page.path): page for page in read_pages(root)}
        self._write(brain_id, pages, every_path=True)

    def update(self, brain_id: str, root: Path, paths: list[PurePosixPath]) -> None:
        """Index ``paths`` of one Brain again, from the clone at ``root``.

        Use it after a job writes, moves, or deletes files. The rules are the same as
        in ``rebuild``: a path whose file is missing, cannot be read, or is not a
        page (for example an Asset, a Source file, ``index.md``, or a file with
        invalid frontmatter) loses its row. Other rows do not change.

        Raises:
            ValueError: A path is absolute, empty, or has a ``..`` segment. Paths are
                relative to the Brain root, for example ``wiki/entities/grey-heron.md``.
            NotADirectoryError: ``root`` is not a directory. The index does not change.
        """
        _require_directory(root)
        unique = list(dict.fromkeys(_relative(path) for path in paths))
        # A path that is not UTF-8 is never a page (read_pages skips it), so it has
        # no row, and SQLite cannot store it.
        pages = {str(path): _read_indexable_page(root, path) for path in unique if _is_utf8(path)}
        if pages:
            self._write(brain_id, pages, every_path=False)

    def query(
        self,
        brain_id: str,
        text: str,
        limit: int = 20,
        types: list[str] | None = None,
    ) -> list[SearchHit]:
        """The pages of one Brain that contain every term of ``text``, best first.

        ``text`` is plain user text, never FTS5 syntax (see
        ``search.query.QueryTerms``). The last term also matches as a prefix.

        Args:
            brain_id: The Brain to search. Other Brains' pages never match.
            text: The user's words. Text without terms gives no hits.
            limit: The maximum number of hits.
            types: If given, only pages with one of these ``type`` values match,
                for example ``["source"]`` or ``["entity", "concept"]``. An empty
                list gives no hits.

        Raises:
            ValueError: ``limit`` is less than 1.
        """
        if limit < 1:
            raise ValueError(f"limit must be 1 or more, not {limit}")
        terms = QueryTerms.parse(text)
        if terms is None or (types is not None and not types):
            return []
        type_values = None if types is None else [clean_text(str(value)) for value in types]
        with self._connect() as connection, _read_transaction(connection):
            tables = _find_brain(connection, brain_id)
            if tables is None:
                return []
            ranked = _rank(connection, tables, terms, limit, type_values)
            snippets = _snippets(connection, tables, terms, ranked)
        return [
            SearchHit(
                path=PurePosixPath(hit.path),
                title=hit.title,
                type=hit.type,
                snippet=snippets[hit.id],
                score=hit.score,
            )
            for hit in ranked
        ]

    def remove_brain(self, brain_id: str) -> None:
        """Delete the tables and rows of one Brain, for example after it is disconnected."""
        with self._connect() as connection, _write_transaction(connection):
            tables = _find_brain(connection, brain_id)
            if tables is None:
                return
            for statement in tables.drop_statements():
                connection.execute(statement)
            connection.execute("DELETE FROM brains WHERE key = ?", (tables.key,))

    def _write(self, brain_id: str, pages: Mapping[str, Page | None], *, every_path: bool) -> None:
        """Make the rows of one Brain match ``pages``.

        ``pages`` maps paths to their page, or to None for a path that has no page.
        With ``every_path``, ``pages`` has every page of the Brain, and the rows of
        other paths are deleted.
        """
        digests = {path: _digest(page) for path, page in pages.items() if page is not None}
        selected = None if every_path else pages.keys()
        with self._connect() as connection:
            # Convert the changed pages before the write lock is taken, because a
            # large page takes time. The transaction checks the digests again.
            tables = _find_brain(connection, brain_id)
            known = {} if tables is None else _stored_digests(connection, tables, selected)
            prepared = {
                path: _prepare(page, digests[path])
                for path, page in pages.items()
                if page is not None and known.get(path) != digests[path]
            }
            with _write_transaction(connection):
                tables = _brain(connection, brain_id)
                stored = _stored_digests(connection, tables, selected)
                if every_path:
                    for path in sorted(stored.keys() - pages.keys()):
                        _delete(connection, tables, path)
                for path, page in pages.items():
                    digest = digests.get(path)
                    if digest is not None and stored.get(path) == digest:
                        continue
                    if path in stored:
                        _delete(connection, tables, path)
                    if page is None or digest is None:
                        continue
                    document = prepared[path] if path in prepared else _prepare(page, digest)
                    if document is not None:
                        _insert(connection, tables, document)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # isolation_level=None: the code starts each transaction itself.
        connection = sqlite3.connect(
            self._db_path, timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None
        )
        try:
            yield connection
        finally:
            connection.close()


# --- Transactions and Brains ----------------------------------------------------


@contextmanager
def _write_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """A transaction that takes the write lock at the start.

    With a plain BEGIN, a read that later turns into a write fails at once when
    another connection wrote in between. BEGIN IMMEDIATE waits for the busy timeout.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        # SQLite rolls back by itself after some errors (a full disk, for example).
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


@contextmanager
def _read_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """A transaction in which every read sees the same state of the file.

    In WAL mode it takes no lock that a writer waits for.
    """
    connection.execute("BEGIN")
    try:
        yield
    finally:
        if connection.in_transaction:
            connection.execute("ROLLBACK")


def _find_brain(connection: sqlite3.Connection, brain_id: str) -> BrainTables | None:
    row = connection.execute("SELECT key FROM brains WHERE brain_id = ?", (brain_id,)).fetchone()
    return None if row is None else BrainTables(int(row[0]))


def _brain(connection: sqlite3.Connection, brain_id: str) -> BrainTables:
    """The tables of one Brain, created if needed. Call it inside a write transaction."""
    tables = _find_brain(connection, brain_id)
    if tables is not None:
        return tables
    cursor = connection.execute("INSERT INTO brains (brain_id) VALUES (?)", (brain_id,))
    tables = BrainTables(_inserted_id(cursor))
    for statement in tables.create_statements():
        connection.execute(statement)
    return tables


def _inserted_id(cursor: sqlite3.Cursor) -> int:
    row_id = cursor.lastrowid
    if row_id is None:  # an INSERT of one row always sets it
        raise sqlite3.InterfaceError("the INSERT gave no row id")
    return row_id


# --- Writes ---------------------------------------------------------------------


def _stored_digests(
    connection: sqlite3.Connection, tables: BrainTables, paths: Collection[str] | None
) -> dict[str, str]:
    """The stored digest of each path in ``paths`` that has a row, or of every row."""
    if paths is None:
        rows = connection.execute(tables.sql(_SELECT_EVERY_DIGEST)).fetchall()
    else:
        rows = connection.execute(
            tables.sql(_SELECT_DIGESTS), (json.dumps(list(paths)),)
        ).fetchall()
    return dict(rows)


def _insert(connection: sqlite3.Connection, tables: BrainTables, document: _Document) -> None:
    passages = split_passages(document.body)
    if len(passages) > MAX_PASSAGES:  # about 2 GB of text: keep the rest in the last one
        passages[MAX_PASSAGES - 1 :] = ["".join(passages[MAX_PASSAGES - 1 :])]
    fields = (document.title, document.description, document.tags)
    cursor = connection.execute(
        tables.sql(_INSERT_DOCUMENT),
        (document.path, document.type, *fields, len(passages), document.digest),
    )
    document_id = _inserted_id(cursor)
    # _delete gives FTS5 the same body again, joined from the passages.
    connection.execute(tables.sql(_INSERT_DOCUMENT_FTS), (document_id, *fields, "".join(passages)))
    connection.execute(tables.sql(_INSERT_FIELDS_FTS), (document_id, *fields))
    rows = [(first_passage_id(document_id) + n, text) for n, text in enumerate(passages)]
    connection.executemany(tables.sql(_INSERT_PASSAGE), rows)
    connection.executemany(tables.sql(_INSERT_PASSAGE_FTS), rows)


def _delete(connection: sqlite3.Connection, tables: BrainTables, path: str) -> None:
    row = connection.execute(tables.sql(_SELECT_FIELDS), (path,)).fetchone()
    if row is None:
        return
    document_id, *fields = row
    passages = (first_passage_id(document_id), last_passage_id(document_id))
    texts = connection.execute(tables.sql(_SELECT_PASSAGE_TEXTS), passages).fetchall()
    body = "".join(text for (text,) in texts)
    connection.execute(tables.sql(_DELETE_DOCUMENT_FTS), (document_id, *fields, body))
    connection.execute(tables.sql(_DELETE_FIELDS_FTS), (document_id, *fields))
    connection.execute(tables.sql(_DELETE_PASSAGES_FTS), passages)
    connection.execute(tables.sql(_DELETE_PASSAGES), passages)
    connection.execute(tables.sql(_DELETE_DOCUMENT), (document_id,))


# --- Queries --------------------------------------------------------------------


def _rank(
    connection: sqlite3.Connection,
    tables: BrainTables,
    terms: QueryTerms,
    limit: int,
    types: list[str] | None,
) -> list[_RankedHit]:
    """The best ``limit`` pages that contain every term, in order."""
    every_term = terms.every_term()
    parameters = {
        "expression": every_term,
        "title": in_columns(("title",), every_term),
        "description": in_columns(("description",), every_term),
        "tags": in_columns(("tags",), every_term),
        "types": None if types is None else json.dumps(types),
        "limit": limit,
        **_BM25_WEIGHTS,
    }
    rows = connection.execute(tables.sql(_RANK), parameters).fetchall()
    return [
        _RankedHit(
            id=document_id,
            path=path,
            title=title,
            type=page_type,
            description=description,
            passage_count=passage_count,
            score=_score(level, rank),
        )
        for document_id, path, title, page_type, description, passage_count, level, rank in rows
    ]


def _score(level: int, rank: float) -> float:
    """A score that sorts like (level, -rank): the level, plus BM25 mapped into [0, 1)."""
    relevance = max(-rank, 0.0)  # FTS5 bm25() is negative: lower is better
    return level + relevance / (1.0 + relevance)


def _snippets(
    connection: sqlite3.Connection,
    tables: BrainTables,
    terms: QueryTerms,
    hits: Sequence[_RankedHit],
) -> dict[int, str]:
    """The snippet of each hit, by document id.

    From the body passage with the best matches, else from the description, else
    the start of the description or of the body.
    """
    any_term = terms.any_term()
    by_id = {hit.id: hit for hit in hits}
    snippets: dict[int, str] = {}

    best = _best_passages(connection, tables, any_term, list(by_id))
    passage_snippets = tables.sql(_PASSAGE_SNIPPETS)
    for passage_id, marked in _marked_text(connection, passage_snippets, any_term, best):
        hit = by_id[passage_id >> PASSAGE_BITS]
        ordinal = passage_ordinal(passage_id)
        snippets[hit.id] = render_snippet(
            marked, cut_before=ordinal > 0, cut_after=ordinal < hit.passage_count - 1
        )

    rest = [document_id for document_id in by_id if document_id not in snippets]
    in_description = in_columns(("description",), any_term)
    description_snippets = tables.sql(_DESCRIPTION_SNIPPETS)
    for document_id, marked in _marked_text(connection, description_snippets, in_description, rest):
        snippets[document_id] = render_snippet(marked)

    # Only the title or the tags match: show the start of the description or body.
    rest = [document_id for document_id in rest if document_id not in snippets]
    first_passages = _first_passages(
        connection,
        tables,
        [document_id for document_id in rest if not by_id[document_id].description.strip()],
    )
    for document_id in rest:
        hit = by_id[document_id]
        if hit.description.strip():
            snippets[document_id] = leading_snippet(hit.description)
        else:
            snippets[document_id] = leading_snippet(
                first_passages.get(document_id, ""), cut_after=hit.passage_count > 1
            )
    return snippets


def _best_passages(
    connection: sqlite3.Connection, tables: BrainTables, expression: str, documents: list[int]
) -> list[int]:
    """For each document that has a passage that matches ``expression``, its best one by BM25."""
    if not documents:
        return []
    rows = connection.execute(
        tables.sql(_BEST_PASSAGES),
        {"expression": expression, "documents": json.dumps(documents), "bits": PASSAGE_BITS},
    ).fetchall()
    return [passage for (passage,) in rows]


def _marked_text(
    connection: sqlite3.Connection, sql: str, expression: str, rowids: list[int]
) -> list[tuple[int, str]]:
    """The rowid and the FTS5 ``snippet()`` of each row in ``rowids`` that matches ``expression``.

    ``sql`` is ``_PASSAGE_SNIPPETS`` or ``_DESCRIPTION_SNIPPETS`` for one Brain.
    """
    if not rowids:
        return []
    rows = connection.execute(
        sql,
        {
            "expression": expression,
            "rowids": json.dumps(rowids),
            "start": MATCH_START,
            "end": MATCH_END,
            "ellipsis": ELLIPSIS,
            "tokens": SNIPPET_TOKENS,
        },
    ).fetchall()
    return [(rowid, marked) for rowid, marked in rows]


def _first_passages(
    connection: sqlite3.Connection, tables: BrainTables, documents: list[int]
) -> dict[int, str]:
    """The first passage of each document that has one, by document id."""
    if not documents:
        return {}
    rows = connection.execute(
        tables.sql(_SELECT_PASSAGES),
        (json.dumps([first_passage_id(document_id) for document_id in documents]),),
    ).fetchall()
    return {passage_id >> PASSAGE_BITS: text for passage_id, text in rows}


# --- Pages ----------------------------------------------------------------------


def _require_directory(root: Path) -> None:
    if not root.is_dir():
        raise NotADirectoryError(f"not a Brain clone directory: {root}")


def _relative(path: PurePosixPath) -> PurePosixPath:
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"not a path relative to the Brain root: {str(path)!r}")
    return path


def _is_utf8(path: PurePosixPath) -> bool:
    """False for a path with names that are not UTF-8 (``os.fsdecode`` escapes their bytes)."""
    try:
        str(path).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _read_indexable_page(root: Path, path: PurePosixPath) -> Page | None:
    """The page at ``path`` if ``brains.read_pages`` would read it, else None.

    The rules are the same as in ``read_pages``, for one path: a ``.md`` file that is
    not a dot-path, not under a symbolic link, and not in an ``assets/`` directory
    under ``sources/``;
    that can be read, with frontmatter that can be read and has a ``type``; and not
    ``index.md`` or ``log.md``. A file that cannot be read is skipped with a
    warning, as in ``read_pages``.
    """
    if path.suffix != ".md" or any(part.startswith(".") for part in path.parts):
        return None
    if path.parts[0] == SOURCES_DIR.name and ASSETS_DIR_NAME in path.parts[1:-1]:
        return None
    try:
        for depth in range(1, len(path.parts) + 1):
            if root.joinpath(*path.parts[:depth]).is_symlink():
                return None
        if not root.joinpath(*path.parts).is_file():
            return None
        page = read_page(root, path)
    except FileNotFoundError:  # deleted after the checks above
        return None
    except (OKFError, OSError, ValueError) as exc:
        # OSError: no permission, or a name that is too long. ValueError: the path
        # goes through a symbolic link that was made after the checks above.
        _log.warning("Skipped %s in %s: %s", path, root, exc)
        return None
    return page if is_concept(page) else None


def _digest(page: Page) -> str:
    """A hash of everything that the index stores for ``page``."""
    frontmatter = page.frontmatter
    fields = (
        [None]
        if frontmatter is None
        else [frontmatter.type, frontmatter.title, frontmatter.description, frontmatter.tags]
    )
    data = json.dumps([*fields, page.body], ensure_ascii=False)
    # surrogatepass: frontmatter can hold lone surrogates (clean_text replaces
    # them in the stored text), and the digest must not fail on them.
    return hashlib.sha256(data.encode("utf-8", "surrogatepass")).hexdigest()


def _prepare(page: Page, digest: str) -> _Document | None:
    """The indexed form of ``page``, or None (with a warning) when it cannot be made.

    One page that the index cannot convert must not stop the other pages of the
    Brain from being indexed.
    """
    try:
        return _document(page, digest)
    except (ValueError, RecursionError) as exc:
        _log.warning("Skipped %s: cannot index it: %s", page.path, exc)
        return None


def _document(page: Page, digest: str) -> _Document:
    frontmatter = page.frontmatter
    if frontmatter is None:  # every page from read_pages and is_concept has frontmatter
        raise ValueError(f"{page.path} has no frontmatter")
    title = " ".join(clean_text(frontmatter.title or "").split()) or page.path.stem
    return _Document(
        path=str(page.path),
        type=clean_text(frontmatter.type),
        title=title,
        description=clean_text(frontmatter.description or ""),
        tags="\n".join(clean_text(tag) for tag in frontmatter.tags),
        body=markdown_to_text(page.body),
        digest=digest,
    )
