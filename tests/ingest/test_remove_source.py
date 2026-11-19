"""remove_source on a real Brain: citations, orphan pages, and links to removed pages."""

import shutil
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import write_index
from second_brain_builder.ingest import IngestEvent, remove_source
from second_brain_builder.okf import SourceRef, footnote_labels, read_page, validate_bundle

GREY_HERON = PurePosixPath("wiki/entities/grey-heron.md")
SEA_STAR = PurePosixPath("wiki/entities/sea-star.md")
COASTAL_BIRDS_DIR = Path("sources/nature/coastal-birds")


def test_removing_a_source_cleans_citations_deletes_orphan_pages_and_unlinks_them(
    brain: Path,
) -> None:
    # As the source.delete job does: the Source directory goes first.
    shutil.rmtree(brain / COASTAL_BIRDS_DIR)
    events: list[IngestEvent] = []

    result = remove_source(brain, "coastal-birds", events.append)

    deleted = [
        PurePosixPath("wiki/comparisons/heron-vs-egret.md"),
        PurePosixPath("wiki/concepts/wading.md"),
        PurePosixPath("wiki/summaries/coastal-birds.md"),
    ]
    assert result.deleted == deleted
    assert result.written == [GREY_HERON, SEA_STAR]
    assert not any((brain / path).exists() for path in deleted)

    heron = read_page(brain, GREY_HERON)
    assert heron.frontmatter is not None
    assert heron.frontmatter.sources == [
        SourceRef(
            id="tide-pools",
            resource="/sources/nature/tide-pools/tide-pools.md",
            title="Tide Pools of the Example Coast",
        )
    ]
    assert footnote_labels(heron.body) == {"tide-pools"}
    assert "coastal-birds" not in heron.body
    assert "It\nhunts by wading; see also\nGrey Heron and Little Egret and the\nbird guide." in (
        heron.body
    )
    assert heron.frontmatter.verified  # other frontmatter stays
    sea_star = read_page(brain, SEA_STAR)
    assert "hunt by wading, it moves" in sea_star.body
    # A reference-style link to a deleted page loses its definition and becomes text too.
    assert "birds wade past it and the [Grey Heron][] looks" in sea_star.body
    assert "wading.md" not in sea_star.body
    assert "[Grey Heron]: /wiki/entities/grey-heron.md\n" in sea_star.body

    write_index(brain)
    assert validate_bundle(brain) == []

    assert [(event.kind, event.data["path"]) for event in events] == [
        ("page_deleted", "wiki/comparisons/heron-vs-egret.md"),
        ("page_deleted", "wiki/concepts/wading.md"),
        ("page_deleted", "wiki/summaries/coastal-birds.md"),
        ("page_written", str(GREY_HERON)),
        ("page_written", str(SEA_STAR)),
    ]
    assert events[0].data == {
        "path": "wiki/comparisons/heron-vs-egret.md",
        "title": "Grey Heron and Little Egret",
        "type": "comparison",
    }
    assert events[3].data["links"] == ["sources/nature/tide-pools/tide-pools.md"]
    assert events[4].data["links"] == [
        "wiki/entities/grey-heron.md",
        "sources/nature/tide-pools/tide-pools.md",
    ]


def test_removing_a_source_leaves_pages_that_do_not_cite_it_untouched(brain: Path) -> None:
    untouched = ["wiki/comparisons/heron-vs-egret.md", "wiki/concepts/wading.md"]
    before = {path: (brain / path).read_bytes() for path in untouched}

    result = remove_source(brain, "tide-pools", lambda event: None)

    assert result.deleted == [SEA_STAR]
    assert result.written == [GREY_HERON, PurePosixPath("wiki/summaries/coastal-birds.md")]
    assert {path: (brain / path).read_bytes() for path in untouched} == before
    heron = read_page(brain, GREY_HERON)
    assert heron.frontmatter is not None
    assert [ref.id for ref in heron.frontmatter.sources] == ["coastal-birds"]
    assert footnote_labels(heron.body) == {"coastal-birds"}
    assert "rock pools" in heron.body  # the text stays; only its citation goes


def test_source_removal_unlinks_inline_images_in_pages_without_its_citations(brain: Path) -> None:
    file = brain / SEA_STAR
    with file.open("a") as stream:
        stream.write(
            '\n![chart](/sources/nature/coastal-birds/assets/chart.png "Chart")\n'
            "![relative](../../sources/nature/coastal-birds/assets/map.png)\n"
            "`![code](/sources/nature/coastal-birds/assets/chart.png)`\n"
            "![kept](/sources/nature/tide-pools/assets/chart.png)\n"
        )
    events: list[IngestEvent] = []
    result = remove_source(brain, "coastal-birds", events.append)
    body = read_page(brain, SEA_STAR).body
    assert "\nchart\nrelative\n" in body
    assert "`![code](/sources/nature/coastal-birds/assets/chart.png)`" in body
    assert "![kept](/sources/nature/tide-pools/assets/chart.png)" in body
    assert SEA_STAR in result.written
    assert any(
        event.kind == "page_written" and event.data["path"] == str(SEA_STAR) for event in events
    )
