"""Snippets: page text around the matches, as a Markdown fragment that is safe to render."""

from __future__ import annotations

import re

from second_brain_builder.search.models import HIGHLIGHT_MARK

MATCH_START = "\x02"
"""What FTS5 ``snippet()`` puts before each match.

It cannot occur in indexed text, because ``clean_text`` replaces control
characters, so a mark shows a real match.
"""
MATCH_END = "\x03"
"""What FTS5 ``snippet()`` puts after each match."""
ELLIPSIS = "…"
"""What marks text that a snippet leaves out."""
SNIPPET_TOKENS = 20
"""The length of a snippet, in tokens (FTS5 ``snippet()``) or words (``leading_snippet``)."""

_MARKS_RE = re.compile(f"[{MATCH_START}{MATCH_END}]")
# Characters that start inline Markdown or HTML anywhere on a line: escapes, code
# spans, emphasis, strikethrough, links, images, footnotes, autolinks, raw HTML,
# character references, and table cells.
_INLINE_RE = re.compile(r"[\\`*_~\[\]<&|]")
# What starts a block when it begins a line: a heading, a block quote, a list item,
# a thematic break, or a setext underline. The group is the character to escape.
_LINE_START_RE = re.compile(r"^(?:([#>+=-])|\d{1,9}([.)])(?=\s|$))")


def render_snippet(marked: str, *, cut_before: bool = False, cut_after: bool = False) -> str:
    """The Markdown snippet for ``marked``, text with ``MATCH_START`` and ``MATCH_END`` marks.

    Whitespace is collapsed to single spaces, so the snippet is one line. Each
    character of the text that Markdown can read as markup gets a backslash before
    it, so a CommonMark renderer shows the text as it is: no emphasis, code, link,
    image, footnote, HTML tag, or character reference comes from it, and it does
    not start a heading, quote, or list. Then each marked match is put between two
    ``HIGHLIGHT_MARK``, so the matches are the only bold text.

    Args:
        marked: The text, with balanced marks, as FTS5 ``snippet()`` gives it.
        cut_before: Text comes before ``marked``: start with ``ELLIPSIS`` if it
            does not already.
        cut_after: Text comes after ``marked``: end with ``ELLIPSIS`` if it does
            not already.
    """
    collapsed = " ".join(marked.split())
    # The marks alternate (start, end, start, ...), so the odd pieces are matches.
    pieces = [_escape_inline(piece) for piece in _MARKS_RE.split(collapsed)]
    pieces[0] = _escape_line_start(pieces[0])
    rendered = HIGHLIGHT_MARK.join(pieces)
    if cut_before and rendered and not rendered.startswith(ELLIPSIS):
        rendered = ELLIPSIS + rendered
    if cut_after and rendered and not rendered.endswith(ELLIPSIS):
        rendered += ELLIPSIS
    return rendered


def leading_snippet(text: str, *, cut_after: bool = False) -> str:
    """The Markdown snippet for the start of ``text``, without matches.

    It has the first ``SNIPPET_TOKENS`` words, and ``ELLIPSIS`` when more text follows.
    """
    words = text.split()
    more = cut_after or len(words) > SNIPPET_TOKENS
    return render_snippet(" ".join(words[:SNIPPET_TOKENS]), cut_after=more)


def _escape_inline(text: str) -> str:
    return _INLINE_RE.sub(r"\\\g<0>", text)


def _escape_line_start(text: str) -> str:
    match = _LINE_START_RE.match(text)
    if match is None:
        return text
    position = match.start(1) if match.group(1) is not None else match.start(2)
    return text[:position] + "\\" + text[position:]
