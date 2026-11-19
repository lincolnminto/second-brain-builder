"""save_answer on a real Brain: synthesis pages with citations from the body."""

from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.ingest import UNKNOWN_AGENT, InvalidAnswerError, save_answer
from second_brain_builder.okf import SourceRef, read_page, validate_bundle, validate_page

ANSWER = (
    "# How do herons hunt?\n\n"
    "The [Grey Heron](/wiki/entities/grey-heron.md) waits in shallow water for fish."
    "[^coastal-birds] At low tide it also hunts in rock pools.[^tide-pools]\n\n"
    "> [^coastal-birds]: [A quoted target](https://example.com/quoted)\n\n"
    "[^tide-pools]: [A wrong target](https://example.com/elsewhere)\n"
)


def all_paths(root: Path) -> set[PurePosixPath]:
    return {
        PurePosixPath(path.relative_to(root).as_posix())
        for path in root.rglob("*")
        if path.is_file() and not path.relative_to(root).as_posix().startswith(".")
    }


def test_a_saved_answer_is_a_valid_synthesis_page_with_citations_from_the_body(
    brain: Path,
) -> None:
    path = save_answer(
        brain,
        "How do herons hunt?",
        ANSWER,
        ["coastal-birds", "tide-pools"],
        "How grey herons find\nfood.",
        ["birds", " birds ", "hunting", ""],
        "test-model",
    )

    assert path == PurePosixPath("wiki/syntheses/how-do-herons-hunt.md")
    page = read_page(brain, path)
    assert validate_page(page, all_paths(brain)) == []
    assert page.frontmatter is not None
    assert page.frontmatter.type == "synthesis"
    assert page.frontmatter.title == "How do herons hunt?"
    assert page.frontmatter.description == "How grey herons find food."
    assert page.frontmatter.tags == ["birds", "hunting"]
    assert page.frontmatter.sources == [
        SourceRef(
            id="coastal-birds",
            resource="/sources/nature/coastal-birds/coastal-birds.md",
            title="Coastal Birds",
        ),
        SourceRef(
            id="tide-pools",
            resource="/sources/nature/tide-pools/tide-pools.md",
            title="Tide Pools of the Example Coast",
        ),
    ]
    assert page.frontmatter.generated is not None
    assert page.frontmatter.generated.by == "sbb/test-model"
    assert page.frontmatter.generated.at.utcoffset() is not None
    assert "example.com" not in page.body
    assert page.body.count("[^coastal-birds]:") == page.body.count("[^tide-pools]:") == 1
    assert page.body.endswith(
        "[^coastal-birds]: [Coastal Birds](/sources/nature/coastal-birds/coastal-birds.md)\n"
        "[^tide-pools]: [Tide Pools of the Example Coast]"
        "(/sources/nature/tide-pools/tide-pools.md)\n"
    )
    assert [issue for issue in validate_bundle(brain) if issue.severity == "error"] == []


def test_a_second_answer_with_the_same_title_gets_a_new_page(brain: Path) -> None:
    first = save_answer(brain, "How do herons hunt?", ANSWER, [], "", [], "test-model")
    first_text = (brain / first).read_bytes()

    second = save_answer(brain, "How do herons hunt?", ANSWER, [], "", [], None)

    assert second == PurePosixPath("wiki/syntheses/how-do-herons-hunt-2.md")
    assert (brain / first).read_bytes() == first_text
    frontmatter = read_page(brain, second).frontmatter
    assert frontmatter is not None
    assert frontmatter.generated is not None
    assert frontmatter.generated.by == UNKNOWN_AGENT


@pytest.mark.parametrize(
    ("body", "source_ids", "problem"),
    [
        ("Herons wade.[^no-such-source]", [], "no-such-source"),
        ("Herons wade.[^coastal-birds]", ["tide-pools"], "tide-pools"),
        ("Herons wade.", [], "cites no Source"),
        ("---\ntype: entity\n---\nHerons wade.[^coastal-birds]", [], "---"),
        ("Herons wade.[^coastal-birds]\n\n```text\nlow tide 06:40\n", [], "code block"),
    ],
)
def test_an_answer_with_wrong_citations_is_rejected_and_nothing_is_written(
    brain: Path, body: str, source_ids: list[str], problem: str
) -> None:
    with pytest.raises(InvalidAnswerError, match=problem):
        save_answer(brain, "Herons", body, source_ids, "", [], "test-model")

    assert not (brain / "wiki" / "syntheses").exists()
