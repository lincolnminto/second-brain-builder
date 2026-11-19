"""The text that the search index stores: page fields as plain text, without Markdown markup."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin

# C0 control characters except tab and line feed, and DEL. They have no meaning in
# page text. The index uses two of them to mark matches in snippets, and FTS5 reads
# a NUL in a query as the end of the string.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# Surrogate code points. A str holds them after a YAML or JSON escape such as
# "\ud800", but UTF-8 cannot encode them, so SQLite cannot store or match them.
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
_HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
# A tag ends at the first ">", and cannot hold a "<". Without the "<" in the class,
# each "<" of a long block with no ">" would scan to the end of the block, which
# takes time that grows with the square of the block length.
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^<>]*>")

# The same Markdown dialect as the okf module: CommonMark, GFM tables, footnotes.
_MARKDOWN = MarkdownIt("commonmark").enable("table").use(footnote_plugin)

_TEXT_TOKENS = frozenset({"text", "text_special", "code_inline"})
_LINE_BREAK_TOKENS = frozenset({"softbreak", "hardbreak"})
_CODE_BLOCK_TOKENS = frozenset({"fence", "code_block"})

PASSAGE_LENGTH = 2000
"""The usual maximum length of a passage, in characters (see ``split_passages``)."""


def clean_text(text: str) -> str:
    """``text`` in a form that the index can store and match.

    Each control character (other than tab and line feed) becomes a space. Each
    surrogate pair becomes the character that it encodes, and each lone surrogate
    becomes U+FFFD (the replacement character).
    """
    text = _CONTROL_RE.sub(" ", text)
    if _SURROGATE_RE.search(text) is None:
        return text
    # UTF-16 joins the pairs, and its decoder replaces each lone surrogate.
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def markdown_to_text(body: str) -> str:
    """The text that a reader of the rendered Markdown ``body`` sees.

    Link text, image descriptions, code, table cells, and footnote definitions are
    kept. Markup is left out: link and image destinations, footnote reference labels,
    link reference definitions, emphasis markers, and HTML tags and comments. Without
    this, the paths in links (``/wiki/entities/x.md``) would make words such as
    ``wiki`` or ``md`` match almost every page. Each block ends with a line break.

    Escapes and character references are decoded: ``\\*`` gives ``*`` and ``&lt;``
    gives ``<``. The result is text, never Markdown or HTML to render.
    """
    parts: list[str] = []
    for token in _MARKDOWN.parse(clean_text(body)):
        if token.type == "inline":
            _append_inline(token.children or [], parts)
        elif token.type in _CODE_BLOCK_TOKENS:
            parts.append(token.content)
        elif token.type == "html_block":
            parts.append(_html_text(token.content))
        else:
            continue
        parts.append("\n")
    return "".join(parts)


def split_passages(text: str, length: int = PASSAGE_LENGTH) -> list[str]:
    """``text`` cut into passages of at most ``length`` characters, in order.

    Each cut follows the last whitespace character inside the length, so a word is
    never split, unless a run without whitespace is longer than ``length``. The
    passages joined together give ``text`` again. Empty text gives no passages.
    """
    if length < 1:
        raise ValueError(f"length must be 1 or more, not {length}")
    passages: list[str] = []
    start = 0
    while len(text) - start > length:
        end = start + length
        cut = max(text.rfind(space, start, end) for space in (" ", "\n", "\t")) + 1
        if cut <= start:  # no whitespace in the window
            cut = end
        passages.append(text[start:cut])
        start = cut
    if start < len(text):
        passages.append(text[start:])
    return passages


def _append_inline(tokens: Sequence[Token], parts: list[str]) -> None:
    for token in tokens:
        if token.type in _TEXT_TOKENS:
            parts.append(token.content)
        elif token.type in _LINE_BREAK_TOKENS:
            parts.append("\n")
        elif token.type == "image":
            # The children are the parsed image description (alt text).
            _append_inline(token.children or [], parts)


def _html_text(content: str) -> str:
    """The text of an HTML block, without comments and tags."""
    text = _HTML_TAG_RE.sub(" ", _HTML_COMMENT_RE.sub(" ", content))
    return html.unescape(text)
