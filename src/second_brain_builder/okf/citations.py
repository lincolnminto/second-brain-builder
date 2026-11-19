"""Footnote citations: ``[^source-id]`` references and their definitions (OKF §5.1)."""

from __future__ import annotations

from second_brain_builder.okf import _markdown
from second_brain_builder.okf.links import apply_edits

_CLOSES_CLAUSE = frozenset(" \t\n.,;:!?)")


def footnote_labels(body: str) -> set[str]:
    """The labels of all footnote references and definitions in a body, outside code."""
    found = _markdown.scan(body)
    return {ref.label for ref in found.footnote_refs} | {
        definition.label for definition in found.footnote_defs
    }


def cited_labels(body: str) -> set[str]:
    """The labels of the footnote references in a body, outside code.

    A definition without a reference does not cite anything.
    """
    return {ref.label for ref in _markdown.scan(body).footnote_refs}


def remove_citations(body: str, source_id: str) -> str:
    """Remove the footnote references to ``source_id`` and their definition blocks.

    A space before a removed reference goes too when only punctuation or a line end
    follows, so ``claim [^dune].`` becomes ``claim.``. Blank lines around a removed
    definition are merged. Code is not changed.
    """
    found = _markdown.scan(body)
    text = found.text
    edits: list[tuple[int, int, str]] = []
    for ref in found.footnote_refs:
        if ref.label != source_id:
            continue
        start = ref.start
        if ref.end == len(text) or text[ref.end] in _CLOSES_CLAUSE:
            spaces = start
            while spaces > 0 and text[spaces - 1] in " \t":
                spaces -= 1
            if spaces > 0 and text[spaces - 1] != "\n":  # keep indentation
                start = spaces
        edits.append((start, ref.end, ""))
    text = apply_edits(text, edits)
    for definition in reversed(_markdown.scan(text).footnote_defs):
        if definition.label == source_id:
            text = _remove_block(text, definition.start, definition.end)
    return text


def _remove_block(text: str, start: int, end: int) -> str:
    """Remove whole lines ``text[start:end]`` and keep one blank line between the neighbors."""
    before = text[:start]
    after = text[end:].lstrip("\n")
    blank_after = text[start:end].endswith("\n\n") or text[end:].startswith("\n")
    if not after:
        kept = before.rstrip("\n")
        return kept + "\n" if kept else ""
    if not before:
        return after
    if blank_after and not before.endswith("\n\n"):
        before += "\n"
    return before + after
