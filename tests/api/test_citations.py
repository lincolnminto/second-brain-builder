"""GET /citations/resolve: the passage that a footnote reference of a Wiki Page cites."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from second_brain_builder.brains import GitIdentity, GitRepo
from second_brain_builder.okf import Frontmatter, Page, SourceRef, read_page, write_page
from second_brain_builder.services import AppContext

if TYPE_CHECKING:
    from .conftest import BrainFixture

BOOK = "sources/books/book-of-tides/book-of-tides.md"
BOOK_TEXT = """# Book of Tides

## Chapter 1

1 The sea was **calm** at <b>dawn</b>, see [the map](https://example.com/map).

2 Fishers mended their nets on the sand.

## Chapter 2

1 The spring tide flooded the salt marsh, and the salt marsh birds fled the spring tide.

2 Herons waited on the dunes.

## Chapter 3

1 A storm came from the north.

2 The spring tide flooded the salt marsh at night.
"""
TIDES = "wiki/concepts/tides.md"
TIDES_TEXT = """# Tides

The spring tide flooded the salt marsh (Book of Tides 3:2).[^book-of-tides]
The spring tide flooded the salt marsh.[^book-of-tides]
The spring tide flooded the salt marsh at night (chapter 3).[^book-of-tides]
A maré de primavera inundou o pântano (Book of Tides 3:2).[^book-of-tides]
The sea was calm at dawn.[^book-of-tides]
Volcanoes erupt under glaciers.[^book-of-tides]

[^book-of-tides]: [Book of Tides](/sources/books/book-of-tides/book-of-tides.md)
"""
OUTSIDE_PAGE = "---\ntype: entity\ntitle: Outside\n---\n\nText outside the Brain.[^x]\n\n[^x]: X\n"


@pytest.fixture
def tides(ctx: AppContext, brain: BrainFixture) -> str:
    """A Canonical Document with chapters, and a Wiki Page that cites it: the body of the book."""
    write_book(brain, BOOK_TEXT)
    reference = SourceRef(id="book-of-tides", resource=f"/{BOOK}", title="Book of Tides")
    write_page(
        brain.root,
        Page(
            path=PurePosixPath(TIDES),
            frontmatter=Frontmatter(type="concept", title="Tides", sources=[reference]),
            body=TIDES_TEXT,
        ),
    )
    ctx.search.update(brain.id, brain.root, [PurePosixPath(BOOK), PurePosixPath(TIDES)])
    return read_page(brain.root, PurePosixPath(BOOK)).body


def write_book(brain: BrainFixture, body: str) -> None:
    write_page(
        brain.root,
        Page(
            path=PurePosixPath(BOOK),
            frontmatter=Frontmatter(type="source", title="Book of Tides"),
            body=body,
        ),
    )


def write_tides_page(brain: BrainFixture, claims: str) -> None:
    """The Wiki Page TIDES with new claims, which cite the Book of Tides."""
    reference = SourceRef(id="book-of-tides", resource=f"/{BOOK}", title="Book of Tides")
    write_page(
        brain.root,
        Page(
            path=PurePosixPath(TIDES),
            frontmatter=Frontmatter(type="concept", title="Tides", sources=[reference]),
            body=f"# Tides\n\n{claims}\n[^book-of-tides]: [Book of Tides](/{BOOK})\n",
        ),
    )


def resolve(client: TestClient, brain: BrainFixture, page: str, ref: int | str) -> Any:
    return client.get(
        f"/api/brains/{brain.id}/citations/resolve", params={"page": page, "ref": ref}
    )


def resolved(client: TestClient, brain: BrainFixture, page: str, ref: int) -> dict[str, Any]:
    response = resolve(client, brain, page, ref)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_a_reference_resolves_to_the_passage_with_its_key_terms(
    client: TestClient, brain: BrainFixture
) -> None:
    canonical = "sources/nature/tide-pools/tide-pools.md"
    body = read_page(brain.root, PurePosixPath(canonical)).body

    # "At low tide it wades into rock pools to hunt small fish.[^tide-pools]"
    passage = resolved(client, brain, "wiki/entities/grey-heron.md", 1)

    assert passage["source_id"] == "tide-pools"
    assert passage["source_path"] == canonical
    assert passage["source_title"] == "Tide Pools of the Example Coast"
    assert passage["heading_trail"] == ["Tide Pools of the Example Coast", "Visitors"]
    assert "Grey herons wade into the pools at low tide" in passage["text"]
    assert body[passage["char_start"] : passage["char_end"]].startswith("## Visitors\n")
    assert passage["method"] == "terms"
    assert 0 < passage["score"] < 1


def test_each_reference_resolves_in_its_own_source(client: TestClient, brain: BrainFixture) -> None:
    heron = resolved(client, brain, "/wiki/entities/grey-heron.md", 0)
    star = resolved(client, brain, "wiki/entities/sea-star.md", 0)

    assert (heron["source_id"], heron["heading_trail"][-1]) == ("coastal-birds", "Grey Heron")
    assert (star["source_id"], star["heading_trail"][-1]) == ("tide-pools", "Sea Stars")


def test_a_locator_prefers_the_passage_under_its_heading(
    client: TestClient, brain: BrainFixture, tides: str
) -> None:
    with_locator = resolved(client, brain, TIDES, 0)
    without_locator = resolved(client, brain, TIDES, 1)
    chapter_locator = resolved(client, brain, TIDES, 2)

    assert with_locator["heading_trail"] == ["Book of Tides", "Chapter 3"]
    assert with_locator["method"] == "locator_terms"
    assert 1 <= with_locator["score"] < 2
    # Chapter 2 repeats the words, so it is the best passage without the locator.
    assert without_locator["heading_trail"] == ["Book of Tides", "Chapter 2"]
    assert without_locator["method"] == "terms"
    assert chapter_locator["heading_trail"] == ["Book of Tides", "Chapter 3"]
    assert chapter_locator["method"] == "locator_terms"
    assert (
        "flooded the salt marsh at night"
        in tides[with_locator["char_start"] : with_locator["char_end"]]
    )


def write_heron_page(ctx: AppContext, brain: BrainFixture, book: str, claim: str) -> str:
    """The Book of Tides with ``book`` as its body, and an entity page Grey Heron citing it."""
    write_book(brain, book)
    page = "wiki/entities/tidal-heron.md"
    reference = SourceRef(id="book-of-tides", resource=f"/{BOOK}", title="Book of Tides")
    write_page(
        brain.root,
        Page(
            path=PurePosixPath(page),
            frontmatter=Frontmatter(type="entity", title="Grey Heron", sources=[reference]),
            body=f"# Grey Heron\n\n{claim}[^book-of-tides]\n\n[^book-of-tides]: Book of Tides\n",
        ),
    )
    ctx.search.update(brain.id, brain.root, [PurePosixPath(BOOK), PurePosixPath(page)])
    return page


def test_a_sentence_without_its_subject_resolves_to_the_passage_about_the_page(
    ctx: AppContext, client: TestClient, brain: BrainFixture
) -> None:
    # Both sections hold every word of the sentence, and BM25 prefers the shorter one.
    book = (
        "# Book of Tides\n\n## Bitterns\n\nThe bittern nests in reed beds.\n\n"
        "## Herons\n\nThe grey heron nests in reed beds, and the grey heron fishes there.\n"
    )
    page = write_heron_page(ctx, brain, book, "It nests in reed beds.")

    assert resolved(client, brain, page, 0)["heading_trail"] == ["Book of Tides", "Herons"]


def test_a_passage_with_many_more_claim_terms_beats_a_bare_heading_that_matches_the_locator(
    ctx: AppContext, client: TestClient, brain: BrainFixture
) -> None:
    book = (
        "# Book of Tides\n\n## Tide table\n\n### Chapter 2\n\n1 2 3 4 5 6 7 8 9 10\n\n"
        "## Shore birds\n\nThe grey heron waits by the rock pools at low tide and spears "
        "small fish.\n"
    )
    claim = "It waits by the rock pools at low tide and spears small fish (chapter 2)."
    page = write_heron_page(ctx, brain, book, claim)

    passage = resolved(client, brain, page, 0)

    assert passage["heading_trail"] == ["Book of Tides", "Shore birds"]
    assert passage["method"] == "terms"


def test_a_locator_alone_finds_its_section_when_no_term_matches(
    client: TestClient, brain: BrainFixture, tides: str
) -> None:
    passage = resolved(client, brain, TIDES, 3)

    assert passage["heading_trail"] == ["Book of Tides", "Chapter 3"]
    assert passage["method"] == "locator"
    assert tides[passage["char_start"] :].startswith("## Chapter 3\n")


def test_the_passage_is_plain_text(client: TestClient, brain: BrainFixture, tides: str) -> None:
    passage = resolved(client, brain, TIDES, 4)

    assert passage["heading_trail"] == ["Book of Tides", "Chapter 1"]
    assert "The sea was calm at dawn, see the map." in passage["text"]
    for markup in ("**", "<b>", "example.com", "]("):
        assert markup not in passage["text"]


@pytest.mark.parametrize(
    ("page", "ref"),
    [
        (TIDES, 5),  # no passage has a key term, and there is no locator
        (TIDES, 6),  # the page has 6 references
        ("index.md", 0),  # a page without references
        ("wiki/concepts/missing.md", 0),
    ],
)
def test_nothing_to_resolve_gives_404(
    client: TestClient, brain: BrainFixture, tides: str, page: str, ref: int
) -> None:
    response = resolve(client, brain, page, ref)

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_a_source_without_an_indexed_canonical_document_gives_404(
    client: TestClient, ctx: AppContext, brain: BrainFixture
) -> None:
    ctx.search.remove_brain(brain.id)

    response = resolve(client, brain, "wiki/entities/grey-heron.md", 1)

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("page", "ref"),
    [
        ("../outside.md", 0),
        ("wiki/../../outside.md", 0),
        (".git/config", 0),
        ("wiki/entities/grey-heron.md\x00", 0),
        ("sources/nature/tide-pools/tide-pools.txt", 0),
        ("wiki/entities/grey-heron.md", -1),
        ("wiki/entities/grey-heron.md", "first"),
    ],
)
def test_bad_paths_and_reference_numbers_are_rejected(
    client: TestClient, brain: BrainFixture, page: str, ref: int | str
) -> None:
    (brain.root.parent / "outside.md").write_text(OUTSIDE_PAGE)

    response = resolve(client, brain, page, ref)

    assert response.status_code == 422
    assert "outside the Brain" not in response.text


def test_a_symbolic_link_out_of_the_brain_is_rejected(
    client: TestClient, brain: BrainFixture
) -> None:
    outside = brain.root.parent / "outside.md"
    outside.write_text(OUTSIDE_PAGE)
    (brain.root / "wiki" / "entities" / "leak.md").symlink_to(outside)

    response = resolve(client, brain, "wiki/entities/leak.md", 0)

    assert response.status_code == 422


def test_an_unknown_brain_gives_404(client: TestClient) -> None:
    response = client.get(
        "/api/brains/no-such-brain/citations/resolve", params={"page": "wiki/x.md", "ref": 0}
    )

    assert response.status_code == 404


def test_a_label_in_another_case_and_caret_brackets_resolve_as_the_web_shows_them(
    client: TestClient, brain: BrainFixture, tides: str
) -> None:
    # remark-gfm matches labels without case and has no inline footnotes ("^[").
    write_tides_page(
        brain,
        "Logs match ^[a-z]+ lines.\n"
        "The spring tide flooded the salt marsh at night (chapter 3).[^Book-of-Tides]\n",
    )

    passage = resolved(client, brain, TIDES, 0)

    assert passage["source_id"] == "book-of-tides"
    assert passage["heading_trail"] == ["Book of Tides", "Chapter 3"]
    assert resolve(client, brain, TIDES, 1).status_code == 404


def test_results_are_cached_while_the_page_the_index_and_head_stay_the_same(
    client: TestClient, ctx: AppContext, brain: BrainFixture, tides: str, monkeypatch: Any
) -> None:
    calls: list[PurePosixPath] = []
    find_passages = ctx.search.find_passages

    def counting(brain_id: str, path: PurePosixPath, *args: Any, **kwargs: Any) -> Any:
        calls.append(path)
        return find_passages(brain_id, path, *args, **kwargs)

    monkeypatch.setattr(ctx.search, "find_passages", counting)
    first = resolved(client, brain, TIDES, 1)
    lookups = len(calls)

    assert resolved(client, brain, TIDES, 1) == first
    assert len(calls) == lookups

    (brain.root / "wiki" / "concepts" / "new.md").write_text("---\ntype: concept\n---\n\nNew.\n")
    repo = GitRepo(brain.root, GitIdentity(name="test-user", email="test-user@example.com"), None)
    assert repo.commit_all("Add page: New") is not None

    assert resolved(client, brain, TIDES, 1) == first
    assert len(calls) == 2 * lookups


def test_a_page_changed_before_a_commit_is_resolved_again(
    client: TestClient, brain: BrainFixture, tides: str
) -> None:
    assert resolved(client, brain, TIDES, 0)["heading_trail"][-1] == "Chapter 3"

    # A running job rewrites the page; it commits later.
    write_tides_page(brain, "The sea was calm at dawn.[^book-of-tides]\n")

    assert resolved(client, brain, TIDES, 0)["heading_trail"][-1] == "Chapter 1"


def test_a_canonical_document_indexed_again_is_resolved_again(
    client: TestClient, ctx: AppContext, brain: BrainFixture, tides: str
) -> None:
    before = resolved(client, brain, TIDES, 0)
    assert "at night" in tides[before["char_start"] : before["char_end"]]

    # The worker commits a new conversion first and updates the index after it.
    body = "# Book of Tides\n\nA new preface for this edition.\n" + BOOK_TEXT.removeprefix(
        "# Book of Tides\n"
    )
    write_book(brain, body)
    first_hit = resolved(client, brain, TIDES, 0)
    ctx.search.update(brain.id, brain.root, [PurePosixPath(BOOK)])

    after = resolved(client, brain, TIDES, 0)

    assert first_hit == before  # the index still has the old rows
    new_body = read_page(brain.root, PurePosixPath(BOOK)).body
    assert after["char_start"] > before["char_start"]
    assert "at night" in new_body[after["char_start"] : after["char_end"]]
