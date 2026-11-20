"""segment_markdown: the segments that the document viewer and the passage index share."""

import time
from itertools import pairwise

import pytest

from second_brain_builder.search import Segmentation, segment_markdown

GUIDE = """# Tide Pools

A field guide to the rock pools.

## Sea Stars

The ochre sea star lives on the lower rocks.

### Arms

It can grow a new arm.

## Hermit Crabs

Hermit crabs move into larger empty shells.
"""


def texts(body: str, segmentation: Segmentation) -> list[str]:
    return [body[segment.char_start : segment.char_end] for segment in segmentation.segments]


def assert_covers(body: str, segmentation: Segmentation) -> None:
    """The segments are in order, contiguous, and give the body again."""
    segments = segmentation.segments
    assert [segment.index for segment in segments] == list(range(len(segments)))
    assert "".join(texts(body, segmentation)) == body
    for before, after in pairwise(segments):
        assert before.char_end == after.char_start


def test_each_heading_starts_a_segment_under_its_heading_trail() -> None:
    result = segment_markdown(GUIDE)

    assert_covers(GUIDE, result)
    first_lines = [text.splitlines()[0] for text in texts(GUIDE, result)]
    assert first_lines == ["# Tide Pools", "## Sea Stars", "### Arms", "## Hermit Crabs"]
    assert [segment.heading_trail for segment in result.segments] == [
        ("Tide Pools",),
        ("Tide Pools", "Sea Stars"),
        ("Tide Pools", "Sea Stars", "Arms"),
        ("Tide Pools", "Hermit Crabs"),
    ]
    assert [(h.level, h.text, h.segment) for h in result.headings] == [
        (1, "Tide Pools", 0),
        (2, "Sea Stars", 1),
        (3, "Arms", 2),
        (2, "Hermit Crabs", 3),
    ]
    for heading in result.headings:
        assert result.segments[heading.segment].char_start == heading.char_start
        assert GUIDE[heading.char_start :].lstrip("#").startswith(" " + heading.text)


def test_text_before_the_first_heading_has_an_empty_trail() -> None:
    body = "\n\nA preface without a heading.\n\n# One\n\nText.\n"

    result = segment_markdown(body)

    assert texts(body, result) == ["\n\nA preface without a heading.\n\n", "# One\n\nText.\n"]
    assert [segment.heading_trail for segment in result.segments] == [(), ("One",)]


def test_headings_are_plain_text_and_setext_headings_count() -> None:
    body = (
        "## The *ochre* [sea star](/wiki/x.md) `arm` &amp; \\*more\\*[^src] ##\n\n"
        "Rock Pools\nof the Coast\n===\n\nText.\n\nShells\n---\n\nMore text.\n\n"
        "- a list item\n---\n"
    )

    result = segment_markdown(body)

    assert_covers(body, result)
    assert [(h.level, h.text) for h in result.headings] == [
        (2, "The ochre sea star arm & *more*"),
        (1, "Rock Pools of the Coast"),
        (2, "Shells"),
    ]
    assert result.segments[-1].heading_trail == ("Rock Pools of the Coast", "Shells")


@pytest.mark.parametrize(
    ("line", "text"),
    [
        ("## Tides ##", "Tides"),
        ("## Tides \t###   ", "Tides"),
        ("## C#", "C#"),
        ("## Tides \\##", "Tides ##"),
        ("## Tides # of the coast", "Tides # of the coast"),
    ],
)
def test_an_atx_closing_sequence_goes_only_after_a_space(line: str, text: str) -> None:
    result = segment_markdown(line + "\n\nText.\n")

    assert [heading.text for heading in result.headings] == [text]


def test_paragraphs_are_grouped_up_to_the_limit() -> None:
    paragraphs = [f"Paragraph {number} " + "x" * 90 + "\n\n" for number in range(10)]
    body = "# Notes\n\n" + "".join(paragraphs)

    result = segment_markdown(body, max_chars=350)

    assert_covers(body, result)
    assert all(segment.char_end - segment.char_start <= 350 for segment in result.segments)
    # Each cut is at the start of a paragraph: the heading and 3 paragraphs fit in one.
    assert [text.split(" ", 2)[:2] for text in texts(body, result)[1:]] == [
        ["Paragraph", "3"],
        ["Paragraph", "6"],
        ["Paragraph", "9"],
    ]
    assert {segment.heading_trail for segment in result.segments} == {("Notes",)}


def test_a_fenced_code_block_is_never_cut() -> None:
    fence = "```python\n# not a heading\n\n" + "value = 1\n\n" * 40 + "```\n"
    body = "# Code\n\nBefore.\n\n" + fence + "\nAfter.\n\n~~~~\n~~~\n# still code\n~~~~\n"

    result = segment_markdown(body, max_chars=100)

    assert_covers(body, result)
    assert [h.text for h in result.headings] == ["Code"]
    assert texts(body, result) == [
        "# Code\n\nBefore.\n\n",
        fence + "\n",
        "After.\n\n~~~~\n~~~\n# still code\n~~~~\n",
    ]


def test_a_fence_that_never_closes_does_not_stop_the_cuts() -> None:
    body = "# Notes\n\n```\n" + "".join(f"Line {n} of notes.\n\n" for n in range(50))

    result = segment_markdown(body, max_chars=100)

    assert_covers(body, result)
    assert all(segment.char_end - segment.char_start <= 100 for segment in result.segments)


@pytest.mark.parametrize("indent", ["    ", "        ", "\t"])
@pytest.mark.parametrize("marker", ["```", "~~~~"])
def test_a_fence_indented_inside_a_list_item_is_never_cut(indent: str, marker: str) -> None:
    fence = indent + marker + "python\n" + (indent + "value = 1\n\n") * 1000
    fence += indent + marker + "\n"
    prefix = "# Code\n\n1. Example:\n\n"
    if indent == "        ":
        prefix += "    1. Nested:\n\n"
    body = prefix + fence + "\nAfter.\n"

    result = segment_markdown(body, max_chars=100)

    assert_covers(body, result)
    assert result == segment_markdown(body, max_chars=100)
    assert any(fence in text for text in texts(body, result))


def test_a_long_paragraph_is_cut_at_line_starts_and_a_long_line_at_spaces() -> None:
    lines = "".join(f"line {n:02d} of a long paragraph\n" for n in range(20))
    words = "word " * 100
    body = lines + "\n" + words + "\n\n" + "y" * 250 + "\n"

    result = segment_markdown(body, max_chars=100)

    assert_covers(body, result)
    assert all(segment.char_end - segment.char_start <= 100 for segment in result.segments)
    cuts = [segment.char_start for segment in result.segments[1:]]
    words_start = body.index("word")
    ys_start = body.index("y")
    assert [cut for cut in cuts if cut < len(lines)] == [84, 168, 252, 336, 420, 504]
    assert all(body[cut - 1] == "\n" for cut in cuts if cut < len(lines))
    assert all(body[cut - 1] == " " for cut in cuts if words_start < cut < words_start + 500)
    assert [cut for cut in cuts if cut > ys_start] == [ys_start + 98, ys_start + 198]


@pytest.mark.parametrize(
    "body",
    [
        GUIDE,
        "Only one line without a newline",
        "\n\n\n",
        "# Heading only",
        "Ação em São Paulo 🌊 é ótima.\n\n## Seção\n\nTexto com emoji 🐚 e acentos.\n",
        "Windows\r\nline endings\r\n\r\n# Title\r\n\r\nText\r\n",
        "> quote\n> ```\n\n> still quote\n\n1. item\n\n   ```\n   code\n\n   ```\n",
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n<div>\n\n</div>\n\n---\n\n***\n",
    ],
)
@pytest.mark.parametrize("max_chars", [1, 7, 40, 1500, 12000])
def test_the_segments_give_the_body_back(body: str, max_chars: int) -> None:
    assert_covers(body, segment_markdown(body, max_chars=max_chars))


def test_an_empty_body_has_no_segments_and_a_blank_body_has_one() -> None:
    assert segment_markdown("") == Segmentation(segments=(), headings=())
    assert texts("\n \n", segment_markdown("\n \n")) == ["\n \n"]


def test_the_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_chars"):
        segment_markdown(GUIDE, max_chars=0)


def test_fences_that_never_close_take_linear_time() -> None:
    # Each "```a" line opens a fence that no line closes (a closing fence has no
    # info string). A search for each fence's closing line would take quadratic time.
    body = "```a\n" * 100_000 + "~~~~~~\n" + "~~~\n" * 50_000 + "Text.\n"

    start = time.perf_counter()
    result = segment_markdown(body)
    elapsed = time.perf_counter() - start

    assert elapsed < 3.0, f"segment_markdown took {elapsed:.1f} s"
    assert_covers(body, result)


@pytest.mark.parametrize("run", [" ", "\t"])
def test_a_long_whitespace_run_in_a_heading_takes_linear_time(run: str) -> None:
    # A pattern that looks for the closing "#" sequence from each space of the run
    # takes quadratic time: about 9 minutes for this heading.
    body = "# Tides" + run * 400_000 + "of the coast ##\n\nText.\n"

    start = time.perf_counter()
    result = segment_markdown(body)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"segment_markdown took {elapsed:.1f} s"
    assert [heading.text for heading in result.headings] == ["Tides of the coast"]
