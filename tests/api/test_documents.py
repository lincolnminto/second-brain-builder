"""The document viewer routes: the outline and the segments of a large Markdown page."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from second_brain_builder.okf import Frontmatter, Page, read_page, write_page

if TYPE_CHECKING:
    from .conftest import BrainFixture

BOOK = "sources/books/book-of-tides/book-of-tides.md"
CHAPTERS = 400
FENCE = "```text\n" + "code line\n\n" * 2000 + "```\n"
OUTSIDE_PAGE = "---\ntype: entity\ntitle: Outside\n---\n\nText outside the Brain.\n"
OUTSIDE_TEXT = "Text outside the Brain"


def book_body() -> str:
    """About 2.7 MB of Markdown: 400 chapters of 100 verses, and a long fenced code block."""
    chapters = [
        f"## Chapter {number}\n\n"
        + "".join(
            f"{verse} The tide of chapter {number} rose over stone {verse}, and herons waited.\n\n"
            for verse in range(1, 101)
        )
        for number in range(1, CHAPTERS + 1)
    ]
    return "# Book of Tides\n\nA synthetic book.\n\n" + "".join(chapters) + FENCE


@pytest.fixture
def book(brain: BrainFixture) -> str:
    """The body of a large Canonical Document in the Brain clone."""
    page = Page(
        path=PurePosixPath(BOOK),
        frontmatter=Frontmatter(type="source", title="Book of Tides"),
        body=book_body(),
    )
    write_page(brain.root, page)
    return read_page(brain.root, page.path).body


def get(client: TestClient, brain: BrainFixture, route: str, **params: Any) -> Any:
    response = client.get(f"/api/brains/{brain.id}/documents/{route}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_outline_of_a_large_document(client: TestClient, brain: BrainFixture, book: str) -> None:
    assert len(book) > 2_500_000

    outline = get(client, brain, "outline", path=BOOK)

    assert (outline["path"], outline["title"]) == (BOOK, "Book of Tides")
    assert outline["total_chars"] == len(book)
    headings = outline["headings"]
    assert len(headings) == CHAPTERS + 1
    assert headings[0] == {"level": 1, "text": "Book of Tides", "segment": 0, "char_start": 0}
    assert [heading["text"] for heading in headings[1:4]] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert all(book[heading["char_start"] :].startswith("## Chapter") for heading in headings[1:])
    assert outline["segments"] > CHAPTERS


def test_segments_of_a_large_document_give_the_body_back(
    client: TestClient, brain: BrainFixture, book: str
) -> None:
    total = get(client, brain, "outline", path=BOOK)["segments"]
    segments: list[dict[str, Any]] = []
    while len(segments) < total:
        page = get(client, brain, "segments", path=BOOK, **{"from": len(segments), "count": 20})
        assert page["total"] == total
        assert 0 < len(page["segments"]) <= 20
        segments.extend(page["segments"])

    assert [segment["index"] for segment in segments] == list(range(total))
    assert "".join(segment["markdown"] for segment in segments) == book
    for segment in segments:
        assert book[segment["char_start"] : segment["char_end"]] == segment["markdown"]
        # Only the fenced code block, which is never cut, is longer than the target.
        assert len(segment["markdown"]) <= 12_000 or segment["markdown"] == FENCE
    assert segments[-1]["markdown"] == FENCE
    assert segments[-1]["heading_trail"] == ["Book of Tides", f"Chapter {CHAPTERS}"]


def test_a_heading_of_the_outline_opens_its_segment(
    client: TestClient, brain: BrainFixture, book: str
) -> None:
    heading = get(client, brain, "outline", path=BOOK)["headings"][150]

    page = get(client, brain, "segments", path=BOOK, **{"from": heading["segment"], "count": 1})

    (segment,) = page["segments"]
    assert segment["char_start"] == heading["char_start"]
    assert segment["markdown"].startswith(f"## {heading['text']}\n")
    assert segment["heading_trail"] == ["Book of Tides", heading["text"]]


def test_a_range_past_the_end_gives_no_segments(
    client: TestClient, brain: BrainFixture, book: str
) -> None:
    total = get(client, brain, "outline", path=BOOK)["segments"]

    page = get(client, brain, "segments", path=BOOK, **{"from": total, "count": 5})

    assert page == {"path": BOOK, "total": total, "segments": []}


def test_a_small_wiki_page_is_one_segment(client: TestClient, brain: BrainFixture) -> None:
    path = "/wiki/entities/grey-heron.md"
    body = read_page(brain.root, PurePosixPath(path.lstrip("/"))).body

    outline = get(client, brain, "outline", path=path)
    page = get(client, brain, "segments", path=path)

    assert (outline["segments"], outline["total_chars"]) == (1, len(body))
    assert [heading["text"] for heading in outline["headings"]] == ["Grey Heron"]
    assert page["path"] == "wiki/entities/grey-heron.md"
    assert [segment["markdown"] for segment in page["segments"]] == [body]


@pytest.mark.parametrize(
    "params",
    [{"count": 21}, {"count": 0}, {"from": -1}],
)
def test_segment_ranges_are_checked(
    client: TestClient, brain: BrainFixture, params: dict[str, int]
) -> None:
    response = client.get(
        f"/api/brains/{brain.id}/documents/segments",
        params={"path": "wiki/entities/grey-heron.md", **params},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("route", ["outline", "segments"])
@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "wiki/../../outside.md",
        ".git/config",
        "wiki/entities/.hidden.md",
        "sources/nature/tide-pools/tide-pools.txt",
        "wiki/entities/sea-star.md\x00",
    ],
)
def test_paths_that_leave_the_brain_or_are_not_pages_are_rejected(
    client: TestClient, brain: BrainFixture, route: str, path: str
) -> None:
    (brain.root.parent / "outside.md").write_text(OUTSIDE_PAGE)

    response = client.get(f"/api/brains/{brain.id}/documents/{route}", params={"path": path})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_input"
    assert OUTSIDE_TEXT not in response.text


@pytest.mark.parametrize("route", ["outline", "segments"])
def test_a_symbolic_link_out_of_the_brain_is_rejected(
    client: TestClient, brain: BrainFixture, route: str
) -> None:
    outside = brain.root.parent / "outside.md"
    outside.write_text(OUTSIDE_PAGE)
    (brain.root / "wiki" / "entities" / "leak.md").symlink_to(outside)

    response = client.get(
        f"/api/brains/{brain.id}/documents/{route}", params={"path": "wiki/entities/leak.md"}
    )

    assert response.status_code == 422
    assert OUTSIDE_TEXT not in response.text


def test_missing_pages_and_brains_give_404(client: TestClient, brain: BrainFixture) -> None:
    missing_page = client.get(
        f"/api/brains/{brain.id}/documents/outline", params={"path": "wiki/entities/missing.md"}
    )
    missing_brain = client.get(
        "/api/brains/no-such-brain/documents/outline", params={"path": "wiki/x.md"}
    )

    assert (missing_page.status_code, missing_brain.status_code) == (404, 404)


def test_a_changed_page_is_segmented_again(client: TestClient, brain: BrainFixture) -> None:
    path = "wiki/entities/grey-heron.md"
    assert get(client, brain, "outline", path=path)["segments"] == 1

    page = read_page(brain.root, PurePosixPath(path))
    longer = page.body + "".join(f"\n## Part {n}\n\nMore about herons.\n" for n in range(3))
    write_page(brain.root, page.model_copy(update={"body": longer}))

    outline = get(client, brain, "outline", path=path)
    assert outline["segments"] == 4
    assert outline["total_chars"] == len(read_page(brain.root, PurePosixPath(path)).body)
