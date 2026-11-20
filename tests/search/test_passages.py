"""The passage index of Canonical Documents: build, update, removal, and find_passages."""

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.okf import read_page
from second_brain_builder.search import PASSAGE_CHARS, SearchIndex, segment_markdown

from .conftest import MakePage

BRAIN = "brain-a"
CANONICAL = "sources/nature/tide-pools/tide-pools.md"
SHORE_TEXT = """# Tide Pools of the Example Coast

A field guide to the rock pools that the falling tide leaves behind.

## Sea Stars

The ochre sea star lives on the lower rocks. It eats mussels and can grow a new arm.

## Visitors

### Herons

Grey herons wade into the pools at low tide to hunt small fish.

### Gulls

Gulls drop mussels on the rocks to break their shells.
"""


@pytest.fixture
def shore(tmp_path: Path, make_page: MakePage) -> Path:
    """A Brain with one Canonical Document and one Wiki Page with the same words."""
    root = tmp_path / "shore"
    make_page(root, CANONICAL, "source", title="Tide Pools of the Example Coast", body=SHORE_TEXT)
    make_page(
        root,
        "wiki/entities/grey-heron.md",
        "entity",
        title="Grey Heron",
        body="Grey herons wade into the pools at low tide to hunt small fish.\n",
    )
    return root


def find(
    index: SearchIndex, *words: str, heading_words: Sequence[str] = ()
) -> list[tuple[tuple[str, ...], str]]:
    """The heading trail and text of each passage of the Canonical Document that matches."""
    hits = index.find_passages(
        BRAIN, PurePosixPath(CANONICAL), list(words), heading_words=heading_words
    )
    return [(hit.heading_trail, hit.text) for hit in hits]


def test_rebuild_indexes_one_passage_per_segment_of_each_canonical_document(
    index: SearchIndex, shore: Path
) -> None:
    index.rebuild(BRAIN, shore)

    hits = index.find_passages(BRAIN, PurePosixPath(CANONICAL), ["herons", "fish"])

    assert len(hits) == 1
    hit = hits[0]
    body = read_page(shore, PurePosixPath(CANONICAL)).body
    segments = segment_markdown(body, PASSAGE_CHARS).segments
    assert (hit.segment, hit.char_start, hit.char_end) == (
        segments[hit.segment].index,
        segments[hit.segment].char_start,
        segments[hit.segment].char_end,
    )
    assert body[hit.char_start : hit.char_end].startswith("### Herons\n")
    assert hit.heading_trail == ("Tide Pools of the Example Coast", "Visitors", "Herons")
    assert hit.text == "Herons\nGrey herons wade into the pools at low tide to hunt small fish."
    assert hit.title == "Tide Pools of the Example Coast"
    assert 0 < hit.score < 1


def test_wiki_pages_have_no_passages(index: SearchIndex, shore: Path) -> None:
    index.rebuild(BRAIN, shore)

    assert (
        index.find_passages(BRAIN, PurePosixPath("wiki/entities/grey-heron.md"), ["herons"]) == []
    )


def test_the_passage_with_the_best_matches_comes_first(index: SearchIndex, shore: Path) -> None:
    index.rebuild(BRAIN, shore)

    trails = [trail for trail, _ in find(index, "mussels", "rocks", "shells", "break")]

    assert trails == [
        ("Tide Pools of the Example Coast", "Visitors", "Gulls"),
        ("Tide Pools of the Example Coast", "Sea Stars"),
    ]


def test_heading_words_keep_only_passages_under_a_matching_trail(
    index: SearchIndex, shore: Path
) -> None:
    index.rebuild(BRAIN, shore)

    under_visitors = find(index, "mussels", "rocks", heading_words=["visitors"])
    every_word = find(index, "mussels", heading_words=["visitors", "gulls"])
    in_body_order = find(index, heading_words=["Visitors"])

    assert [trail[-1] for trail, _ in under_visitors] == ["Gulls"]
    assert [trail[-1] for trail, _ in every_word] == ["Gulls"]
    assert [trail[-1] for trail, _ in in_body_order] == ["Herons", "Gulls"]
    assert find(index, heading_words=["egrets"]) == []
    assert find(index) == []


def test_words_are_plain_text_never_fts5_syntax(index: SearchIndex, shore: Path) -> None:
    index.rebuild(BRAIN, shore)

    # As FTS5 syntax, each word would match a passage: an OR, a NEAR group, a column
    # filter, a prefix. As one plain string, no passage has these words in a row.
    for word in ('herons" OR "gulls', 'visitors" OR "gulls', "NEAR(herons wade)", "text:herons"):
        assert find(index, word) == [], word
        assert find(index, heading_words=[word]) == [], word
    assert find(index, "heron*") == find(index, heading_words=["heron*"]) == []
    for word in ("*", "(", '"', "^"):
        assert find(index, word, heading_words=[word]) == []  # no FTS5 syntax error

    assert find(index, "HÉRONS") == find(index, "herons") != []


def test_update_replaces_the_passages_of_a_changed_document(
    index: SearchIndex, shore: Path, make_page: MakePage
) -> None:
    index.rebuild(BRAIN, shore)
    make_page(
        shore,
        CANONICAL,
        "source",
        title="Tide Pools of the Example Coast",
        body="# Tide Pools\n\n## Crabs\n\nShore crabs hide under the weed.\n",
    )

    index.update(BRAIN, shore, [PurePosixPath(CANONICAL)])

    assert find(index, "herons") == []
    assert find(index, "crabs") == [
        (("Tide Pools", "Crabs"), "Crabs\nShore crabs hide under the weed.")
    ]


def test_a_deleted_or_retyped_document_loses_its_passages(
    index: SearchIndex, shore: Path, make_page: MakePage
) -> None:
    index.rebuild(BRAIN, shore)
    make_page(shore, CANONICAL, "concept", title="Now a concept", body=SHORE_TEXT)
    index.update(BRAIN, shore, [PurePosixPath(CANONICAL)])
    assert find(index, "herons") == []

    make_page(shore, CANONICAL, "source", title="Tide Pools", body=SHORE_TEXT)
    index.update(BRAIN, shore, [PurePosixPath(CANONICAL)])
    assert len(find(index, "herons")) == 1

    (shore / CANONICAL).unlink()
    index.update(BRAIN, shore, [PurePosixPath(CANONICAL)])
    assert find(index, "herons") == []


def test_passages_belong_to_one_brain(index: SearchIndex, shore: Path) -> None:
    index.rebuild(BRAIN, shore)
    index.rebuild("brain-b", shore)

    index.remove_brain("brain-b")

    assert len(find(index, "herons")) == 1
    assert index.find_passages("brain-b", PurePosixPath(CANONICAL), ["herons"]) == []
    assert index.find_passages("never-indexed", PurePosixPath(CANONICAL), ["herons"]) == []


def test_a_large_document_has_passages_of_the_passage_size(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "big"
    chapters = [
        f"## Chapter {number}\n\n"
        + "".join(f"Verse {verse} tells of tide number {number}.\n\n" for verse in range(60))
        for number in range(1, 41)
    ]
    body = "# Book of Tides\n\n" + "".join(chapters)
    make_page(root, CANONICAL, "source", title="Book of Tides", body=body)

    index.rebuild(BRAIN, root)

    hits = index.find_passages(BRAIN, PurePosixPath(CANONICAL), [], heading_words=["37"], limit=50)
    assert len(hits) > 1
    assert all(hit.heading_trail == ("Book of Tides", "Chapter 37") for hit in hits)
    assert all(hit.char_end - hit.char_start <= PASSAGE_CHARS for hit in hits)
    assert "".join(body[hit.char_start : hit.char_end] for hit in hits) == chapters[36]


@pytest.mark.parametrize("path", ["/sources/x.md", "", "../x.md"])
def test_find_passages_rejects_paths_outside_the_brain(index: SearchIndex, path: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        index.find_passages(BRAIN, PurePosixPath(path), ["x"])


def test_find_passages_rejects_a_limit_below_one(index: SearchIndex) -> None:
    with pytest.raises(ValueError, match="limit"):
        index.find_passages(BRAIN, PurePosixPath(CANONICAL), ["x"], limit=0)
