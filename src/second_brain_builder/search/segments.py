"""Split a Markdown body into segments: the document viewer and the passage index share it.

``segment_markdown`` cuts a body at each heading, then groups the blocks of each
section (paragraphs, lists, fenced code, and so on) into segments of at most
``max_chars`` characters. The document viewer reads a Canonical Document in
segments of ``SEGMENT_CHARS``; the passage index (``SearchIndex.find_passages``)
stores segments of ``PASSAGE_CHARS``.

Offsets are indexes into the body string (Unicode code points, as Python counts
them; JavaScript counts UTF-16 code units, which differ for characters outside the
Basic Multilingual Plane). The segments of a body are contiguous and in order, so
``"".join(body[s.char_start:s.char_end] for s in segments) == body``.

The result depends only on the body and ``max_chars``. The passage index stores
segments, so a change to the rules here needs a new ``search.SCHEMA_VERSION``.

Bodies are untrusted (a Canonical Document is a converted Source), so the work is
linear in the length of the body.
"""

from __future__ import annotations

import bisect
import html
import re
from dataclasses import dataclass

SEGMENT_CHARS = 12_000
"""The usual maximum length of a document viewer segment."""
PASSAGE_CHARS = 1_500
"""The usual maximum length of a passage in the passage index."""

# Block syntax, matched on one line without its line ending (CommonMark §4).
_ATX_RE = re.compile(r" {0,3}(#{1,6})(?:[ \t](.*))?")
_FENCE_OPEN_RE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_FENCE_CLOSE_RE = re.compile(r" {0,3}(`{3,}|~{3,})[ \t]*")
_SETEXT_RE = re.compile(r" {0,3}(=+|-+)[ \t]*")
# Lines that start a block other than a paragraph: a list item, a block quote,
# indented code, an HTML block, or a thematic break. A setext underline under them
# is not a heading.
_NOT_PARAGRAPH_RE = re.compile(
    r"(?: {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]|$)| {0,3}>| {4}|\t| {0,3}<"
    r"| {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$)"
)
# Lines that end a paragraph without a blank line (a list item, a block quote, an
# HTML block), so a setext underline after them is not under a paragraph.
_INTERRUPTS_PARAGRAPH_RE = re.compile(r" {0,3}(?:[-+*][ \t]|\d{1,9}[.)][ \t]|>|<)")

# Inline markup that heading text loses (see ``_plain_heading``).
_IMAGE_OR_LINK_RE = re.compile(r"!?\[([^\[\]]*)\](?:\([^()]*\)|\[[^\[\]]*\])?")
_FOOTNOTE_REF_RE = re.compile(r"\[\^[^\[\]]*\]")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^<>]*>")
_EMPHASIS_RE = re.compile(r"(?<!\w)[*_~]+|[*_~]+(?!\w)|`+")
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")


@dataclass(frozen=True)
class Heading:
    """One heading of a body, in plain text."""

    level: int
    """1 to 6. A setext heading has level 1 (``===``) or 2 (``---``)."""
    text: str
    """The heading text without Markdown markup, on one line. Untrusted plain text."""
    char_start: int
    """The offset of the heading's first line in the body."""
    segment: int
    """The index of the segment that starts with this heading."""


@dataclass(frozen=True)
class Segment:
    """One segment of a body: ``body[char_start:char_end]``."""

    index: int
    char_start: int
    char_end: int
    heading_trail: tuple[str, ...]
    """The headings that the segment is under, outermost first.

    It ends with the heading that starts the segment's section. A segment before
    the first heading has an empty trail.
    """


@dataclass(frozen=True)
class Segmentation:
    """The segments and headings of one body, both in body order."""

    segments: tuple[Segment, ...]
    headings: tuple[Heading, ...]


def segment_markdown(body: str, max_chars: int = SEGMENT_CHARS) -> Segmentation:
    """Split ``body`` into segments of at most ``max_chars`` characters, where it can.

    The rules, in order:

    1. Each heading (ATX ``#`` or setext) starts a new segment, so a segment never
       spans two sections.
    2. Inside a section, whole blocks are grouped into segments of at most
       ``max_chars`` characters. A block starts at a non-blank line after a blank
       line, at a heading, at a fence, or at the line after a heading or a fence.
       The blank lines after a block belong to it.
    3. A block longer than ``max_chars`` is cut at line starts, and a line longer
       than ``max_chars`` after the last space or tab that fits (or at
       ``max_chars`` when it has none). A fenced code block is never cut: it can
       be longer than ``max_chars``.

    Fenced code blocks are found at the top level only (indented at most three
    spaces). A fence that never closes does not count as one: CommonMark runs it
    to the end of the document, and one segment of that size would defeat the
    document viewer.

    An empty body has no segments.

    Raises:
        ValueError: ``max_chars`` is less than 1.
    """
    if max_chars < 1:
        raise ValueError(f"max_chars must be 1 or more, not {max_chars}")
    if not body:
        return Segmentation(segments=(), headings=())
    lines = _lines(body)
    blocks = _blocks(lines)
    return _segmentation(body, lines, blocks, max_chars)


# --- Lines and blocks -----------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    """The offset after the line ending."""
    text: str
    """The line without its line ending."""


@dataclass
class _Block:
    first_line: int
    kind: str
    """``text``, ``heading``, or ``fence`` (a fence that closes)."""
    paragraph: bool = False
    """For ``text``: every line so far is paragraph text, so a setext underline can follow."""
    level: int = 0
    heading: str = ""
    line_count: int = 1


def _lines(body: str) -> list[_Line]:
    lines: list[_Line] = []
    start = 0
    length = len(body)
    while start < length:
        newline = body.find("\n", start)
        end = length if newline < 0 else newline + 1
        text = body[start : newline if newline >= 0 else length]
        lines.append(_Line(start=start, end=end, text=text.removesuffix("\r")))
        start = end
    return lines


class _FenceClosers:
    """The lines that can close a fence, to find each fence's closing line in linear time."""

    def __init__(self, lines: list[_Line]) -> None:
        self._indexes: dict[str, list[int]] = {"`": [], "~": []}
        self._lengths: dict[str, list[int]] = {"`": [], "~": []}
        for index, line in enumerate(lines):
            match = _FENCE_CLOSE_RE.fullmatch(line.text)
            if match is not None:
                run = match.group(1)
                self._indexes[run[0]].append(index)
                self._lengths[run[0]].append(len(run))
        # The longest closing run at or after each position, to see at once
        # whether a fence closes at all.
        self._longest_after: dict[str, list[int]] = {}
        for char, lengths in self._lengths.items():
            longest = [0] * (len(lengths) + 1)
            for position in range(len(lengths) - 1, -1, -1):
                longest[position] = max(lengths[position], longest[position + 1])
            self._longest_after[char] = longest

    def closing_line(self, opening_line: int, char: str, length: int) -> int | None:
        """The index of the line that closes a fence that opens at ``opening_line``.

        A fence that never closes costs one binary search. For a fence that closes,
        the search passes over the lines inside the fence only, which the caller
        then skips, so a whole scan stays linear.
        """
        indexes = self._indexes[char]
        position = bisect.bisect_right(indexes, opening_line)
        if self._longest_after[char][position] < length:
            return None
        lengths = self._lengths[char]
        while lengths[position] < length:
            position += 1
        return indexes[position]


def _blocks(lines: list[_Line]) -> list[_Block]:
    blocks: list[_Block] = []
    closers = _FenceClosers(lines)
    after_blank = True  # the start of the body acts like a blank line
    block_ended = False  # the last line was a heading or a fence's closing line
    index = 0
    while index < len(lines):
        text = lines[index].text
        if not text.strip(" \t"):
            after_blank = True
            index += 1
            continue
        heading = _ATX_RE.fullmatch(text)
        if heading is not None:
            content = _without_closing_sequence((heading.group(2) or "").strip(" \t"))
            blocks.append(
                _Block(
                    first_line=index,
                    kind="heading",
                    level=len(heading.group(1)),
                    heading=_plain_heading(content),
                )
            )
            after_blank, block_ended = False, True
            index += 1
            continue
        fence = _FENCE_OPEN_RE.fullmatch(text)
        opens_fence = fence is not None and not (fence.group(1)[0] == "`" and "`" in fence.group(2))
        if fence is not None and opens_fence:
            run = fence.group(1)
            closing = closers.closing_line(index, run[0], len(run))
            if closing is not None:
                blocks.append(
                    _Block(first_line=index, kind="fence", line_count=closing - index + 1)
                )
                after_blank, block_ended = False, True
                index = closing + 1
                continue
        current = blocks[-1] if blocks else None
        if current is not None and not after_blank and not block_ended:
            underline = _SETEXT_RE.fullmatch(text)
            if underline is not None and current.kind == "text" and current.paragraph:
                current.kind = "heading"
                current.level = 1 if underline.group(1)[0] == "=" else 2
                current.heading = _plain_heading(
                    " ".join(line.text for line in lines[current.first_line : index])
                )
                current.line_count = index - current.first_line + 1
                block_ended = True
                index += 1
                continue
        if current is None or after_blank or block_ended or opens_fence:
            paragraph = not opens_fence and _NOT_PARAGRAPH_RE.match(text) is None
            blocks.append(_Block(first_line=index, kind="text", paragraph=paragraph))
        else:
            current.line_count = index - current.first_line + 1
            if current.paragraph and _INTERRUPTS_PARAGRAPH_RE.match(text) is not None:
                current.paragraph = False
        after_blank, block_ended = False, False
        index += 1
    return blocks


def _without_closing_sequence(content: str) -> str:
    """ATX heading content without its closing ``#`` sequence (CommonMark §4.2).

    The sequence counts only when the content is all ``#`` or a space or tab comes
    before it: ``Title ##`` gives ``Title``, ``C#`` stays. String methods, not a
    regular expression: a pattern that tries each position of a long run of spaces
    takes time that grows with the square of the run length.
    """
    stripped = content.rstrip("#")
    if stripped == content:
        return content
    if not stripped or stripped[-1] in " \t":
        return stripped.rstrip(" \t")
    return content


def _plain_heading(markdown: str) -> str:
    """Heading text without the usual inline markup, on one line.

    A light pass, not a full inline parser: links and images keep their text,
    footnote references, HTML tags, and emphasis and code markers go, and
    character references are decoded. An escaped character (``\\*``) stays as
    text.
    """
    # With its group, split gives [text, escaped character, text, ...].
    pieces = _ESCAPE_RE.split(markdown)
    plain = [
        piece if position % 2 else html.unescape(_strip_markup(piece))
        for position, piece in enumerate(pieces)
    ]
    return " ".join("".join(plain).split())


def _strip_markup(text: str) -> str:
    text = _FOOTNOTE_REF_RE.sub("", text)
    text = _IMAGE_OR_LINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    return _EMPHASIS_RE.sub("", text)


# --- Segments -------------------------------------------------------------------


def _segmentation(
    body: str, lines: list[_Line], blocks: list[_Block], max_chars: int
) -> Segmentation:
    starts = [lines[block.first_line].start for block in blocks]
    # The first block takes the blank lines before it, so the segments cover the body.
    if starts:
        starts[0] = 0
    sections: list[list[int]] = []
    for position, block in enumerate(blocks):
        if not sections or block.kind == "heading":
            sections.append([])
        sections[-1].append(position)

    segments: list[Segment] = []
    headings: list[Heading] = []
    trail: list[tuple[int, str]] = []
    if not blocks:  # only blank lines
        segments.append(Segment(index=0, char_start=0, char_end=len(body), heading_trail=()))
    for section_number, section in enumerate(sections):
        first = blocks[section[0]]
        if first.kind == "heading":
            while trail and trail[-1][0] >= first.level:
                trail.pop()
            trail.append((first.level, first.heading))
            if first.heading:
                headings.append(
                    Heading(
                        level=first.level,
                        text=first.heading,
                        char_start=lines[first.first_line].start,
                        segment=len(segments),
                    )
                )
        heading_trail = tuple(text for _, text in trail if text)
        end = (
            starts[sections[section_number + 1][0]]
            if section_number + 1 < len(sections)
            else len(body)
        )
        for start, stop in _section_cuts(body, lines, blocks, starts, section, end, max_chars):
            segments.append(
                Segment(
                    index=len(segments),
                    char_start=start,
                    char_end=stop,
                    heading_trail=heading_trail,
                )
            )
    return Segmentation(segments=tuple(segments), headings=tuple(headings))


def _section_cuts(
    body: str,
    lines: list[_Line],
    blocks: list[_Block],
    starts: list[int],
    section: list[int],
    section_end: int,
    max_chars: int,
) -> list[tuple[int, int]]:
    """The ``(start, end)`` of each segment of one section, grouped greedily."""
    cuts: list[tuple[int, int]] = []
    current = starts[section[0]]
    for offset, position in enumerate(section):
        block = blocks[position]
        start = starts[position]
        end = starts[section[offset + 1]] if offset + 1 < len(section) else section_end
        if end - current <= max_chars:
            continue
        # Blank lines alone never make a segment: they go with the next block.
        if start > current and body[current:start].strip():
            cuts.append((current, start))
            current = start
        if end - current <= max_chars or block.kind == "fence":
            continue
        for cut in _block_cuts(body, lines, block, current, end, max_chars):
            cuts.append((current, cut))
            current = cut
    cuts.append((current, section_end))
    return cuts


def _block_cuts(
    body: str, lines: list[_Line], block: _Block, start: int, end: int, max_chars: int
) -> list[int]:
    """Where to cut a block that is longer than ``max_chars``: at line starts, else in a line.

    The text after the last cut is at most ``max_chars`` long, or ends at a line end
    that a later block can follow.
    """
    cuts: list[int] = []
    current = start
    first = block.first_line
    last = first + block.line_count  # the blank lines after the block need no cuts
    for number, line in enumerate(lines[first:last]):
        if (
            number > 0
            and line.start > current
            and line.end - current > max_chars
            and body[current : line.start].strip()
        ):
            cuts.append(line.start)
            current = line.start
        while min(line.end, end) - current > max_chars:
            cut = _cut_in_line(body, current, max_chars)
            cuts.append(cut)
            current = cut
    return cuts


def _cut_in_line(body: str, start: int, max_chars: int) -> int:
    """A cut after the last space or tab in ``body[start:start + max_chars]``, else at its end."""
    limit = start + max_chars
    space = max(body.rfind(" ", start, limit), body.rfind("\t", start, limit))
    return space + 1 if space > start else limit
