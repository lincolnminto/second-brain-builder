"""Citations that point to the passage: resolve a footnote reference to its passage.

A Wiki Page cites a Source with footnote references (``[^dune]``). The resolver
finds the passage of the Source's Canonical Document that the cited sentence most
likely restates:

1. Take the sentence that holds the reference, as plain text
   (``search.cited_sentence``).
2. Read a locator in parentheses, such as ``(Gênesis 11:1-9)`` or ``(chapter 3)``
   (``search.parse_locator``).
3. Query the passage index of the cited Canonical Document with the key terms of
   the sentence (``search.key_terms``, BM25). With a locator, passages under a
   heading trail that matches it come first.

Results are cached in memory per (Brain, page, HEAD commit, reference), and an
entry counts only while the page file and the index rows of the cited Canonical
Document are the same as when it was made: a running job changes the working tree
before it commits, and the worker commits before it updates the index. Only found
passages are cached.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict

from second_brain_builder.brains import BrainPathError, GitError, GitRepo, resolve_in_brain
from second_brain_builder.okf import resolve_link
from second_brain_builder.search import (
    CitedSentence,
    PassageHit,
    cited_sentence,
    key_terms,
    parse_locator,
)
from second_brain_builder.services.brains import READ_ONLY_IDENTITY, clone_root
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import InvalidInputError, NotFoundError
from second_brain_builder.services.models import PageView
from second_brain_builder.services.pages import read_page

CitationMethod = Literal["locator_terms", "terms", "locator"]
"""How the passage was found.

``locator_terms``: the best passage, by the key terms of the sentence, under a
heading trail that matches the locator. ``terms``: the best passage by the key
terms (the sentence has no locator, or no matching passage is under it).
``locator``: the first passage under a heading trail that matches the locator,
when no passage has a key term.
"""

_CACHE_SIZE = 4096


class CitationPassage(BaseModel):
    """The passage of a Canonical Document that one footnote reference cites.

    ``char_start`` and ``char_end`` are offsets in the body of the Canonical
    Document (after the frontmatter, in Unicode code points), as in the document
    segments. ``text`` is untrusted plain text: show it as text, never as Markdown
    or HTML.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str
    source_path: str
    """The Canonical Document, from the Brain root."""
    source_title: str
    char_start: int
    char_end: int
    heading_trail: list[str]
    text: str
    score: float
    """1 plus the BM25 relevance (in [0, 1)) for ``locator_terms``, the relevance for
    ``terms``, and 1 for ``locator``. Compare scores only for one reference."""
    method: CitationMethod


_FileIdentity = tuple[int, int, int, int]
"""A file's device, inode, size, and modification time."""
_CacheKey = tuple[str, str, str, int, _FileIdentity]
"""The Brain id, the page path as requested, the HEAD commit, the reference number,
and the identity of the page file."""


@dataclass(frozen=True)
class _CacheEntry:
    passage: CitationPassage
    digest: str
    """``SearchIndex.document_digest`` of the Canonical Document, when the passage was read."""


_cache: OrderedDict[_CacheKey, _CacheEntry] = OrderedDict()
_cache_lock = Lock()


def resolve_citation(ctx: AppContext, brain_id: str, page: str, ref: int) -> CitationPassage:
    """The passage that footnote reference number ``ref`` (from 0) of a page cites.

    References count in the order of the rendered page: the references of the body
    first, then those in footnote definitions.

    Raises:
        NotFoundError: No such Brain or page, the page has no reference number
            ``ref``, the cited Source has no Canonical Document in the index, or no
            passage matches.
        InvalidInputError: ``ref`` is negative, or the page path is not valid (it
            leaves the Brain, has a dot-path or ``..``, or is not a ``.md`` file).
    """
    if ref < 0:
        raise InvalidInputError("The footnote reference number must be 0 or more.")
    root = clone_root(ctx, brain_id).resolve()
    target = _check_path(root, page)
    head = _head(root)
    file_identity = _file_identity(target)
    # The path as requested, so each path passes the checks of read_page before it
    # can read a cache entry.
    key = (
        None
        if head is None or file_identity is None
        else (brain_id, page, head, ref, file_identity)
    )
    if key is not None:
        cached = _cached(ctx, brain_id, key)
        if cached is not None:
            return cached
    view = read_page(ctx, brain_id, page)
    cited = cited_sentence(view.body, ref)
    if cited is None:
        raise NotFoundError(f"The page {view.path} has no footnote reference {ref}.")
    cited_document = _cited_document(root, view, cited)
    if cited_document is None:
        raise NotFoundError(f"The footnote [^{cited.label}] does not cite a Canonical Document.")
    document, source_id = cited_document
    digest = ctx.search.document_digest(brain_id, document)
    found = _find_passage(ctx, brain_id, document, cited)
    if found is None:
        raise NotFoundError(f"No passage of {document} matches this citation.")
    hit, score, method = found
    passage = CitationPassage(
        source_id=source_id,
        source_path=hit.path.as_posix(),
        source_title=hit.title,
        char_start=hit.char_start,
        char_end=hit.char_end,
        heading_trail=list(hit.heading_trail),
        text=hit.text,
        score=score,
        method=method,
    )
    # A worker can change HEAD, the page, or the index while they are read. Keep
    # the result only when all three are the same after the reads as before them.
    if (
        key is not None
        and digest is not None
        and _head(root) == head
        and _file_identity(target) == file_identity
        and ctx.search.document_digest(brain_id, document) == digest
    ):
        with _cache_lock:
            _cache[key] = _CacheEntry(passage=passage, digest=digest)
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
    return passage


def _cached(ctx: AppContext, brain_id: str, key: _CacheKey) -> CitationPassage | None:
    """The cached passage for ``key``, if the index still has the rows it came from."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None:
            _cache.move_to_end(key)
    if entry is None:
        return None
    document = PurePosixPath(entry.passage.source_path)
    if ctx.search.document_digest(brain_id, document) != entry.digest:
        return None
    return entry.passage


def _find_passage(
    ctx: AppContext, brain_id: str, document: PurePosixPath, cited: CitedSentence
) -> tuple[PassageHit, float, CitationMethod] | None:
    """The best passage, its score, and the method that found it."""
    terms = key_terms(cited.claim)
    locator = None if cited.locator is None else parse_locator(cited.locator)
    heading_sets = [] if locator is None else locator.heading_word_sets()
    if terms:
        for heading_words in heading_sets:
            hits = ctx.search.find_passages(
                brain_id, document, terms, heading_words=heading_words, limit=1
            )
            if hits:
                return hits[0], 1.0 + hits[0].score, "locator_terms"
        hits = ctx.search.find_passages(brain_id, document, terms, limit=1)
        if hits:
            return hits[0], hits[0].score, "terms"
    for heading_words in heading_sets:
        hits = ctx.search.find_passages(
            brain_id, document, [], heading_words=heading_words, limit=1
        )
        if hits:
            return hits[0], 1.0, "locator"
    return None


def _cited_document(
    root: Path, view: PageView, cited: CitedSentence
) -> tuple[PurePosixPath, str] | None:
    """The Canonical Document that a footnote cites, from the Brain root, and the Source id.

    The page's ``sources`` entry whose id is the footnote label gives both. The id
    matches without case, as the web matches it (GFM footnote labels ignore case).
    Else the first link of the footnote definition gives the document, and the
    label is the id. A path that leaves the Brain or is not a Markdown file does
    not count.
    """
    page_path = PurePosixPath(view.path)
    label = cited.label.lower()
    candidates = [
        (source.resource, source.id) for source in view.sources if source.id.lower() == label
    ]
    if cited.definition_href is not None:
        candidates.append((cited.definition_href, cited.label))
    for href, source_id in candidates:
        target = resolve_link(page_path, href)
        if target is None or target.suffix != ".md" or not target.parts:
            continue
        try:
            resolve_in_brain(root, target.as_posix())
        except BrainPathError:
            continue
        return target, source_id
    return None


def _check_path(root: Path, page: str) -> Path:
    """The page file, or InvalidInputError for a path that ``resolve_in_brain`` rejects.

    It runs before the cache is read.
    """
    try:
        return resolve_in_brain(root, page)
    except BrainPathError as exc:
        raise InvalidInputError(str(exc)) from None


def _file_identity(path: Path) -> _FileIdentity | None:
    """The device, inode, size, and modification time of a file, or None if it has none."""
    try:
        status = path.stat()
    except OSError:
        return None
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def _head(root: Path) -> str | None:
    """The HEAD commit of the clone, or None (then nothing is cached)."""
    if not root.is_dir():
        return None
    try:
        return GitRepo(root, READ_ONLY_IDENTITY, None).head_commit()
    except GitError:
        return None
