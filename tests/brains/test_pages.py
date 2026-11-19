import os
import shutil
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import pytest

import second_brain_builder.brains.pages as pages_module
from second_brain_builder.brains import GraphEdge, build_graph, list_pages, read_pages, write_index
from second_brain_builder.okf import Page

TRIP_NOTES = PurePosixPath("sources/journal/trip-notes/trip-notes.md")
COASTAL_BIRDS = PurePosixPath("sources/nature/coastal-birds/coastal-birds.md")
COMPARISON = PurePosixPath("wiki/comparisons/heron-vs-egret.md")
WADING = PurePosixPath("wiki/concepts/wading.md")
GREY_HERON = PurePosixPath("wiki/entities/grey-heron.md")
SUMMARY = PurePosixPath("wiki/summaries/coastal-birds.md")


def test_list_pages_returns_every_okf_page_sorted_by_path(sample_brain: Path) -> None:
    pages = list_pages(sample_brain)

    assert [(page.path, page.type, page.title) for page in pages] == [
        (TRIP_NOTES, "source", "Trip Notes"),
        (COASTAL_BIRDS, "source", "Coastal Birds"),
        (COMPARISON, "comparison", "heron-vs-egret"),  # no title: the filename stem
        (WADING, "concept", "Wading"),
        (GREY_HERON, "entity", "Grey Heron"),
        (SUMMARY, "summary", "Coastal Birds"),
    ]
    assert pages[4].description == "A tall wading bird of the Example Islands."


def test_list_pages_skips_files_that_are_not_pages(sample_brain: Path) -> None:
    # The fixture also has: index.md, log.md, AGENTS.md (no frontmatter), a Markdown
    # Source with frontmatter (trip-notes.original.markdown), a Markdown Asset with
    # frontmatter, a page in a dot-path (.drafts/), and a page whose frontmatter is
    # not YAML (wiki/entities/broken-frontmatter.md).
    paths = {page.path for page in list_pages(sample_brain)}
    assert paths == {TRIP_NOTES, COASTAL_BIRDS, COMPARISON, WADING, GREY_HERON, SUMMARY}


def test_pages_behind_symlinks_are_not_read(sample_brain: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntype: entity\ntitle: Outside\n---\n\nNot in the Brain.\n")
    (sample_brain / "wiki" / "entities" / "outside.md").symlink_to(outside)
    (sample_brain / "wiki" / "linked").symlink_to(tmp_path)

    titles = {page.title for page in list_pages(sample_brain)}
    assert "Outside" not in titles


def test_read_pages_keeps_the_body(sample_brain: Path) -> None:
    pages = {page.path: page for page in read_pages(sample_brain)}
    assert pages[WADING].body.startswith("# Wading\n")


def test_graph_nodes_are_the_pages(sample_brain: Path) -> None:
    graph = build_graph(sample_brain)
    assert [(node.path, node.type, node.title) for node in graph.nodes] == [
        (page.path, page.type, page.title) for page in list_pages(sample_brain)
    ]


def test_graph_edges_are_links_and_citations_between_pages(sample_brain: Path) -> None:
    graph = build_graph(sample_brain)

    # grey-heron.md also links to itself, to a fragment, to a missing page, to an
    # external URL, and (in a code span) to Trip Notes: none of them is an edge. Its
    # two links to wading.md (one relative, one rooted) give one edge.
    assert graph.edges == [
        GraphEdge(from_path=start, to_path=end)
        for start, end in [
            (COMPARISON, TRIP_NOTES),
            (COMPARISON, COASTAL_BIRDS),
            (COMPARISON, GREY_HERON),
            (WADING, COASTAL_BIRDS),
            (GREY_HERON, COASTAL_BIRDS),
            (GREY_HERON, WADING),
            (GREY_HERON, SUMMARY),
            (SUMMARY, COASTAL_BIRDS),
            (SUMMARY, WADING),
            (SUMMARY, GREY_HERON),
        ]
    ]


def test_graph_of_an_empty_brain(tmp_path: Path) -> None:
    graph = build_graph(tmp_path)
    assert graph.nodes == []
    assert graph.edges == []


def test_a_page_removed_while_the_pages_are_read_is_skipped(
    sample_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The worker changes a clone while the app reads it: git rebase, git mv, or a
    # Source delete can remove a file after the listing and before the read.
    real_read_page = pages_module.read_page

    def read_after_removal(root: Path, path: PurePosixPath) -> Page:
        if path == WADING:
            (root / path).unlink()
        return real_read_page(root, path)

    monkeypatch.setattr(pages_module, "read_page", read_after_removal)

    paths = {page.path for page in list_pages(sample_brain)}
    assert paths == {TRIP_NOTES, COASTAL_BIRDS, COMPARISON, GREY_HERON, SUMMARY}


def test_a_directory_removed_while_the_pages_are_listed_is_skipped(
    sample_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    removed = sample_brain / "sources" / "journal"
    real_iterdir = Path.iterdir

    def iterdir_after_removal(self: Path) -> Iterator[Path]:
        if self == removed:
            shutil.rmtree(self)
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir_after_removal)

    paths = {page.path for page in build_graph(sample_brain).nodes}
    assert paths == {COASTAL_BIRDS, COMPARISON, WADING, GREY_HERON, SUMMARY}


def test_files_and_directories_whose_name_is_not_utf8_are_skipped(sample_brain: Path) -> None:
    page = "---\ntype: concept\ntitle: Odd Name\n---\n\nText.\n"
    odd_directory = sample_brain / "wiki" / os.fsdecode(b"odd-\xfe")
    try:
        (sample_brain / "wiki" / "concepts" / os.fsdecode(b"odd-\xff.md")).write_text(page)
        odd_directory.mkdir()
    except OSError:  # a file system that allows only UTF-8 names
        pytest.skip("the file system rejects names that are not UTF-8")
    (odd_directory / "page.md").write_text(page)

    paths = {page.path for page in list_pages(sample_brain)}
    assert paths == {TRIP_NOTES, COASTAL_BIRDS, COMPARISON, WADING, GREY_HERON, SUMMARY}
    # The API sends these as JSON, and every job writes index.md from them.
    build_graph(sample_brain).model_dump_json()
    write_index(sample_brain)
    assert "Odd Name" not in (sample_brain / "index.md").read_text()
