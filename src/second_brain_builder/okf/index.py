"""The bundle-root ``index.md`` (OKF §8)."""

from __future__ import annotations

from typing import Any

from ruamel.yaml.comments import CommentedMap

from second_brain_builder.okf import _yaml
from second_brain_builder.okf._format import markdown_link, one_line
from second_brain_builder.okf.models import OKF_VERSION, OKFError, Page, PageType, is_concept
from second_brain_builder.okf.pages import normalize_text, split_frontmatter

SECTION_TITLES: dict[PageType, str] = {
    PageType.SOURCE: "Sources",
    PageType.SUMMARY: "Summaries",
    PageType.ENTITY: "Entities",
    PageType.CONCEPT: "Concepts",
    PageType.COMPARISON: "Comparisons",
    PageType.SYNTHESIS: "Syntheses",
    PageType.NAME_INDEX: "Name indexes",
}
"""Section headings of ``index.md``, in their order."""

_METADATA_KEYS = ("okf_version", "title", "description")


def render_index(
    title: str,
    description: str,
    pages: list[Page],
    previous: str | None = None,
) -> str:
    """Render the bundle-root ``index.md``.

    The frontmatter has ``okf_version``, ``title``, and ``description``. The body
    has one ``#`` section per page type, in the order of ``SECTION_TITLES``, with
    one entry per page: ``* [Title](/path.md) - description``. Entries are sorted
    by title (case-insensitive, then by path), and empty sections are left out.
    Pages that are not concepts or have a type outside ``PageType`` are not listed.
    A page without a title uses its filename stem.

    ``previous`` is the current ``index.md`` text, if there is one. OKF §8.1 says
    that a producer keeps the existing frontmatter, so its other keys (``license``,
    extension keys) stay, after the three keys above.

    Raises:
        OKFError: The frontmatter of ``previous`` cannot be read.
    """
    metadata = CommentedMap()
    metadata["okf_version"] = _yaml.scalar(OKF_VERSION)
    metadata["title"] = _yaml.scalar(one_line(title))
    if description.strip():
        metadata["description"] = _yaml.scalar(one_line(description))
    for key, value in _previous_metadata(previous).items():
        if key not in _METADATA_KEYS:
            metadata[_yaml.key_node(key)] = _yaml.to_node(value)

    sections: dict[PageType, list[tuple[tuple[str, str, str], str]]] = {
        page_type: [] for page_type in SECTION_TITLES
    }
    for page in pages:
        if not is_concept(page) or page.frontmatter is None:
            continue
        try:
            page_type = PageType(page.frontmatter.type)
        except ValueError:
            continue
        entry_title = one_line(page.frontmatter.title or "") or page.path.stem
        entry = f"* {markdown_link(entry_title, page.path)}"
        if page.frontmatter.description and one_line(page.frontmatter.description):
            entry += f" - {one_line(page.frontmatter.description)}"
        sort_key = (entry_title.casefold(), entry_title, page.path.as_posix())
        sections[page_type].append((sort_key, entry))

    blocks = [
        f"# {SECTION_TITLES[page_type]}\n\n" + "\n".join(entry for _, entry in sorted(entries))
        for page_type, entries in sections.items()
        if entries
    ]
    head = f"---\n{_yaml.dump_mapping(metadata)}---\n"
    if not blocks:
        return head
    return head + "\n" + "\n\n".join(blocks) + "\n"


def _previous_metadata(previous: str | None) -> dict[str, Any]:
    if previous is None:
        return {}
    block, _ = split_frontmatter(normalize_text(previous))
    if block is None:
        return {}
    try:
        return _yaml.load_mapping(block, first_line=2)
    except OKFError as exc:
        raise OKFError(f"index.md: {exc}") from exc
