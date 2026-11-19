"""Split a Canonical Document body into Parts of about 16k tokens, on headings."""

from __future__ import annotations

import re
from dataclasses import dataclass

from second_brain_builder.ingest._fences import FENCE_RE, closes_fence

CHARS_PER_TOKEN = 4
"""A rough estimate of the characters in one token, for text in most languages."""

CHUNK_TOKENS = 16_000
"""The target size of one chunk, in tokens."""

CHUNK_CHARS = CHUNK_TOKENS * CHARS_PER_TOKEN
"""The largest chunk, in characters."""

_HEADING_RE = re.compile(r" {0,3}#{1,6}(?:[ \t]|$)")
_SHORT_SHARE = 8
"""A text of at most 1/8 of a chunk is short: it does not end a chunk before more text."""
_SECTION_SEPARATOR = "\n\n"
"""What joins two sections, or two pieces of text, in one chunk."""
_SEPARATORS = ("\n\n", "\n")
"""Where a section that is too long is split: between paragraphs, then between lines."""


@dataclass(frozen=True)
class Chunk:
    """One part of a Canonical Document body, in document order.

    ``headings`` is the trail of ATX heading lines at the start of the chunk,
    outermost first, for example ``("# Dune", "## Book One")``. It tells the model
    where a chunk that starts inside a long section belongs.
    """

    text: str
    headings: tuple[str, ...]


@dataclass
class _Section:
    headings: tuple[str, ...]
    lines: list[str]


def split_into_chunks(body: str, max_chars: int = CHUNK_CHARS) -> list[Chunk]:
    """Split ``body`` into chunks of at most ``max_chars`` characters.

    A section starts at each ATX heading (``#`` to ``######``) outside fenced code.
    Whole sections are packed into a chunk while they fit. A section that is longer
    than ``max_chars`` is split between paragraphs, then between lines, and as a
    last resort inside a line, and its pieces are packed like sections. A short
    text (at most 1/8 of ``max_chars``, such as a heading or a short introduction)
    never becomes a chunk of its own when more text follows: when the next section
    or paragraph does not fit after it, that text is split, and its first piece
    fills the chunk. Blank lines between sections become one blank line, and
    chunks without text are left out, so an empty body gives no chunks.

    Raises:
        ValueError: ``max_chars`` is less than 1.
    """
    if max_chars < 1:
        raise ValueError(f"max_chars must be 1 or more, not {max_chars}")
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[Chunk] = []
    pending: list[str] = []
    pending_size = 0
    pending_headings: tuple[str, ...] = ()

    def flush() -> None:
        nonlocal pending, pending_size
        joined = "\n\n".join(pending)
        if joined.strip():
            chunks.append(Chunk(text=joined, headings=pending_headings))
        pending = []
        pending_size = 0

    def add(piece: str, headings: tuple[str, ...]) -> None:
        nonlocal pending_size, pending_headings
        if not pending:
            pending_headings = headings
        pending_size += len(piece) + (len(_SECTION_SEPARATOR) if pending else 0)
        pending.append(piece)

    for section in _sections(text):
        section_text = "\n".join(section.lines).strip("\n")
        if not section_text.strip():
            continue
        gap = len(_SECTION_SEPARATOR) if pending else 0
        if pending_size + gap + len(section_text) <= max_chars:
            add(section_text, section.headings)
            continue
        if pending and pending_size + gap > max_chars // _SHORT_SHARE:
            flush()
            gap = 0
        if not pending and len(section_text) <= max_chars:
            add(section_text, section.headings)
            continue
        first, *rest = _split_text(
            section_text, max_chars, _SEPARATORS, max_chars - pending_size - gap
        )
        add(first, section.headings)
        for piece in rest:  # the last piece stays pending, so later sections can follow it
            flush()
            add(piece, section.headings)
    flush()
    return chunks


def _sections(text: str) -> list[_Section]:
    """The sections of ``text``: the lines before the first heading, then one per heading."""
    sections: list[_Section] = []
    trail: list[tuple[int, str]] = []
    current = _Section(headings=(), lines=[])
    fence: str | None = None
    for line in text.split("\n"):
        if fence is not None:
            current.lines.append(line)
            if closes_fence(line, fence):
                fence = None
            continue
        fence_match = FENCE_RE.match(line)
        if fence_match is not None:
            fence = fence_match.group(1)
        elif _HEADING_RE.match(line):
            heading = line.strip()
            level = len(heading) - len(heading.lstrip("#"))
            trail = [(depth, title) for depth, title in trail if depth < level]
            trail.append((level, heading))
            if current.lines:
                sections.append(current)
            current = _Section(headings=tuple(title for _, title in trail), lines=[])
        current.lines.append(line)
    if current.lines:
        sections.append(current)
    return sections


def _split_text(
    text: str, max_chars: int, separators: tuple[str, ...], first_max: int
) -> list[str]:
    """Pieces of ``text``, cut at the first separator that works.

    The first piece has at most ``first_max`` characters (1 to ``max_chars``) and
    the others at most ``max_chars``. A part between two separators that is too long
    for a piece is cut at the next separator. A short text before a part that does
    not fit after it stays in the piece of the part's first cut, as in
    ``split_into_chunks``.
    """
    if len(text) <= first_max:
        return [text]
    if not separators:
        starts = [0, *range(first_max, len(text), max_chars)]
        ends = [*starts[1:], len(text)]
        return [text[start:end] for start, end in zip(starts, ends, strict=True)]
    separator, finer = separators[0], separators[1:]
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for part in text.split(separator):
        limit = max_chars if pieces else first_max
        gap = len(separator) if current else 0
        if size + gap + len(part) <= limit:
            current.append(part)
            size += gap + len(part)
            continue
        if current and size + gap > limit // _SHORT_SHARE:
            pieces.append(separator.join(current))
            current, size, gap, limit = [], 0, 0, max_chars
        if not current and len(part) <= limit:
            current, size = [part], len(part)
            continue
        first, *rest = _split_text(part, max_chars, finer, limit - size - gap)
        current.append(first)
        size += gap + len(first)
        if rest:
            pieces.append(separator.join(current))
            pieces.extend(rest[:-1])
            current, size = [rest[-1]], len(rest[-1])
    if current:
        pieces.append(separator.join(current))
    return pieces
