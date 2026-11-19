from pathlib import PurePosixPath

import pytest

from second_brain_builder.brains import BrainLayout
from second_brain_builder.okf import PageType

layout = BrainLayout()
BOOKS = PurePosixPath("books/sci-fi")


def test_source_paths_follow_the_brain_layout() -> None:
    assert layout.source_dir(BOOKS, "dune") == PurePosixPath("sources/books/sci-fi/dune")
    assert layout.canonical_path(BOOKS, "dune") == PurePosixPath(
        "sources/books/sci-fi/dune/dune.md"
    )
    assert layout.source_file_path(BOOKS, "dune", "epub") == PurePosixPath(
        "sources/books/sci-fi/dune/dune.epub"
    )
    assert layout.assets_dir(BOOKS, "dune") == PurePosixPath("sources/books/sci-fi/dune/assets")


def test_a_source_can_sit_directly_under_sources() -> None:
    assert layout.canonical_path(PurePosixPath(), "dune") == PurePosixPath("sources/dune/dune.md")


@pytest.mark.parametrize("ext", ["md", ".md", "MD"])
def test_a_markdown_source_does_not_collide_with_its_canonical_document(ext: str) -> None:
    source = layout.source_file_path(BOOKS, "notes", ext)
    assert source == PurePosixPath("sources/books/sci-fi/notes/notes.original.markdown")
    assert source != layout.canonical_path(BOOKS, "notes")


def test_source_file_extension_is_lowercased_without_its_dot() -> None:
    assert layout.source_file_path(BOOKS, "scan", ".PDF").name == "scan.pdf"
    assert layout.source_file_path(BOOKS, "data", "tar.gz").name == "data.tar.gz"


@pytest.mark.parametrize(
    ("page_type", "expected"),
    [
        (PageType.SUMMARY, "wiki/summaries/dune.md"),
        (PageType.ENTITY, "wiki/entities/dune.md"),
        (PageType.CONCEPT, "wiki/concepts/dune.md"),
        (PageType.COMPARISON, "wiki/comparisons/dune.md"),
        (PageType.SYNTHESIS, "wiki/syntheses/dune.md"),
    ],
)
def test_wiki_path_per_page_type(page_type: PageType, expected: str) -> None:
    assert layout.wiki_path(page_type, "dune") == PurePosixPath(expected)


def test_canonical_documents_have_no_wiki_path() -> None:
    with pytest.raises(ValueError, match="not Wiki Pages"):
        layout.wiki_path(PageType.SOURCE, "dune")


@pytest.mark.parametrize(
    "folder",
    [
        PurePosixPath("../outside"),
        PurePosixPath("/books"),
        PurePosixPath("Books"),
        PurePosixPath(".git"),
        PurePosixPath("books/assets"),
    ],
)
def test_folders_must_be_slug_paths(folder: PurePosixPath) -> None:
    with pytest.raises(ValueError, match="folder"):
        layout.canonical_path(folder, "dune")


@pytest.mark.parametrize("slug", ["", "Dune", "../dune", "dune.md", "index", "assets"])
def test_source_and_wiki_slugs_must_be_slugs(slug: str) -> None:
    with pytest.raises(ValueError, match="not a slug"):
        layout.canonical_path(BOOKS, slug)
    with pytest.raises(ValueError, match="not a slug"):
        layout.wiki_path(PageType.ENTITY, slug)


@pytest.mark.parametrize("ext", ["", ".", "pdf/../x", "p df", ".tar.", "x" * 17])
def test_source_file_extension_must_be_plain(ext: str) -> None:
    with pytest.raises(ValueError, match="extension"):
        layout.source_file_path(BOOKS, "dune", ext)
