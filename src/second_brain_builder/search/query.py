"""Turn user query text into safe FTS5 MATCH expressions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from second_brain_builder.search.text import clean_text


@dataclass(frozen=True)
class QueryTerms:
    """The terms of one user query, as FTS5 strings.

    Make it with ``QueryTerms.parse``. Each term is an FTS5 string (in double
    quotes, with each inner double quote doubled), so FTS5 syntax that the user
    types is plain text: the operators ``AND``, ``OR``, ``NOT``, and ``NEAR``,
    column filters (``title:``), ``*``, ``^``, ``+``, parentheses, and quotes.
    FTS5 splits each string into tokens as it splits the indexed text, and the
    tokens of one term must be next to each other: ``heron's`` finds "heron's".
    The last term also matches as a prefix, so ``gre`` finds "grey" while the user
    types.
    """

    terms: tuple[str, ...]

    @classmethod
    def parse(cls, text: str) -> QueryTerms | None:
        """The terms of ``text``: the parts between whitespace.

        Control characters become spaces, because FTS5 reads a NUL as the end of
        the query, and lone surrogates become U+FFFD (see ``clean_text``). A term
        with no letters or digits (``*``, ``:``) gives an empty FTS5 string, which
        FTS5 ignores next to other terms and which matches nothing alone.

        Returns:
            The terms, or ``None`` when ``text`` has no terms.
        """
        words = clean_text(text).split()
        if not words:
            return None
        quoted = [fts_string(word) for word in words]
        quoted[-1] += "*"
        return cls(tuple(quoted))

    def every_term(self) -> str:
        """An expression that matches a row with every term, in any of its columns."""
        return " ".join(self.terms)

    def any_term(self) -> str:
        """An expression that matches a row with at least one of the terms."""
        return " OR ".join(self.terms)


def fts_string(text: str) -> str:
    """``text`` as one FTS5 string: in double quotes, with each inner double quote doubled.

    FTS5 reads every character in it as text, never as syntax, and splits it into
    tokens as it splits the indexed text: the tokens must be next to each other.
    Control characters become spaces and lone surrogates U+FFFD (see ``clean_text``).
    """
    return '"' + clean_text(text).replace('"', '""') + '"'


def in_columns(columns: Sequence[str], expression: str) -> str:
    """``expression`` limited to ``columns``: each phrase must match inside one of them.

    ``columns`` are column names that the code defines, never user text.
    """
    return "{" + " ".join(columns) + "} : (" + expression + ")"
