"""Empty Folders and the Folder filter of the page list and the graph."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from second_brain_builder.brains import GraphEdge, build_graph, kept_folders, list_pages

TRIP_NOTES = PurePosixPath("sources/journal/trip-notes/trip-notes.md")
COASTAL_BIRDS = PurePosixPath("sources/nature/coastal-birds/coastal-birds.md")
COMPARISON = PurePosixPath("wiki/comparisons/heron-vs-egret.md")
WADING = PurePosixPath("wiki/concepts/wading.md")
GREY_HERON = PurePosixPath("wiki/entities/grey-heron.md")
SUMMARY = PurePosixPath("wiki/summaries/coastal-birds.md")
FIELD_DAY = PurePosixPath("sources/journal/2026/field-day/field-day.md")
TIDE_TABLE = PurePosixPath("wiki/entities/tide-table.md")
FIELD_DAY_NAMES = PurePosixPath("wiki/name-indexes/field-day-a-z.md")


def write(root: Path, path: PurePosixPath | str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def add_nested_source(root: Path) -> None:
    """A Source in ``journal/2026``, a Wiki Page and a Name Index page that cite it."""
    write(
        root,
        FIELD_DAY,
        "---\ntype: source\ntitle: Field Day\nresource: ./field-day.txt\n"
        'generated: {by: "process:sbb-conversion", at: "2026-11-18T12:00:00+00:00"}\n'
        "---\n\n# Field Day\n\nThe tide table of the bay.\n",
    )
    write(
        root,
        TIDE_TABLE,
        "---\ntype: entity\ntitle: Tide Table\nsources:\n  - id: field-day\n"
        f"    resource: /{FIELD_DAY}\n"
        'generated: {by: sbb/test-model, at: "2026-11-18T12:05:00+00:00"}\n'
        "---\n\nThe tide table lists low tides.[^field-day] See [Wading](/wiki/concepts/wading.md)"
        f" and [Trip Notes](/{TRIP_NOTES}).\n\n[^field-day]: [Field Day](/{FIELD_DAY})\n",
    )
    write(
        root,
        FIELD_DAY_NAMES,
        "---\ntype: name-index\ntitle: 'Field Day: Names A-Z'\nsources:\n  - id: field-day\n"
        f"    resource: /{FIELD_DAY}\n"
        'generated: {by: "second-brain-builder:name-index", at: "2026-11-18T12:00:00+00:00"}\n'
        "---\n\n# Field Day: Names A-Z\n\n- **Bay**: a bay.[^field-day]\n\n"
        f"[^field-day]: [Field Day](/{FIELD_DAY})\n",
    )


def test_folder_filter_keeps_its_sources_and_the_pages_that_cite_them(sample_brain: Path) -> None:
    pages = list_pages(sample_brain, PurePosixPath("journal"))

    # heron-vs-egret cites trip-notes (and coastal-birds); the other Wiki Pages do not.
    assert [page.path for page in pages] == [TRIP_NOTES, COMPARISON]
    assert [page.path for page in list_pages(sample_brain, PurePosixPath("nature"))] == [
        COASTAL_BIRDS,
        COMPARISON,
        WADING,
        GREY_HERON,
        SUMMARY,
    ]


def test_folder_filter_is_recursive_and_keeps_name_indexes(sample_brain: Path) -> None:
    add_nested_source(sample_brain)

    journal = {page.path for page in list_pages(sample_brain, PurePosixPath("journal"))}
    nested = {page.path for page in list_pages(sample_brain, PurePosixPath("journal/2026"))}

    assert journal == {TRIP_NOTES, COMPARISON, FIELD_DAY, TIDE_TABLE, FIELD_DAY_NAMES}
    assert nested == {FIELD_DAY, TIDE_TABLE, FIELD_DAY_NAMES}


def test_folder_filter_of_an_unknown_or_empty_folder_keeps_nothing(sample_brain: Path) -> None:
    (sample_brain / "sources" / "empty").mkdir()
    (sample_brain / "sources" / "empty" / ".gitkeep").write_text("")

    assert list_pages(sample_brain, PurePosixPath("empty")) == []
    assert list_pages(sample_brain, PurePosixPath("nature/coastal-birds")) == []
    graph = build_graph(sample_brain, PurePosixPath("missing"))
    assert (graph.nodes, graph.edges) == ([], [])


def test_filtered_graph_has_only_the_links_between_kept_pages(sample_brain: Path) -> None:
    add_nested_source(sample_brain)

    graph = build_graph(sample_brain, PurePosixPath("journal/2026"))

    # tide-table also links to wading.md and trip-notes.md, which the filter drops.
    assert [node.path for node in graph.nodes] == [FIELD_DAY, TIDE_TABLE, FIELD_DAY_NAMES]
    assert graph.edges == [
        GraphEdge(from_path=TIDE_TABLE, to_path=FIELD_DAY),
        GraphEdge(from_path=FIELD_DAY_NAMES, to_path=FIELD_DAY),
    ]
    full = build_graph(sample_brain)
    assert GraphEdge(from_path=TIDE_TABLE, to_path=WADING) in full.edges


def test_kept_folders_are_the_folders_with_a_marker(sample_brain: Path, tmp_path: Path) -> None:
    sources = sample_brain / "sources"
    for folder in ("empty", "work/clients", "nature/coastal-birds", ".hidden", "Not A Slug"):
        (sources / folder).mkdir(parents=True, exist_ok=True)
        (sources / folder / ".gitkeep").write_text("")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".gitkeep").write_text("")
    (sources / "linked").symlink_to(outside)
    (sources / "journal" / ".gitkeep").symlink_to(outside / ".gitkeep")

    # A marker in a Source directory, a dot-path, a name that is not a slug, and
    # symbolic links do not make a Folder; "work" holds only a subfolder.
    assert kept_folders(sample_brain) == [PurePosixPath("empty"), PurePosixPath("work/clients")]


def test_kept_folders_of_a_brain_without_sources(tmp_path: Path) -> None:
    assert kept_folders(tmp_path) == []
