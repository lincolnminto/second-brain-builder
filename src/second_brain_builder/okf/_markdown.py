"""Find links, footnotes, and wikilinks in a Markdown body, with their positions.

markdown-it-py (CommonMark, GFM tables, footnotes) gives the block structure: code
blocks and HTML blocks to skip, the line ranges of inline content, footnote
definitions, and link reference definitions. A small scanner then finds the inline
constructs in those line ranges, after it blanks out code spans, so the positions
are offsets in the body text and edits can replace exactly the bytes they change.

Bodies can be untrusted (a Canonical Document is a converted Source), so every
step takes time linear in the length of the body.
"""

from __future__ import annotations

import bisect
import html
import re
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock, reference
from mdit_py_plugins.footnote import footnote_plugin

LinkKind = Literal["inline", "image", "definition", "autolink"]

_REFERENCE_RANGES = "sbb_reference_ranges"
_MASKED_BLOCKS = frozenset({"fence", "code_block", "html_block"})
_ASCII_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_MAX_PAREN_DEPTH = 32
"""Nested parentheses in a link destination, at most. markdown-it has the same limit."""

_AUTOLINK_RE = re.compile(r"<([A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\x00-\x20<>]*)>")
_FOOTNOTE_TOKEN_RE = re.compile(r"\[\^|[\]\s]")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
_DEFINITION_RE = re.compile(
    r"^[ \t>]*(?:(?:[-+*]|\d{1,9}[.)])[ \t]+)?[ \t>]*"
    r"\[(?P<label>(?:[^\[\]\\\n]|\\.)+)\]:[ \t]*\n?[ \t>]*"
    r"(?:<(?P<angle>[^<>\n]*)>|(?P<plain>[^\s<]\S*))",
    re.MULTILINE,
)
_UNESCAPE_RE = re.compile(
    r"\\([!-/:-@\[-`{-~])|&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)


@dataclass(frozen=True)
class Link:
    """A link, image, link reference definition, or autolink.

    ``start``/``end`` cover the whole construct. ``text_start``/``text_end`` cover the
    link text or image description (or the label of a definition).
    ``dest_start``/``dest_end`` cover the destination as written, without the angle
    brackets when ``angle`` is true. ``href`` is the destination with backslash
    escapes and character references resolved.
    """

    kind: LinkKind
    start: int
    end: int
    text_start: int
    text_end: int
    dest_start: int
    dest_end: int
    angle: bool
    href: str


@dataclass(frozen=True)
class FootnoteRef:
    """A footnote reference ``[^label]`` in inline content."""

    label: str
    start: int
    end: int


@dataclass(frozen=True)
class FootnoteDef:
    """A footnote definition block. ``start``/``end`` cover whole lines."""

    label: str
    start: int
    end: int


@dataclass(frozen=True)
class Wikilink:
    """A ``[[target]]`` construct. OKF does not treat it as a link."""

    target: str
    start: int
    end: int


@dataclass(frozen=True)
class Scan:
    """Everything found in one body. ``text`` is the body with ``\\n`` line endings."""

    text: str
    links: tuple[Link, ...]
    footnote_refs: tuple[FootnoteRef, ...]
    footnote_defs: tuple[FootnoteDef, ...]
    wikilinks: tuple[Wikilink, ...]


def _recording_reference(state: StateBlock, start_line: int, end_line: int, silent: bool) -> bool:
    """The link reference definition rule, which also records the lines that it used."""
    matched = reference(state, start_line, end_line, silent)
    if matched and not silent:
        env: MutableMapping[str, Any] = state.env
        env.setdefault(_REFERENCE_RANGES, []).append((start_line, state.line))
    return matched


@lru_cache(maxsize=1)
def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark").enable("table")
    # Keep footnote definitions in place: their tokens then carry line maps.
    footnote_plugin(md, inline=False, move_to_end=False)
    md.block.ruler.at("reference", _recording_reference)
    # Only the block structure is used; the scanner below finds the inline
    # constructs. The inline rules are off: they take time, and the footnote
    # reference rule takes quadratic time on text such as "[^[^[^[^...".
    md.disable("inline")
    return md


def normalize_newlines(text: str) -> str:
    """Use ``\\n`` line endings only."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _unescape_match(match: re.Match[str]) -> str:
    escaped = match.group(1)
    return escaped if escaped is not None else html.unescape(match.group(0))


def unescape(raw: str) -> str:
    """Resolve backslash escapes and character references, as CommonMark does in destinations."""
    return _UNESCAPE_RE.sub(_unescape_match, raw)


def raw_offset(raw: str, index: int) -> int | None:
    """The offset in ``raw`` where the text of ``unescape(raw)[index:]`` starts.

    ``unescape(raw[raw_offset(raw, index):]) == unescape(raw)[index:]``. Returns
    ``None`` when ``index`` falls inside the text of one escape or reference.
    """
    decoded = 0
    position = 0
    for match in _UNESCAPE_RE.finditer(raw):
        if index - decoded <= match.start() - position:
            return position + index - decoded
        decoded += match.start() - position
        piece = len(_unescape_match(match))
        if decoded < index < decoded + piece:
            return None
        decoded += piece
        position = match.end()
    if index - decoded <= len(raw) - position:
        return position + index - decoded
    return None


def escape_destination(text: str) -> str:
    """Destination text that ``unescape`` reads as ``text``, in angle brackets or not.

    ASCII punctuation gets a backslash, and whitespace and control characters
    become numeric character references.
    """
    return "".join(
        f"\\{char}"
        if char in _ASCII_PUNCTUATION
        else f"&#{ord(char)};"
        if char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F
        else char
        for char in text
    )


def scan(body: str) -> Scan:
    """Find the links, footnotes, and wikilinks of a body, outside code."""
    text = normalize_newlines(body)
    env: dict[str, Any] = {}
    tokens = _parser().parse(text, env)
    line_starts = [0, *(index + 1 for index, char in enumerate(text) if char == "\n")]

    def offset(line: int) -> int:
        return line_starts[line] if line < len(line_starts) else len(text)

    chars = list(text)
    segments: set[tuple[int, int]] = set()
    def_lines: list[tuple[str, int, int]] = []
    for token in tokens:
        if token.map is None:
            continue
        first, last = token.map
        if token.type in _MASKED_BLOCKS:
            _blank(chars, offset(first), offset(last))
        elif token.type == "inline":
            segments.add((offset(first), offset(last)))
        elif token.type == "footnote_reference_open":
            def_lines.append((str(token.meta["label"]), first, last))
    ordered_segments = sorted(segments)
    for start, end in ordered_segments:
        _blank_code_spans(chars, start, end)
    masked = "".join(chars)

    links: list[Link] = []
    refs: list[FootnoteRef] = []
    wikilinks: list[Wikilink] = []
    for start, end in ordered_segments:
        links.extend(_inline_links(text, masked, start, end))
    # Destinations and titles are metadata, not rendered inline content. Keep
    # the labels available for citations and images nested in link text.
    for link in links:
        _blank(chars, link.text_end + 1, link.end)
    inline_text = "".join(chars)
    for start, end in ordered_segments:
        refs.extend(_footnote_refs(inline_text, start, end))
        wikilinks.extend(_wikilinks(inline_text, start, end))
    for first, last in env.get(_REFERENCE_RANGES, []):
        definition = _definition(text, masked, offset(first), offset(last))
        if definition is not None:
            links.append(definition)

    defs: list[FootnoteDef] = []
    markers: set[int] = set()
    for label, first, last in def_lines:
        defs.append(FootnoteDef(label=label, start=offset(first), end=offset(last)))
        marker = masked.find(f"[^{label}]:", offset(first), offset(first + 1))
        if marker >= 0:
            markers.add(marker)
    link_starts = {link.text_start - 1 for link in links if link.kind == "inline"}
    refs = [ref for ref in refs if ref.start not in markers and ref.start not in link_starts]

    return Scan(
        text=text,
        links=tuple(sorted(links, key=lambda link: link.start)),
        footnote_refs=tuple(sorted(refs, key=lambda ref: ref.start)),
        footnote_defs=tuple(sorted(defs, key=lambda definition: definition.start)),
        wikilinks=tuple(sorted(wikilinks, key=lambda wikilink: wikilink.start)),
    )


def _blank(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] != "\n":
            chars[index] = " "


def _blank_code_spans(chars: list[str], start: int, end: int) -> None:
    """Blank out the code spans in one inline segment (CommonMark §6.1).

    A code span ends at the next backtick run of the same length. The starts of the
    runs, by length, are found first, so an opening run without a closing run does
    not scan the rest of the segment.
    """
    runs: dict[int, list[int]] = {}
    index = start
    while index < end:
        if chars[index] != "`":
            index += 1
            continue
        run_end = index
        while run_end < end and chars[run_end] == "`":
            run_end += 1
        runs.setdefault(run_end - index, []).append(index)
        index = run_end
    index = start
    while index < end:
        char = chars[index]
        if char == "\\":
            index += 2
            continue
        if char != "`":
            index += 1
            continue
        run_end = index
        while run_end < end and chars[run_end] == "`":
            run_end += 1
        starts = runs.get(run_end - index, [])
        found = bisect.bisect_left(starts, run_end)
        if found == len(starts):
            index = run_end
            continue
        close = starts[found] + run_end - index
        _blank(chars, index, close)
        index = close


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    while index - backslashes - 1 >= 0 and text[index - backslashes - 1] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def _skip_whitespace(masked: str, index: int, end: int) -> tuple[int, bool]:
    """Skip spaces, tabs, and at most one line ending. Returns the new index and if it moved."""
    moved = index
    newline = False
    while moved < end and (masked[moved] in " \t" or (masked[moved] == "\n" and not newline)):
        newline = newline or masked[moved] == "\n"
        moved += 1
    return moved, moved != index


def _destination(masked: str, index: int, end: int) -> tuple[int, int, bool, int] | None:
    """Parse ``destination "title")`` after ``(``.

    Returns the destination start and end, whether it was in angle brackets, and the
    offset after the closing ``)``.
    """
    index, _ = _skip_whitespace(masked, index, end)
    if index < end and masked[index] == "<":
        probe = index + 1
        while probe < end and masked[probe] not in "<>\n":
            probe += 2 if masked[probe] == "\\" else 1
        if probe >= end or masked[probe] != ">":
            return None
        dest_start, dest_end, angle = index + 1, probe, True
        index = probe + 1
    else:
        probe = index
        depth = 0
        while probe < end:
            char = masked[probe]
            if char == "\\" and probe + 1 < end and masked[probe + 1] in _ASCII_PUNCTUATION:
                probe += 2
                continue
            if char == "(":
                depth += 1
                # The limit also bounds the work: a scan that passes the "](" of
                # a later link is one level deeper, so at most this many scans
                # pass any offset.
                if depth > _MAX_PAREN_DEPTH:
                    return None
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            elif char.isspace() or ord(char) < 0x20:
                break
            probe += 1
        if depth != 0:
            return None
        dest_start, dest_end, angle = index, probe, False
        index = probe
    index, spaced = _skip_whitespace(masked, index, end)
    if spaced and index < end and masked[index] in "\"'(":
        # A title in parentheses cannot hold an unescaped "(" (CommonMark §6.3).
        stops = ")(" if masked[index] == "(" else masked[index]
        probe = index + 1
        while probe < end and masked[probe] not in stops:
            probe += 2 if masked[probe] == "\\" else 1
        if probe >= end or masked[probe] == "(":
            return None
        index, _ = _skip_whitespace(masked, probe + 1, end)
    if index < end and masked[index] == ")":
        return dest_start, dest_end, angle, index + 1
    return None


def _inline_links(text: str, masked: str, start: int, end: int) -> list[Link]:
    """Inline links, images, and autolinks in one inline segment.

    One pass matches brackets with a stack: each ``]`` closes the last open ``[``.
    A ``]`` right before ``(`` makes a link when a destination follows.
    """
    found: list[Link] = []
    openers: list[int] = []
    index = start
    while index < end:
        char = masked[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            openers.append(index)
        elif char == "]" and openers:
            opening = openers.pop()
            parsed = None
            if index + 1 < end and masked[index + 1] == "(":
                parsed = _destination(masked, index + 2, end)
            if parsed is not None:
                dest_start, dest_end, angle, after = parsed
                image = opening > start and masked[opening - 1] == "!"
                image = image and not _is_escaped(masked, opening - 1)
                found.append(
                    Link(
                        kind="image" if image else "inline",
                        start=opening - 1 if image else opening,
                        end=after,
                        text_start=opening + 1,
                        text_end=index,
                        dest_start=dest_start,
                        dest_end=dest_end,
                        angle=angle,
                        href=unescape(text[dest_start:dest_end]),
                    )
                )
                index = after
                continue
        index += 1
    covered = _merge_spans((link.start, link.end) for link in found)
    covered_starts = [first for first, _ in covered]
    for match in _AUTOLINK_RE.finditer(masked, start, end):
        if _is_escaped(masked, match.start()):
            continue
        before = bisect.bisect_right(covered_starts, match.start()) - 1
        if before >= 0 and match.start() < covered[before][1]:
            continue
        found.append(
            Link(
                kind="autolink",
                start=match.start(),
                end=match.end(),
                text_start=match.start(1),
                text_end=match.end(1),
                dest_start=match.start(1),
                dest_end=match.end(1),
                angle=True,
                href=match.group(1),
            )
        )
    return found


def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorted, disjoint spans that cover the same offsets as ``spans``."""
    merged: list[tuple[int, int]] = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def _footnote_refs(masked: str, start: int, end: int) -> list[FootnoteRef]:
    """``[^label]`` references: the label has no whitespace and no ``]``.

    One pass: a ``]`` closes the first ``[^`` that is still open, and whitespace
    closes none. A reference cannot start inside an earlier one.
    """
    refs: list[FootnoteRef] = []
    opening: int | None = None
    for match in _FOOTNOTE_TOKEN_RE.finditer(masked, start, end):
        position = match.start()
        if match.group() == "[^":
            if opening is None and not _is_escaped(masked, position):
                opening = position
        elif opening is not None:
            if match.group() == "]" and position > opening + 2:
                label = masked[opening + 2 : position]
                refs.append(FootnoteRef(label=label, start=opening, end=position + 1))
            opening = None
    return refs


def _wikilinks(masked: str, start: int, end: int) -> list[Wikilink]:
    return [
        Wikilink(target=match.group(1).strip(), start=match.start(), end=match.end())
        for match in _WIKILINK_RE.finditer(masked, start, end)
        if not _is_escaped(masked, match.start())
    ]


def _definition(text: str, masked: str, start: int, end: int) -> Link | None:
    """The link reference definition in the lines that markdown-it used for one."""
    match = _DEFINITION_RE.search(masked, start, end)
    if match is None:
        return None
    group = "angle" if match.group("angle") is not None else "plain"
    dest_start, dest_end = match.start(group), match.end(group)
    return Link(
        kind="definition",
        start=match.start(),
        end=match.end(),
        text_start=match.start("label"),
        text_end=match.end("label"),
        dest_start=dest_start,
        dest_end=dest_end,
        angle=group == "angle",
        href=unescape(text[dest_start:dest_end]),
    )
