"""The code-written files: the bundle-root index.md and log.md."""

from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.okf import (
    Frontmatter,
    LogKind,
    OKFError,
    Page,
    SourceRef,
    log_entry,
    prepend_log,
    read_page,
    render_index,
)

FIXTURE_BRAIN = Path(__file__).parents[1] / "fixtures" / "okf" / "brain"
MOBY_DICK_DOC = PurePosixPath("sources/classics/moby-dick/moby-dick.md")
MOBY_DICK_REF = SourceRef(id="moby-dick", resource=f"/{MOBY_DICK_DOC}")


def _fixture_pages() -> list[Page]:
    paths = sorted(FIXTURE_BRAIN.rglob("*.md"))
    return [
        read_page(FIXTURE_BRAIN, PurePosixPath(path.relative_to(FIXTURE_BRAIN).as_posix()))
        for path in paths
        if not path.relative_to(FIXTURE_BRAIN).parts[0].startswith(".")
    ]


def _wiki_page(path: str, type_: str, title: str | None, description: str | None) -> Page:
    return Page(
        path=PurePosixPath(path),
        frontmatter=Frontmatter(
            type=type_, title=title, description=description, sources=[MOBY_DICK_REF]
        ),
        body="Text.[^moby-dick]\n",
    )


def test_render_index_rebuilds_the_fixture_index_from_its_pages() -> None:
    # The page list holds every .md file, also AGENTS.md, index.md, and log.md:
    # files that are not concepts are not listed.
    index_text = (FIXTURE_BRAIN / "index.md").read_text(encoding="utf-8")

    rendered = render_index(
        "Example Brain",
        "Synthetic test Brain with one public-domain novel.",
        _fixture_pages(),
        previous=index_text,
    )

    assert rendered == index_text


def test_render_index_sections_order_and_entries() -> None:
    pages = [
        _wiki_page("wiki/entities/starbuck.md", "entity", "starbuck", "First mate."),
        _wiki_page("wiki/syntheses/two-captains.md", "synthesis", "Two [captains]", None),
        _wiki_page("wiki/entities/ahab.md", "entity", "Ahab", "Captain of the Pequod."),
        _wiki_page("wiki/entities/queequeg.md", "entity", None, "Harpooneer."),
        _wiki_page("wiki/entities/a-fish.md", "Fish Species", "A fish", "Unknown type."),
        Page(path=MOBY_DICK_DOC, frontmatter=Frontmatter(type="source", title="Moby-Dick")),
    ]

    assert render_index("Example Brain", "Test Brain.", pages) == (
        "---\n"
        'okf_version: "0.3"\n'
        "title: Example Brain\n"
        "description: Test Brain.\n"
        "---\n"
        "\n"
        "# Sources\n"
        "\n"
        "* [Moby-Dick](/sources/classics/moby-dick/moby-dick.md)\n"
        "\n"
        "# Entities\n"
        "\n"
        "* [Ahab](/wiki/entities/ahab.md) - Captain of the Pequod.\n"
        "* [queequeg](/wiki/entities/queequeg.md) - Harpooneer.\n"
        "* [starbuck](/wiki/entities/starbuck.md) - First mate.\n"
        "\n"
        "# Syntheses\n"
        "\n"
        "* [Two \\[captains\\]](/wiki/syntheses/two-captains.md)\n"
    )


def test_render_index_of_an_empty_brain_has_only_frontmatter() -> None:
    assert render_index("Example Brain", "", []) == (
        '---\nokf_version: "0.3"\ntitle: Example Brain\n---\n'
    )


def test_render_index_keeps_other_keys_of_the_previous_frontmatter() -> None:
    # OKF §8.1: a producer that regenerates index.md keeps the existing frontmatter.
    previous = (
        "---\n"
        'okf_version: "0.2"\n'
        "title: Old name\n"
        "license: CC-BY-4.0\n"
        "x-acme-owner: team:research\n"
        "---\n"
        "\n"
        "# Old body\n"
    )

    assert render_index("New name", "New description.", [], previous=previous) == (
        "---\n"
        'okf_version: "0.3"\n'
        "title: New name\n"
        "description: New description.\n"
        "license: CC-BY-4.0\n"
        "x-acme-owner: team:research\n"
        "---\n"
    )


def test_render_index_rejects_previous_frontmatter_that_is_not_yaml() -> None:
    with pytest.raises(OKFError, match=r"index\.md"):
        render_index("Example Brain", "", [], previous="---\ntitle: [broken\n---\n")


FIXTURE_LOG_PLUS_TODAY = """\
# Log

## 2026-11-19
* **Delete**: Moby-Dick

## 2026-11-18
* **Ingest**: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md) - 4 pages updated
* **Add**: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md) - Plain text, 3 chapters

## 2026-11-17
* **Create**: Example Brain
"""


def test_prepend_log_adds_a_new_day_above_the_older_days() -> None:
    log = (FIXTURE_BRAIN / "log.md").read_text(encoding="utf-8")

    assert prepend_log(log, date(2026, 11, 19), ["**Delete**: Moby-Dick"]) == (
        FIXTURE_LOG_PLUS_TODAY
    )


def test_prepend_log_puts_new_entries_first_under_an_existing_day() -> None:
    log = (FIXTURE_BRAIN / "log.md").read_text(encoding="utf-8")

    result = prepend_log(
        log,
        datetime(2026, 11, 18, 20, 30, tzinfo=timezone.utc),
        ["* **Answer**: [Two captains](/wiki/syntheses/two-captains.md)"],
    )

    assert result == log.replace(
        "## 2026-11-18\n",
        "## 2026-11-18\n* **Answer**: [Two captains](/wiki/syntheses/two-captains.md)\n",
    )


def test_prepend_log_keeps_dates_newest_first_for_a_late_entry() -> None:
    result = prepend_log(FIXTURE_LOG_PLUS_TODAY, date(2026, 11, 16), ["**Create**: Old entry"])

    assert result == FIXTURE_LOG_PLUS_TODAY + "\n## 2026-11-16\n* **Create**: Old entry\n"


def test_prepend_log_starts_a_new_log() -> None:
    assert prepend_log("", date(2026, 11, 17), ["**Create**: Example Brain"]) == (
        "# Log\n\n## 2026-11-17\n* **Create**: Example Brain\n"
    )
    assert prepend_log("# Log\n", date(2026, 11, 17), []) == "# Log\n"


def test_log_entry_formats() -> None:
    assert log_entry(LogKind.INGEST, "Moby-Dick", MOBY_DICK_DOC, "4 pages\nupdated") == (
        "**Ingest**: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md) - 4 pages updated"
    )
    assert log_entry(LogKind.DELETE, "Notes [draft]") == "**Delete**: Notes \\[draft\\]"
    assert log_entry(LogKind.MOVE, "Dune", PurePosixPath("sources/sci fi/dune/dune.md")) == (
        "**Move**: [Dune](/sources/sci%20fi/dune/dune.md)"
    )
