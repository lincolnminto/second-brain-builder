"""Models that cross the boundary of the search module."""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict

HIGHLIGHT_MARK = "**"
"""The text before and after each match in ``SearchHit.snippet``.

It is Markdown bold, so a Markdown renderer shows the matches in bold, and an agent
reads them as emphasis. The page text around the marks is escaped (see
``SearchHit.snippet``), so the matches are the only bold text. Render the snippet
as Markdown: do not split it on this mark, because an escaped ``\\*`` next to a
match also gives ``**`` in the string.
"""


class SearchHit(BaseModel):
    """One page that matches a query."""

    model_config = ConfigDict(frozen=True)

    path: PurePosixPath
    """Path from the Brain root, for example ``wiki/entities/grey-heron.md``."""
    title: str
    """The frontmatter title, or the filename stem when the page has none.

    Plain text from the page: show it as text, not as Markdown or HTML.
    """
    type: str
    """The frontmatter ``type``: ``source`` for a Canonical Document, else a Wiki Page type."""
    snippet: str
    """A one-line Markdown fragment: the page text around the best match.

    The text comes from the body, else from the description. Each match is between
    two ``HIGHLIGHT_MARK``. When only the title or the tags match, the snippet is
    the start of the description, else of the body, without marks. Whitespace is
    collapsed to single spaces, and ``…`` marks cut text.

    Page content is untrusted, so the page text in the snippet is escaped: each
    character that Markdown could read as markup has a backslash before it
    (``\\*``, ``\\[``, ``\\<``, ``\\&``, and others). A Markdown renderer shows the
    page text as text: no link, image, or HTML tag comes from it, and only the
    matches are bold.
    """
    score: float
    """Relevance: higher is better, and the hits of a query are sorted by it.

    Compare scores only within one query.
    """


class PassageHit(BaseModel):
    """One passage of a Canonical Document that ``SearchIndex.find_passages`` found."""

    model_config = ConfigDict(frozen=True)

    path: PurePosixPath
    """The Canonical Document, from the Brain root."""
    title: str
    """The title of the Canonical Document, as the index stores it."""
    segment: int
    """The index of the passage in ``segment_markdown(body, PASSAGE_CHARS)``."""
    char_start: int
    """Where the passage starts in the body of the Canonical Document (after the frontmatter)."""
    char_end: int
    heading_trail: tuple[str, ...]
    """The headings that the passage is under, outermost first."""
    text: str
    """The passage as plain text (``search.text.markdown_to_text``).

    Untrusted content: show it as text, never as Markdown or HTML.
    """
    score: float
    """BM25 relevance mapped into [0, 1): higher is better. 0 when no words were given."""
