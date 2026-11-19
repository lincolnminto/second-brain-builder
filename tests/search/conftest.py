"""Fixtures for the search tests: an index file in tmp_path, and Brains to index."""

import shutil
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.okf import Frontmatter, Page, write_page
from second_brain_builder.search import SearchIndex

FIXTURES = Path(__file__).parent.parent / "fixtures" / "search"

MakePage = Callable[..., PurePosixPath]


@pytest.fixture
def index(tmp_path: Path) -> SearchIndex:
    """An empty index at tmp_path/data/search.sqlite."""
    return SearchIndex(tmp_path / "data" / "search.sqlite")


@pytest.fixture
def sample_brain(tmp_path: Path) -> Path:
    """A copy of tests/fixtures/search/brain.

    Every Markdown file in it has the word "fieldnote", so a query for that word
    shows which files the index holds. Seven of them are pages; the others are
    files that must never be indexed (dot-paths, Assets, Source files, index.md,
    log.md, AGENTS.md, files without frontmatter or type, invalid frontmatter).
    """
    root = tmp_path / "sample"
    shutil.copytree(FIXTURES / "brain", root)
    return root


@pytest.fixture
def make_page() -> MakePage:
    """Write an OKF page into a Brain root and return its path.

    Call it as ``make_page(root, "wiki/entities/x.md", "entity", title=..., body=...)``.
    """

    def make(
        root: Path,
        path: str,
        page_type: str,
        *,
        title: str | None = None,
        description: str | None = None,
        tags: Sequence[str] = (),
        body: str = "",
    ) -> PurePosixPath:
        page = Page(
            path=PurePosixPath(path),
            frontmatter=Frontmatter(
                type=page_type, title=title, description=description, tags=list(tags)
            ),
            body=body,
        )
        write_page(root, page)
        return page.path

    return make
