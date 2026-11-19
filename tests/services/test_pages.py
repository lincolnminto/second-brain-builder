"""Page use cases on a real Brain clone: read, raw files, graph, search, and path checks."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

import second_brain_builder.services.pages as pages_module
from second_brain_builder.brains import GitRepo
from second_brain_builder.services import (
    AppContext,
    InvalidInputError,
    NotFoundError,
    get_graph,
    list_pages,
    raw_file,
    read_page,
    search,
)
from second_brain_builder.services.brains import READ_ONLY_IDENTITY

if TYPE_CHECKING:
    from tests.services.conftest import BrainFixture, InsertBrain

SECRET_PAGE = "---\ntype: entity\ntitle: Outside\n---\n\nText outside the Brain.\n"


@pytest.mark.parametrize("path", ["wiki/entities/sea-star.md", "/wiki/entities/sea-star.md"])
def test_read_page_takes_paths_with_or_without_a_leading_slash(
    ctx: AppContext, brain: BrainFixture, path: str
) -> None:
    page = read_page(ctx, brain.id, path)

    assert (page.path, page.type, page.title) == (
        "wiki/entities/sea-star.md",
        "entity",
        "Ochre Sea Star",
    )
    assert [source.id for source in page.sources] == ["tide-pools"]
    assert page.generated_by == "sbb/test-model"
    assert "grow a new arm" in page.body


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "wiki/../../outside.md",
        "//outside.md",
        ".git/config",
        "wiki\\entities\\sea-star.md",
        "wiki/entities/sea-star.md\x00",
        "",
    ],
)
def test_paths_that_leave_the_brain_are_rejected(
    ctx: AppContext, brain: BrainFixture, path: str
) -> None:
    (brain.root.parent / "outside.md").write_text(SECRET_PAGE)

    with pytest.raises(InvalidInputError):
        read_page(ctx, brain.id, path)
    with pytest.raises(InvalidInputError):
        raw_file(ctx, brain.id, path)


def test_symbolic_link_out_of_the_brain_is_rejected(ctx: AppContext, brain: BrainFixture) -> None:
    outside = brain.root.parent / "outside.md"
    outside.write_text(SECRET_PAGE)
    (brain.root / "wiki" / "entities" / "leak.md").symlink_to(outside)

    with pytest.raises(InvalidInputError, match="outside"):
        read_page(ctx, brain.id, "wiki/entities/leak.md")
    with pytest.raises(InvalidInputError, match="outside"):
        raw_file(ctx, brain.id, "wiki/entities/leak.md")


def test_index_and_log_are_readable(ctx: AppContext, brain: BrainFixture) -> None:
    index = read_page(ctx, brain.id, "/index.md")
    log = read_page(ctx, brain.id, "log.md")

    assert (index.type, index.title) == ("", "Shore Notes")
    assert "[Ochre Sea Star](/wiki/entities/sea-star.md)" in index.body
    assert (log.type, log.title) == ("", "log")
    assert log.body.startswith("# Log")


def test_read_page_rejects_other_files_and_reports_missing_pages(
    ctx: AppContext, brain: BrainFixture
) -> None:
    with pytest.raises(InvalidInputError, match="not a Markdown page"):
        read_page(ctx, brain.id, "sources/nature/tide-pools/tide-pools.txt")
    with pytest.raises(NotFoundError):
        read_page(ctx, brain.id, "wiki/entities/missing.md")


def test_raw_svg_asset_is_never_shown_inline(ctx: AppContext, brain: BrainFixture) -> None:
    asset = raw_file(ctx, brain.id, "/sources/nature/coastal-birds/assets/heron.svg")

    assert asset.path == (brain.root / "sources/nature/coastal-birds/assets/heron.svg").resolve()
    assert (asset.media_type, asset.inline) == ("image/svg+xml", False)
    assert asset.size_bytes == asset.path.stat().st_size


def test_list_pages_filters_by_type(ctx: AppContext, brain: BrainFixture) -> None:
    entities = list_pages(ctx, brain.id, types=["entity"])

    assert [page.path for page in entities] == [
        "wiki/entities/grey-heron.md",
        "wiki/entities/sea-star.md",
    ]
    assert {page.type for page in list_pages(ctx, brain.id)} >= {"source", "summary", "concept"}


def test_graph_has_the_links_between_pages(ctx: AppContext, brain: BrainFixture) -> None:
    graph = get_graph(ctx, brain.id)

    edges = {(edge.from_path, edge.to_path) for edge in graph.edges}
    assert ("wiki/entities/sea-star.md", "wiki/concepts/wading.md") in edges
    assert ("wiki/entities/sea-star.md", "sources/nature/tide-pools/tide-pools.md") in edges
    assert "wiki/entities/sea-star.md" in {node.path for node in graph.nodes}


def test_graph_cache_reuses_head_and_invalidates_on_a_new_commit(
    ctx: AppContext, brain: BrainFixture
) -> None:
    with patch.object(pages_module, "build_graph", wraps=pages_module.build_graph) as build:
        first = get_graph(ctx, brain.id)
        assert get_graph(ctx, brain.id) == first
        assert build.call_count == 1

        page = brain.root / "wiki" / "concepts" / "new.md"
        page.write_text(
            "---\ntype: concept\ntitle: New\n---\n\n[Wading](/wiki/concepts/wading.md)\n"
        )
        GitRepo(brain.root, READ_ONLY_IDENTITY, None).commit_all("Add graph page")

        updated = get_graph(ctx, brain.id)
        assert "wiki/concepts/new.md" in {node.path for node in updated.nodes}
        assert ("wiki/concepts/new.md", "wiki/concepts/wading.md") in {
            (edge.from_path, edge.to_path) for edge in updated.edges
        }
        assert get_graph(ctx, brain.id) == updated
        assert build.call_count == 2


def test_graph_cache_is_not_mutated_by_callers(ctx: AppContext, brain: BrainFixture) -> None:
    first = get_graph(ctx, brain.id)
    expected = first.model_copy(deep=True)
    first.nodes.clear()
    cached = get_graph(ctx, brain.id)
    assert cached == expected
    cached.edges.clear()
    assert get_graph(ctx, brain.id) == expected


def test_graph_without_head_is_not_cached(
    ctx: AppContext, brain: BrainFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pages_module, "_graph_head", lambda root: None)
    with patch.object(pages_module, "build_graph", wraps=pages_module.build_graph) as build:
        assert get_graph(ctx, brain.id) == get_graph(ctx, brain.id)
        assert build.call_count == 2


def test_search_reads_only_the_brain_it_names(
    ctx: AppContext, brain: BrainFixture, insert_brain: InsertBrain
) -> None:
    other = insert_brain("test-user/empty")

    hits = search(ctx, brain.id, "grey heron")

    assert "wiki/entities/grey-heron.md" in {hit.path for hit in hits}
    assert search(ctx, other, "heron") == []
    for limit in (0, 101):
        with pytest.raises(InvalidInputError):
            search(ctx, brain.id, "heron", limit=limit)


def test_pages_of_an_unknown_brain_are_not_found(ctx: AppContext) -> None:
    with pytest.raises(NotFoundError):
        read_page(ctx, "00000000-0000-0000-0000-000000000000", "index.md")
