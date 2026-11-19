"""Save a good answer to a Query as a synthesis Wiki Page."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import WIKI_DIR, WIKI_DIRS, BrainLayout, slugify, unique_slug
from second_brain_builder.ingest._wiki import (
    bundle_paths,
    check_page,
    cited_in_order,
    clean_tags,
    merge_sources,
    one_line,
    render_citations,
    source_catalog,
    starts_with_frontmatter,
    strip_footnote_definitions,
)
from second_brain_builder.ingest.models import InvalidAnswerError
from second_brain_builder.okf import Frontmatter, Page, PageType, Stamp, write_page

UNKNOWN_AGENT = "agent:unknown"
"""``generated.by`` of a saved answer when the model that wrote it is not known."""


def save_answer(
    root: Path,
    title: str,
    body: str,
    source_ids: list[str],
    description: str,
    tags: list[str],
    model_name: str | None,
) -> PurePosixPath:
    """Write an answer as a new synthesis page ``wiki/syntheses/<slug>.md`` and return its path.

    The slug comes from the title; a page that exists already is never replaced, so
    a title that is taken gets ``-2``, ``-3``, ... The body cites with footnote
    references whose labels are Source ids (``[^dune]``). As in the Ingest tool
    write_page, ``sources`` and the footnote definitions come from those citations
    and the Canonical Documents; definitions in ``body`` are replaced, also in block
    quotes and list items. Every id in ``source_ids`` must be cited too.
    ``generated.by`` is ``sbb/<model_name>``, or ``UNKNOWN_AGENT`` without a model
    name. ``index.md`` and ``log.md`` do not change.

    Raises:
        InvalidAnswerError: The title or body is empty, the body has frontmatter, it
            cites no Source or an id that is not a Source of this Brain, an id of
            ``source_ids`` is not cited, the body ends inside a code block or HTML
            block (the footnote definitions after it would be part of the block), or
            the page breaks another OKF rule. Nothing was written.
    """
    clean_title = one_line(title)
    text = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    problems: list[str] = []
    if not clean_title:
        problems.append("The title is empty.")
    if not text.strip():
        problems.append("The body is empty.")
    elif starts_with_frontmatter(text):
        problems.append("The body starts with a --- line. Give the Markdown body only.")
    if problems:
        raise InvalidAnswerError(problems)

    catalog = source_catalog(root)
    text = strip_footnote_definitions(text).rstrip()
    labels = list(dict.fromkeys([*cited_in_order(text), *source_ids]))
    unknown = [label for label in labels if label not in catalog]
    if unknown:
        raise InvalidAnswerError(
            [f"{label!r} is not the id of a Source in this Brain." for label in unknown]
        )
    if not labels:
        raise InvalidAnswerError(["The body cites no Source. Cite claims with [^source-id]."])

    syntheses = root / WIKI_DIR / WIKI_DIRS[PageType.SYNTHESIS]
    taken = {file.stem for file in syntheses.glob("*.md")} if syntheses.is_dir() else set()
    slug = unique_slug(slugify(clean_title), taken)
    path = BrainLayout().wiki_path(PageType.SYNTHESIS, slug)
    by = f"sbb/{model_name.strip()}" if model_name and model_name.strip() else UNKNOWN_AGENT
    page = Page(
        path=path,
        frontmatter=Frontmatter(
            type=PageType.SYNTHESIS.value,
            title=clean_title,
            description=one_line(description) or None,
            tags=clean_tags(tags),
            sources=merge_sources(labels, catalog, []),
            generated=Stamp(by=by, at=datetime.now(timezone.utc).replace(microsecond=0)),
        ),
        body=render_citations(text, cited_in_order(text), catalog),
    )
    errors, _ = check_page(page, bundle_paths(root) | {path})
    if errors:
        raise InvalidAnswerError(errors)
    write_page(root, page)
    return path
