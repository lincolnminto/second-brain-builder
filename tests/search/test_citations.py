"""The claim behind a footnote reference: sentence, locator, and key terms."""

import time

import pytest

from second_brain_builder.search import (
    Locator,
    cited_sentence,
    claim_terms,
    key_terms,
    parse_locator,
)
from second_brain_builder.search.text import markdown_to_text

PAGE = """# Grey Heron

The grey heron waits for fish (Shore Book 11:1-9).[^shore] It wades into pools at low
tide.[^tides] The heron[^birds] nests in tall trees (see chapter 3) near the coast.

A later paragraph (cf. Gen. 2:4) cites twice.[^shore][^tides] An unknown label[^none] is text.

[^shore]: [Shore Book](/sources/books/shore-book/shore-book.md), and see[^birds].
[^tides]: [Tides](/sources/nature/tides/tides.md)
[^birds]: Coastal Birds, without a link.
"""


@pytest.mark.parametrize(
    ("ref", "label", "sentence", "locator"),
    [
        (0, "shore", "The grey heron waits for fish (Shore Book 11:1-9).", "Shore Book 11:1-9"),
        (1, "tides", "It wades into pools at low tide.", None),
        (
            2,
            "birds",
            "The heron nests in tall trees (see chapter 3) near the coast.",
            "see chapter 3",
        ),
        (3, "shore", "A later paragraph (cf. Gen. 2:4) cites twice.", "cf. Gen. 2:4"),
        (4, "tides", "A later paragraph (cf. Gen. 2:4) cites twice.", "cf. Gen. 2:4"),
        # The references in footnote definitions come after those of the body.
        (5, "birds", "Shore Book, and see.", None),
    ],
)
def test_cited_sentence_takes_the_sentence_that_holds_the_reference(
    ref: int, label: str, sentence: str, locator: str | None
) -> None:
    cited = cited_sentence(PAGE, ref)

    assert cited is not None
    assert (cited.label, cited.sentence, cited.locator) == (label, sentence, locator)


def test_the_claim_leaves_out_the_locator() -> None:
    cited = cited_sentence(PAGE, 2)

    assert cited is not None
    assert cited.claim == "The heron nests in tall trees near the coast."


def test_cited_sentence_gives_the_first_link_of_the_definition() -> None:
    hrefs = [cited.definition_href for ref in (0, 1, 2) if (cited := cited_sentence(PAGE, ref))]

    assert hrefs == [
        "/sources/books/shore-book/shore-book.md",
        "/sources/nature/tides/tides.md",
        None,
    ]


@pytest.mark.parametrize("ref", [6, 100, -1])
def test_a_reference_number_past_the_last_reference_gives_none(ref: int) -> None:
    assert cited_sentence(PAGE, ref) is None


def test_references_in_code_are_not_references() -> None:
    body = "Text `[^a]` and\n\n    [^a] in code\n\nreal.[^a]\n\n[^a]: [A](/sources/a/a.md)\n"

    cited = cited_sentence(body, 0)

    assert cited is not None
    assert cited.sentence == "real."
    assert cited_sentence(body, 1) is None


def test_labels_match_without_case_as_in_gfm() -> None:
    # The web (remark-gfm) shows both references; a match with case would see one.
    body = (
        "Herons fish at dawn.[^Shore] They nest in trees.[^birds]\n\n"
        "[^shore]: [Shore](/sources/shore/shore.md)\n[^BIRDS]: [Birds](/sources/birds/birds.md)\n"
    )

    first, second = cited_sentence(body, 0), cited_sentence(body, 1)

    assert first is not None and second is not None
    assert (first.label, first.sentence, first.definition_href) == (
        "Shore",
        "Herons fish at dawn.",
        "/sources/shore/shore.md",
    )
    assert (second.label, second.sentence, second.definition_href) == (
        "birds",
        "They nest in trees.",
        "/sources/birds/birds.md",
    )


def test_caret_brackets_are_text_not_inline_footnotes() -> None:
    # GFM and the okf module have no inline footnotes, so "^[a-z]" is text and
    # does not count as a reference.
    body = (
        "Logs match ^[a-z]+ lines. Herons hunt fish.[^shore] Gulls follow.[^birds]\n\n"
        "[^shore]: [Shore](/sources/shore/shore.md)\n[^birds]: [Birds](/sources/birds/birds.md)\n"
    )

    cited = [cited_sentence(body, ref) for ref in range(3)]

    assert [(c.label, c.sentence) for c in cited if c is not None] == [
        ("shore", "Herons hunt fish."),
        ("birds", "Gulls follow."),
    ]
    assert cited[2] is None


@pytest.mark.parametrize(("first", "second"), [("a", "a"), ("a", "A"), ("A", "a"), ("ß", "SS")])
def test_the_first_definition_of_a_normalized_footnote_label_wins(first: str, second: str) -> None:
    body = (
        f"Herons hunt.[^{second}]\n\n"
        f"[^{first}]: [First](/sources/first/first.md)\n\n"
        "    The first habitat.\n\n"
        f"[^{second}]: [Second](/sources/second/second.md)\n\n"
        "    The second habitat.\n"
    )

    cited = cited_sentence(body, 0)

    assert cited is not None
    assert cited.definition_href == "/sources/first/first.md"
    text = markdown_to_text(body)
    assert "First" in text and "The first habitat." in text
    assert "Second" not in text and "The second habitat." not in text


@pytest.mark.parametrize(
    "body",
    [
        # A sentence end that scans the rest of the run from each dot.
        "Claim " + "." * 200_000 + "x[^a].\n\n[^a]: [A](/sources/a/a.md)\n",
        # A footnote reference that scans to the next "]" from each "[^".
        "Claim " + "[^" * 30_000 + " x.[^a]\n\n[^a]: [A](/sources/a/a.md)\n",
    ],
)
def test_long_runs_in_the_cited_paragraph_take_linear_time(body: str) -> None:
    start = time.perf_counter()
    cited = cited_sentence(body, 0)
    elapsed = time.perf_counter() - start

    assert elapsed < 3.0, f"cited_sentence took {elapsed:.1f} s"
    assert cited is not None
    assert cited.label == "a"


@pytest.mark.parametrize(
    ("text", "locator"),
    [
        ("Gênesis 11:1-9", Locator(names=("genesis",), keywords=(), numbers=("11",))),
        ("chapter 3", Locator(names=(), keywords=("chapter",), numbers=("3",))),
        (
            "see Capítulo 12, versículos 3\u20135",
            Locator((), ("capitulo", "versiculos"), ("12", "3")),
        ),
        ("2 Samuel 7:12", Locator(names=("samuel",), keywords=(), numbers=("2", "7"))),
        # "book" is a division keyword; a page locator keeps only its names.
        ("Shore Book, p. 42", Locator(names=("shore",), keywords=(), numbers=())),
        ("1990", None),
        ("p. 42", None),
        ("a large bird", None),
    ],
)
def test_parse_locator(text: str, locator: Locator | None) -> None:
    assert parse_locator(text) == locator


def test_heading_word_sets_go_from_most_to_least_specific() -> None:
    locator = Locator(names=("genesis",), keywords=("chapter",), numbers=("11",))

    assert locator.heading_word_sets() == [
        ("genesis", "chapter", "11"),
        ("genesis", "11"),
        ("genesis",),
    ]
    assert Locator(names=(), keywords=("chapter",), numbers=("3",)).heading_word_sets() == [
        ("chapter", "3"),
        ("3",),
    ]


def test_key_terms_leave_out_common_words_and_numbers() -> None:
    assert key_terms("The grey heron waits for 12 fish, and the HERON eats them.") == [
        "grey",
        "heron",
        "waits",
        "fish",
        "eats",
    ]
    assert key_terms("Ela não foi à praia com as gaivotas.") == ["praia", "gaivotas"]
    assert key_terms("It is a ox.") == ["it", "is", "ox"]


def test_claim_terms_put_the_subject_first_and_leave_out_the_source_name() -> None:
    terms = claim_terms(
        "According to the Book of Tides, it is the father of Amram and Heron.",
        subject="Grey Heron",
        source_title="Book of Tides",
    )
    assert terms == ["grey", "heron", "father", "amram"]
    assert claim_terms("Moises spoke.", subject="Moisés") == ["moisés", "spoke"]


def test_key_terms_keep_the_longest_words_in_order() -> None:
    assert key_terms("aaa bbbbbb cc dddd eeeeeee fff", limit=3) == ["bbbbbb", "dddd", "eeeeeee"]
