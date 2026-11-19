"""Remove a Source from the Wiki Pages, without an LLM."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from second_brain_builder.brains import WIKI_DIR, BrainLayout, is_slug, read_pages
from second_brain_builder.ingest._wiki import fresh_name, is_canonical_path, page_links
from second_brain_builder.ingest.models import IngestEvent, IngestResult
from second_brain_builder.okf import (
    WIKI_PAGE_TYPES,
    Page,
    PageType,
    _markdown,
    extract_links,
    footnote_labels,
    remove_citations,
    remove_links,
    resolve_link,
    rewrite_links,
    write_page,
)

_REFERENCE_DEFINITION_RE = re.compile(
    r"^[ \t>]*(?:(?:[-+*]|\d{1,9}[.)])[ \t][ \t>]*)?"
    r"\[(?P<label>(?:[^\[\]\\\n]|\\.)+)\]:[ \t]*(?:\n[ \t>]*)?"
    r"(?P<destination><[^<>\n]*>|[^\s<]\S*)(?P<after>[^\n]*)\n?",
    re.MULTILINE,
)
"""A link reference definition ``[label]: destination "title"``, with its line ending.

The destination can be on the line after the label, as in CommonMark. okf finds
definitions with the same lines, in the places where markdown-it reads one. No two
parts of the pattern match the same run of spaces, so a search takes linear time.
"""

_TITLE_LINE_RE = re.compile(r"[ \t>]*(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\))[ \t]*(?:\n|\Z)")
"""A line with only a link title: the end of a definition whose title is on its own line."""

_REFERENCE_LINK_RE = re.compile(
    r"(?<![\\\]])!?\[(?P<text>(?:[^\[\]\\\n|]|\\[^\n])*)\]"
    r"(?:\[(?P<label>(?:[^\[\]\\\n|]|\\[^\n])*)\]|(?![(\[:]))"
)
"""A reference link or image: ``[text][label]``, ``[label][]``, or ``[label]``."""


def remove_source(
    root: Path, source_id: str, on_event: Callable[[IngestEvent], None]
) -> IngestResult:
    """Remove the Source ``source_id`` from the Wiki Pages of the Brain at ``root``.

    The result is the same for the same files: no LLM takes part.

    1. Each Wiki Page under ``wiki/`` loses its citations of the Source (the
       ``[^source_id]`` references and definition) and its ``sources`` entry.
    2. A Wiki Page with no ``sources`` left is deleted, and so is the summary page
       of the Source, ``wiki/summaries/<source_id>.md``.
    3. In the Wiki Pages that stay, links to the deleted pages, and to the files of
       the Source directory (as the ``sources`` entries name it), become their plain
       link text, and images their alt text. This covers inline links and
       reference-style links, whose definitions are removed too.

    The Source directory itself, ``index.md``, and ``log.md`` do not change: the
    caller removes the directory before or after, and writes the index and log.
    Emits ``page_deleted`` for each deleted page and ``page_written`` for each
    changed page.

    Raises:
        ValueError: ``source_id`` is not a slug.
    """
    if not is_slug(source_id):
        raise ValueError(f"not a Source id: {source_id!r}")
    summary_path = BrainLayout().wiki_path(PageType.SUMMARY, source_id)
    all_pages = read_pages(root)
    pages = [page for page in all_pages if _is_wiki_page(page)]
    # The Source directory: from the Canonical Document if it is still there, and
    # from the sources entries that cite it.
    source_dirs = {
        page.path.parent
        for page in all_pages
        if is_canonical_path(page.path) and page.path.stem == source_id
    }

    changed: dict[PurePosixPath, Page] = {}
    deleted: dict[PurePosixPath, Page] = {}
    for page in pages:
        frontmatter = page.frontmatter
        if frontmatter is None:  # _is_wiki_page keeps only pages with frontmatter
            continue
        cited = [ref for ref in frontmatter.sources if ref.id == source_id]
        for ref in cited:
            target = resolve_link(page.path, ref.resource)
            if target is not None and is_canonical_path(target):
                source_dirs.add(target.parent)
        if not cited and source_id not in footnote_labels(page.body):
            continue
        remaining = [ref for ref in frontmatter.sources if ref.id != source_id]
        if not remaining or page.path == summary_path:
            deleted[page.path] = page
            continue
        changed[page.path] = page.model_copy(
            update={
                "frontmatter": frontmatter.model_copy(update={"sources": remaining}),
                "body": remove_citations(page.body, source_id),
            }
        )

    gone = set(deleted)
    for page in pages:
        if page.path in gone:
            continue
        current = changed.get(page.path, page)
        targets = [
            target
            for link in _markdown.scan(current.body).links
            if (target := resolve_link(current.path, link.href)) is not None
            and (target in gone or any(directory in target.parents for directory in source_dirs))
        ]
        if not targets:
            continue
        body = remove_links(current.body, targets, from_path=current.path)
        body = _remove_reference_links(body, targets, current.path)
        if body != current.body:
            changed[page.path] = current.model_copy(update={"body": body})

    for path in sorted(changed):
        write_page(root, changed[path])
    for path in sorted(deleted):
        root.joinpath(*path.parts).unlink(missing_ok=True)

    for path in sorted(deleted):
        on_event(IngestEvent(kind="page_deleted", data=_page_data(deleted[path])))
    for path in sorted(changed):
        page = changed[path]
        data = _page_data(page)
        data["links"] = [str(link) for link in page_links(page.path, page.body)]
        on_event(IngestEvent(kind="page_written", data=data))
    return IngestResult(written=sorted(changed), deleted=sorted(deleted))


def _is_wiki_page(page: Page) -> bool:
    return (
        page.frontmatter is not None
        and page.frontmatter.type in WIKI_PAGE_TYPES
        and page.path.parts[0] == WIKI_DIR.name
    )


def _page_data(page: Page) -> dict[str, Any]:
    frontmatter = page.frontmatter
    title = (frontmatter.title if frontmatter else None) or page.path.stem
    return {
        "path": str(page.path),
        "title": " ".join(title.split()),
        "type": frontmatter.type if frontmatter else "",
    }


def _remove_reference_links(
    body: str, targets: list[PurePosixPath], from_path: PurePosixPath
) -> str:
    """Remove the link reference definitions that point at ``targets``, and turn the
    reference links and images that used only them into their text.

    ``okf.remove_links`` changes inline links only, so this pass handles the
    reference-style ones. okf still decides what a link is: ``rewrite_links`` points
    exactly the destinations that okf reads at a marker path, and ``remove_links``
    changes exactly the links that okf reads outside code. A page with a definition
    that this pass cannot take apart keeps its reference-style links.
    """
    marker = fresh_name(body, "sbb-removed")
    moves = {target: PurePosixPath(marker, str(number)) for number, target in enumerate(targets)}
    marked = rewrite_links(body, moves, from_path=from_path)
    if marked == body:
        return body
    spans: list[tuple[int, int]] = []
    removed: set[str] = set()
    for match in _REFERENCE_DEFINITION_RE.finditer(marked):
        if marker not in match.group("destination"):
            continue
        end = match.end()
        title = None if match.group("after").strip() else _TITLE_LINE_RE.match(marked, end)
        spans.append((match.start(), title.end() if title else end))
        removed.add(_label_key(match.group("label")))
    text = _cut_lines(marked, spans)
    if marker in text:
        return body
    return _unlink_references(text, removed - _defined_labels(text))


def _defined_labels(text: str) -> set[str]:
    """The keys (see ``_label_key``) of the labels that okf reads definitions for in ``text``.

    Each text that looks like a definition gets a destination of its own, and okf
    lists the destinations of the definitions that it reads, outside code.
    """
    candidates = list(_REFERENCE_DEFINITION_RE.finditer(text))
    if not candidates:
        return set()
    marker = fresh_name(text, "sbb-defined")
    pieces: list[str] = []
    position = 0
    for number, candidate in enumerate(candidates):
        pieces += [text[position : candidate.start("destination")], f"/{marker}-{number}.md"]
        position = candidate.end("destination")
    pieces.append(text[position:])
    hrefs = set(extract_links("".join(pieces)))
    return {
        _label_key(candidate.group("label"))
        for number, candidate in enumerate(candidates)
        if f"/{marker}-{number}.md" in hrefs
    }


def _unlink_references(text: str, labels: set[str]) -> str:
    """Turn the reference links and images to ``labels`` (as ``_label_key`` gives them) into
    their text, outside code.

    Each one first becomes an inline link to a marker path, which ``remove_links``
    turns into text where okf reads a link; the others get their reference back.
    """
    uses = [
        use
        for use in _REFERENCE_LINK_RE.finditer(text)
        if not use.group("text").startswith("^")
        and _label_key(use.group("label") or use.group("text")) in labels
    ]
    if not uses:
        return text
    marker = fresh_name(text, "sbb-unlinked")
    pieces: list[str] = []
    references: list[str] = []
    position = 0
    for number, use in enumerate(uses):
        reference_start = use.end("text") + 1  # after the "]" of the text
        pieces += [text[position:reference_start], f"(/{marker}-{number}.md)"]
        references.append(text[reference_start : use.end()])
        position = use.end()
    pieces.append(text[position:])
    unlinked = remove_links(
        "".join(pieces), [PurePosixPath(f"{marker}-{number}.md") for number in range(len(uses))]
    )
    return re.sub(
        rf"\(/{re.escape(marker)}-(\d+)\.md\)",
        lambda match: references[int(match.group(1))],
        unlinked,
    )


def _label_key(label: str) -> str:
    """The key that matches a reference label to its definition: case-folded, one space."""
    return " ".join(label.split()).casefold()


def _cut_lines(text: str, spans: list[tuple[int, int]]) -> str:
    """``text`` without the line ``spans`` (in order), and without the blank lines that
    a cut doubles."""
    pieces: list[str] = []
    position = 0
    for start, end in spans:
        if start < position:
            continue
        blank_before = start == 0 or text[max(0, start - 2) : start] == "\n\n"
        if blank_before and text.startswith("\n", end):
            end += 1
        pieces.append(text[position:start])
        position = end
    pieces.append(text[position:])
    return "".join(pieces)
