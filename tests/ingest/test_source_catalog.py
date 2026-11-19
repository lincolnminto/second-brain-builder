"""The Source catalog needs frontmatter only, with the usual page filters."""

from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.brains import list_pages
from second_brain_builder.ingest._wiki import CanonicalSource, is_canonical_path, source_catalog


def test_catalog_matches_page_summaries(brain: Path) -> None:
    expected: dict[str, CanonicalSource] = {}
    for page in list_pages(brain):
        if page.type == "source" and is_canonical_path(page.path):
            expected.setdefault(
                page.path.stem,
                CanonicalSource(id=page.path.stem, path=page.path, title=page.title),
            )
    assert source_catalog(brain) == expected


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_catalog_does_not_decode_document_bodies(
    tmp_path: Path, newline: bytes, bom: bytes
) -> None:
    path = PurePosixPath("sources/book/book.md")
    file = tmp_path / path
    file.parent.mkdir(parents=True)
    header = newline.join([b"---", b"type: source", b"title: '  A   book  '", b"---", b""])
    file.write_bytes(bom + header + b"\xff" * 100_000)
    assert source_catalog(tmp_path) == {
        "book": CanonicalSource(id="book", path=path, title="A book")
    }


def test_catalog_preserves_filters_order_and_fallback_title(tmp_path: Path) -> None:
    for name, content in {
        "sources/a/book/book.md": "---\ntype: source\n---\n",
        "sources/z/book/book.md": "---\ntype: source\ntitle: Duplicate\n---\n",
        "sources/a/assets/hidden/hidden.md": "---\ntype: source\n---\n",
        "sources/.hidden/hidden.md": "---\ntype: source\n---\n",
        "sources/invalid/invalid.md": "---\ntype: [invalid]\n---\n",
        "sources/concept/concept.md": "---\ntype: concept\n---\n",
        "sources/missing/missing.md": "# No frontmatter\n",
        "sources/unclosed/unclosed.md": "---\ntype: source\n",
        "sources/other.md": "---\ntype: source\n---\n",
    }.items():
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
    (tmp_path / "sources/link").symlink_to(tmp_path / "sources/a/book", target_is_directory=True)
    path = PurePosixPath("sources/a/book/book.md")
    assert source_catalog(tmp_path) == {"book": CanonicalSource(id="book", path=path, title="book")}
