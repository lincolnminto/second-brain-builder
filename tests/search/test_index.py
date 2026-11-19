"""SearchIndex against real SQLite FTS5 files and real Brain files in tmp_path."""

import os
import sqlite3
import sys
import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.brains import list_pages
from second_brain_builder.okf import PageType
from second_brain_builder.search import SCHEMA_VERSION, SearchHit, SearchIndex

MakePage = Callable[..., PurePosixPath]

BRAIN = "brain-a"
SAMPLE_PAGES = {
    "sources/journal/trip-notes/trip-notes.md",
    "sources/nature/coastal-birds/coastal-birds.md",
    "wiki/concepts/acao-coletiva.md",
    "wiki/concepts/wading.md",
    "wiki/entities/grey-heron.md",
    "wiki/summaries/coastal-birds.md",
}


def paths(hits: list[SearchHit]) -> set[str]:
    return {str(hit.path) for hit in hits}


# --- What gets indexed ----------------------------------------------------------


def test_rebuild_indexes_the_pages_that_brains_lists_and_nothing_else(
    index: SearchIndex, sample_brain: Path
) -> None:
    index.rebuild(BRAIN, sample_brain)

    assert paths(index.query(BRAIN, "fieldnote")) == SAMPLE_PAGES
    assert {str(page.path) for page in list_pages(sample_brain)} == SAMPLE_PAGES


def test_update_of_every_file_indexes_the_same_pages_as_rebuild(
    index: SearchIndex, sample_brain: Path
) -> None:
    wiki = sample_brain / "wiki"
    (wiki / "entities" / "linked-heron.md").symlink_to(wiki / "entities" / "grey-heron.md")
    (wiki / "mirror").symlink_to(wiki / "concepts", target_is_directory=True)
    files = [
        PurePosixPath(path.relative_to(sample_brain).as_posix())
        for path in sample_brain.rglob("*")
        if not path.is_dir()
    ]
    files.append(PurePosixPath("wiki/mirror/wading.md"))

    index.rebuild("by-rebuild", sample_brain)
    index.update("by-update", sample_brain, files)
    # An update of unchanged, indexed pages keeps their rows.
    index.update("by-rebuild", sample_brain, files)

    assert paths(index.query("by-rebuild", "fieldnote")) == SAMPLE_PAGES
    assert paths(index.query("by-update", "fieldnote")) == SAMPLE_PAGES


def test_markdown_markup_is_not_searchable(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    make_page(
        root,
        "wiki/entities/heron.md",
        "entity",
        title="Heron",
        body=(
            "See [the tall bird](/wiki/entities/grey-heron.md) at the shore.[^src-a]\n\n"
            "![A heron at dawn](assets/heron-photo.svg)\n\n"
            "<!-- converter placeholder -->\n\n"
            "Run `count_birds` daily.\n\n"
            "[^src-a]: [Coastal Birds](/sources/nature/coastal-birds/coastal-birds.md)\n"
        ),
    )
    index.rebuild(BRAIN, root)

    for markup in ("entities", "nature", "svg", "photo", "placeholder", "src"):
        assert index.query(BRAIN, markup) == [], markup
    for visible in ("tall bird", "dawn", "count_birds", "coastal birds"):
        assert paths(index.query(BRAIN, visible)) == {"wiki/entities/heron.md"}, visible
    (hit,) = index.query(BRAIN, "tall")
    assert hit.snippet.startswith("See the **tall** bird at the shore. A heron at dawn")
    assert "](" not in hit.snippet


# --- Matching -------------------------------------------------------------------


@pytest.mark.parametrize("text", ["acao", "ACAO", "ação", "coracao", "Coração"])
def test_matching_ignores_case_and_diacritics(
    index: SearchIndex, sample_brain: Path, text: str
) -> None:
    index.rebuild(BRAIN, sample_brain)

    assert paths(index.query(BRAIN, text)) == {"wiki/concepts/acao-coletiva.md"}


def test_query_with_diacritics_finds_text_without_them(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    make_page(root, "wiki/concepts/cafe.md", "concept", title="Cafe", body="It opens at dawn.")
    index.rebuild(BRAIN, root)

    assert paths(index.query(BRAIN, "café")) == {"wiki/concepts/cafe.md"}


def test_snippet_marks_matches_in_their_original_spelling(
    index: SearchIndex, sample_brain: Path
) -> None:
    index.rebuild(BRAIN, sample_brain)

    (hit,) = index.query(BRAIN, "acao")

    assert hit.snippet.startswith("**Ação** coletiva Uma fieldnote sobre a **ação** da vila")
    assert hit.title == "Ação coletiva"
    assert hit.type == "concept"


def test_only_the_last_term_matches_as_a_prefix(index: SearchIndex, sample_brain: Path) -> None:
    index.rebuild(BRAIN, sample_brain)

    assert "wiki/entities/grey-heron.md" in paths(index.query(BRAIN, "heron gre"))
    assert index.query(BRAIN, "gre heron") == []


def test_bm25_ranks_title_over_description_over_tags_over_body(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    for number in range(5):
        make_page(root, f"wiki/concepts/filler-{number}.md", "concept", body="Gulls on a pier.")
    make_page(
        root,
        "wiki/concepts/in-body.md",
        "concept",
        title="Coast walks",
        description="Walks along the coast.",
        tags=["walks"],
        body="We saw the lighthouse from the cliff path in the morning.",
    )
    make_page(
        root,
        "wiki/concepts/in-tags.md",
        "concept",
        title="Harbor walks",
        description="Walks along the harbor.",
        tags=["lighthouse"],
        body="We saw the boats from the stone pier in the morning.",
    )
    make_page(
        root,
        "wiki/concepts/in-description.md",
        "concept",
        title="Cliff walks",
        description="Walks to the lighthouse.",
        tags=["walks"],
        body="We saw the gulls from the cliff path in the morning.",
    )
    make_page(
        root,
        "wiki/concepts/in-title.md",
        "concept",
        title="Lighthouse walks",
        description="Walks along the cliff.",
        tags=["walks"],
        body="We saw the waves from the cliff path in the morning.",
    )
    index.rebuild(BRAIN, root)

    hits = index.query(BRAIN, "lighthouse")

    assert [hit.path.stem for hit in hits] == ["in-title", "in-description", "in-tags", "in-body"]
    assert hits[0].score > hits[1].score > hits[2].score > hits[3].score > 0


def test_a_title_only_match_shows_the_description_as_snippet(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    make_page(
        root,
        "wiki/concepts/lighthouse.md",
        "concept",
        title="Lighthouse",
        description="A tower with a light.",
        body="It guides ships at night.",
    )
    index.rebuild(BRAIN, root)

    (hit,) = index.query(BRAIN, "lighthouse")

    assert hit.snippet == "A tower with a light."


def test_a_title_match_ranks_above_body_matches_whatever_the_body_length(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    canonical = make_page(
        root,
        "sources/books/lighthouse/lighthouse.md",
        "source",
        title="Lighthouse",
        description="EPUB, 300 pages.",
        body="The keeper climbed the stairs at night. " * 2000,
    )
    # Each citing Wiki Page repeats the Source title in its footnote.
    citation = "[^lighthouse]: [Lighthouse](/sources/books/lighthouse/lighthouse.md)"
    for number in range(25):
        make_page(
            root,
            f"wiki/entities/keeper-{number}.md",
            "entity",
            title=f"Keeper {number}",
            body=f"A keeper of the lamp.[^lighthouse]\n\n{citation}\n",
        )
    make_page(root, "wiki/entities/lamp.md", "entity", title="Lamp", body="The lighthouse lamp.")
    index.rebuild(BRAIN, root)

    hits = index.query(BRAIN, "lighthouse")

    assert len(hits) == 20
    assert hits[0].path == canonical
    assert all(hit.score < hits[0].score for hit in hits[1:])


def test_a_match_far_into_a_long_body_gives_a_snippet_around_it(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    filler = "Waves broke on the empty beach all day. " * 500
    make_page(
        root,
        "sources/journal/beach-days/beach-days.md",
        "source",
        title="Beach days",
        body=filler + "At dusk a heron landed on the pier. " + filler,
    )
    index.rebuild(BRAIN, root)

    (hit,) = index.query(BRAIN, "heron")

    assert "a **heron** landed on the pier" in hit.snippet
    assert hit.snippet.startswith("…")
    assert hit.snippet.endswith("…")


# --- Query text is never FTS5 syntax --------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '"',
        "'",
        'heron"',
        'grey "heron" "',
        "AND",
        "OR heron",
        "heron NOT",
        "NEAR",
        "NEAR(grey heron)",
        "grey NEAR/2 heron",
        "*",
        "heron*",
        "*heron",
        "grey * heron",
        ":",
        "title:",
        "title:heron",
        "{title description}:heron",
        "- heron",
        "-heron",
        "+heron",
        "^heron",
        "(",
        "(grey OR heron",
        "heron)",
        "\\",
        "%_",
        "'; DROP TABLE documents; --",
        "grey\x00heron",
        "\x00",
        "‽",
        "🙂",
        # Lone surrogates: a JSON decoder keeps "\ud83d" from half an emoji.
        "\ud83d",
        "heron \udcff",
        "gre\ud800",
    ],
)
def test_query_text_with_fts5_syntax_does_not_raise(
    index: SearchIndex, sample_brain: Path, text: str
) -> None:
    index.rebuild(BRAIN, sample_brain)

    hits = index.query(BRAIN, text)

    assert all(str(hit.path) in SAMPLE_PAGES for hit in hits)
    assert paths(index.query(BRAIN, "fieldnote")) == SAMPLE_PAGES


@pytest.mark.parametrize("text", ["", "   \n\t", "*", ":", '""', "\x00", "( ) -"])
def test_text_without_letters_or_digits_gives_no_hits(
    index: SearchIndex, sample_brain: Path, text: str
) -> None:
    index.rebuild(BRAIN, sample_brain)

    assert index.query(BRAIN, text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # As FTS5 syntax, each of these would give a different result.
        ("heron OR zebra", set()),
        ("egret AND heron", {"wiki/entities/egret.md"}),
        ("heron NOT egret", {"wiki/concepts/plain-words.md"}),
        ("title:heron", {"wiki/concepts/plain-words.md"}),
        ("NEAR(heron cliff)", set()),
        ('"grey heron', {"wiki/concepts/plain-words.md"}),
    ],
)
def test_fts5_operators_in_query_text_are_plain_words(
    index: SearchIndex, tmp_path: Path, make_page: MakePage, text: str, expected: set[str]
) -> None:
    root = tmp_path / "brain"
    make_page(
        root,
        "wiki/concepts/plain-words.md",
        "concept",
        title="Plain words",
        body="The heron is not an egret. Working title: heron study, near the grey cliff.",
    )
    make_page(
        root,
        "wiki/entities/egret.md",
        "entity",
        title="Egret",
        body="An egret and a heron share the flat.",
    )
    index.rebuild(BRAIN, root)

    assert paths(index.query(BRAIN, text)) == expected


# --- Filters and limits ---------------------------------------------------------


def test_types_filter_the_hits(index: SearchIndex, sample_brain: Path) -> None:
    index.rebuild(BRAIN, sample_brain)

    sources = index.query(BRAIN, "fieldnote", types=["source"])
    wiki = index.query(BRAIN, "fieldnote", types=[PageType.ENTITY, PageType.SUMMARY])

    assert paths(sources) == {
        "sources/journal/trip-notes/trip-notes.md",
        "sources/nature/coastal-birds/coastal-birds.md",
    }
    assert {hit.type for hit in sources} == {"source"}
    assert paths(wiki) == {"wiki/entities/grey-heron.md", "wiki/summaries/coastal-birds.md"}
    assert index.query(BRAIN, "fieldnote", types=[]) == []


def test_limit_keeps_the_best_hits(index: SearchIndex, sample_brain: Path) -> None:
    index.rebuild(BRAIN, sample_brain)
    every_hit = index.query(BRAIN, "fieldnote")

    assert len(every_hit) == len(SAMPLE_PAGES)
    assert index.query(BRAIN, "fieldnote", limit=2) == every_hit[:2]
    with pytest.raises(ValueError, match="limit"):
        index.query(BRAIN, "fieldnote", limit=0)


# --- Rebuild and update ---------------------------------------------------------


def test_rebuild_replaces_the_rows_of_the_brain(
    index: SearchIndex, sample_brain: Path, make_page: MakePage
) -> None:
    index.rebuild(BRAIN, sample_brain)
    (sample_brain / "wiki" / "entities" / "grey-heron.md").unlink()
    make_page(
        sample_brain, "wiki/concepts/wading.md", "concept", title="Paddling", body="fieldnote"
    )

    index.rebuild(BRAIN, sample_brain)

    assert paths(index.query(BRAIN, "fieldnote")) == SAMPLE_PAGES - {"wiki/entities/grey-heron.md"}
    assert paths(index.query(BRAIN, "paddling")) == {"wiki/concepts/wading.md"}
    assert index.query(BRAIN, "walking") == []


def test_update_removes_the_rows_of_deleted_files(index: SearchIndex, sample_brain: Path) -> None:
    index.rebuild(BRAIN, sample_brain)
    deleted = PurePosixPath("wiki/entities/grey-heron.md")
    (sample_brain / deleted).unlink()

    index.update(BRAIN, sample_brain, [deleted])

    assert paths(index.query(BRAIN, "fieldnote")) == SAMPLE_PAGES - {str(deleted)}


def test_update_follows_a_moved_source_and_an_edited_page(
    index: SearchIndex, sample_brain: Path, make_page: MakePage
) -> None:
    index.rebuild(BRAIN, sample_brain)
    (sample_brain / "sources" / "archive").mkdir()
    (sample_brain / "sources" / "journal" / "trip-notes").rename(
        sample_brain / "sources" / "archive" / "trip-notes"
    )
    edited = make_page(
        sample_brain, "wiki/concepts/wading.md", "concept", title="Paddling", body="fieldnote"
    )

    index.update(
        BRAIN,
        sample_brain,
        [
            PurePosixPath("sources/journal/trip-notes/trip-notes.md"),
            PurePosixPath("sources/archive/trip-notes/trip-notes.md"),
            PurePosixPath("sources/archive/trip-notes/trip-notes.original.markdown"),
            edited,
        ],
    )

    assert paths(index.query(BRAIN, "egrets")) == {"sources/archive/trip-notes/trip-notes.md"}
    assert paths(index.query(BRAIN, "paddling")) == {"wiki/concepts/wading.md"}
    assert index.query(BRAIN, "walking") == []
    assert len(index.query(BRAIN, "fieldnote")) == len(SAMPLE_PAGES)


@pytest.mark.parametrize(
    "new_text",
    [
        "---\ntype: concept\ntitle: [unclosed\n---\n\nfieldnote\n",
        "---\ntitle: Wading\n---\n\nfieldnote\n",
        "# Wading\n\nfieldnote\n",
    ],
    ids=["invalid-frontmatter", "no-type", "no-frontmatter"],
)
def test_update_removes_the_row_of_a_file_that_is_no_longer_a_page(
    index: SearchIndex, sample_brain: Path, new_text: str
) -> None:
    index.rebuild(BRAIN, sample_brain)
    path = PurePosixPath("wiki/concepts/wading.md")
    (sample_brain / path).write_text(new_text, encoding="utf-8")

    index.update(BRAIN, sample_brain, [path])

    assert paths(index.query(BRAIN, "fieldnote")) == SAMPLE_PAGES - {str(path)}


def test_update_skips_a_path_it_cannot_check_and_indexes_the_rest_of_the_batch(
    index: SearchIndex, sample_brain: Path, make_page: MakePage
) -> None:
    index.rebuild(BRAIN, sample_brain)
    new = make_page(sample_brain, "wiki/concepts/tides.md", "concept", body="fieldnote")
    unusable = [
        PurePosixPath("wiki/concepts/" + "x" * 300 + ".md"),  # longer than a file name can be
        PurePosixPath("wiki/concepts/latin-1-\udce9.md"),  # a name that is not UTF-8
    ]

    index.update(BRAIN, sample_brain, [*unusable, new])

    assert paths(index.query(BRAIN, "fieldnote")) == SAMPLE_PAGES | {str(new)}


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX file permissions"
)
def test_a_file_without_read_permission_loses_its_row_in_rebuild_and_update(
    index: SearchIndex, sample_brain: Path, make_page: MakePage
) -> None:
    index.rebuild("by-rebuild", sample_brain)
    index.rebuild("by-update", sample_brain)
    locked = PurePosixPath("wiki/concepts/wading.md")
    new = make_page(sample_brain, "wiki/concepts/tides.md", "concept", body="fieldnote")
    (sample_brain / locked).chmod(0)
    try:
        index.rebuild("by-rebuild", sample_brain)
        index.update("by-update", sample_brain, [locked, new])
    finally:
        (sample_brain / locked).chmod(0o644)

    expected = (SAMPLE_PAGES - {str(locked)}) | {str(new)}
    assert paths(index.query("by-rebuild", "fieldnote")) == expected
    assert paths(index.query("by-update", "fieldnote")) == expected


def test_an_index_kept_current_by_update_matches_a_fresh_rebuild(
    tmp_path: Path, sample_brain: Path, make_page: MakePage
) -> None:
    kept = SearchIndex(tmp_path / "kept.sqlite")
    kept.rebuild(BRAIN, sample_brain)
    # Long bodies have many passages; each write replaces every indexed value.
    long_body = "A fieldnote about the heron and the egret on the shore. " * 300
    changed = [
        make_page(
            sample_brain,
            "wiki/concepts/wading.md",
            "concept",
            title="Paddling",
            tags=["shore"],
            body=long_body,
        ),
        make_page(
            sample_brain,
            "wiki/entities/egret.md",
            "entity",
            title="Egret",
            description="A white heron.",
            body=long_body + "Egrets nest in trees.",
        ),
        PurePosixPath("wiki/entities/grey-heron.md"),
    ]
    (sample_brain / changed[-1]).unlink()
    kept.update(BRAIN, sample_brain, changed)
    kept.update(BRAIN, sample_brain, changed)
    shorter = make_page(
        sample_brain, "wiki/entities/egret.md", "entity", title="Egret", body="Egrets nest."
    )
    kept.update(BRAIN, sample_brain, [shorter])

    fresh = SearchIndex(tmp_path / "fresh.sqlite")
    fresh.rebuild(BRAIN, sample_brain)

    # BM25 uses the number of rows, their lengths, and how many rows have each term,
    # so equal scores show that each write removed every old value.
    for text in ("fieldnote", "heron", "egret shore", "paddling", "white", "nest", "walking"):
        assert kept.query(BRAIN, text, limit=50) == fresh.query(BRAIN, text, limit=50), text
    assert kept.query(BRAIN, "walking") == []
    assert kept.query(BRAIN, "white") == []


@pytest.mark.parametrize(
    "path", ["/wiki/entities/grey-heron.md", "../outside.md", "wiki/../../outside.md", ""]
)
def test_update_rejects_paths_that_are_not_relative_to_the_root(
    index: SearchIndex, sample_brain: Path, path: str
) -> None:
    index.rebuild(BRAIN, sample_brain)

    with pytest.raises(ValueError, match="relative to the Brain root"):
        index.update(BRAIN, sample_brain, [PurePosixPath(path)])
    assert paths(index.query(BRAIN, "fieldnote")) == SAMPLE_PAGES


@pytest.mark.parametrize(
    "write",
    [
        lambda index, root: index.rebuild(BRAIN, root),
        lambda index, root: index.update(BRAIN, root, [PurePosixPath("wiki/concepts/wading.md")]),
    ],
    ids=["rebuild", "update"],
)
def test_a_missing_clone_raises_and_keeps_the_rows(
    index: SearchIndex,
    sample_brain: Path,
    tmp_path: Path,
    write: Callable[[SearchIndex, Path], None],
) -> None:
    index.rebuild(BRAIN, sample_brain)

    with pytest.raises(NotADirectoryError):
        write(index, tmp_path / "missing")
    assert paths(index.query(BRAIN, "fieldnote")) == SAMPLE_PAGES


# --- Brains, files, and processes -----------------------------------------------


def test_brains_do_not_see_each_other(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    herons = tmp_path / "herons"
    owls = tmp_path / "owls"
    same_path = make_page(
        herons, "wiki/entities/bird.md", "entity", title="Grey Heron", body="A shore bird."
    )
    make_page(owls, str(same_path), "entity", title="Snow Owl", body="A tundra bird.")
    index.rebuild("herons", herons)
    index.rebuild("owls", owls)

    assert [hit.title for hit in index.query("herons", "bird")] == ["Grey Heron"]
    assert [hit.title for hit in index.query("owls", "bird")] == ["Snow Owl"]
    assert index.query("herons", "owl") == []
    assert index.query("owls", "heron") == []

    (owls / same_path).unlink()
    index.update("owls", owls, [same_path])
    assert index.query("owls", "bird") == []
    assert [hit.title for hit in index.query("herons", "bird")] == ["Grey Heron"]

    make_page(owls, "wiki/entities/owl.md", "entity", title="Snow Owl")
    index.rebuild("owls", owls)
    index.remove_brain("owls")
    assert index.query("owls", "owl") == []
    assert [hit.title for hit in index.query("herons", "bird")] == ["Grey Heron"]
    assert index.query("never-indexed", "bird") == []

    index.rebuild("owls", owls)
    assert [hit.title for hit in index.query("owls", "owl")] == ["Snow Owl"]


def test_other_brains_do_not_change_the_ranking_inside_a_brain(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    birds = tmp_path / "birds"
    make_page(birds, "wiki/concepts/one.md", "concept", body="alpha alpha alpha beta")
    make_page(birds, "wiki/concepts/two.md", "concept", body="alpha beta beta beta")
    index.rebuild("birds", birds)
    before = index.query("birds", "alpha beta")

    other = tmp_path / "other"
    for number in range(20):
        make_page(other, f"wiki/concepts/p{number}.md", "concept", body="alpha gamma")
    index.rebuild("other", other)

    assert [hit.path.stem for hit in before] == ["one", "two"]
    assert index.query("birds", "alpha beta") == before
    index.remove_brain("other")
    assert index.query("birds", "alpha beta") == before


def test_a_second_instance_on_the_same_file_sees_the_rows(
    tmp_path: Path, sample_brain: Path
) -> None:
    db_path = tmp_path / "data" / "search.sqlite"
    SearchIndex(db_path).rebuild(BRAIN, sample_brain)

    assert paths(SearchIndex(db_path).query(BRAIN, "fieldnote")) == SAMPLE_PAGES


def test_a_new_instance_and_queries_do_not_wait_for_a_write_in_another_process(
    tmp_path: Path, sample_brain: Path
) -> None:
    db_path = tmp_path / "data" / "search.sqlite"
    SearchIndex(db_path).rebuild(BRAIN, sample_brain)
    opened: list[SearchIndex] = []
    thread = threading.Thread(target=lambda: opened.append(SearchIndex(db_path)), daemon=True)
    # The worker holds the write lock for the length of a large rebuild.
    with closing(sqlite3.connect(db_path, isolation_level=None)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            thread.start()
            thread.join(timeout=5)
            assert opened, "SearchIndex() waited for the write lock"
            assert paths(opened[0].query(BRAIN, "fieldnote")) == SAMPLE_PAGES
        finally:
            writer.execute("ROLLBACK")
            thread.join()


def test_an_index_file_with_another_schema_version_is_made_again(
    tmp_path: Path, sample_brain: Path
) -> None:
    db_path = tmp_path / "search.sqlite"
    with closing(sqlite3.connect(db_path)) as connection:
        # Tables like the ones of schema version 1: content, FTS5, and a trigger.
        connection.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, path TEXT)")
        connection.execute(
            "CREATE VIRTUAL TABLE documents_fts USING fts5(path, content = 'documents')"
        )
        connection.execute(
            "CREATE TRIGGER documents_after_insert AFTER INSERT ON documents BEGIN"
            " INSERT INTO documents_fts (rowid, path) VALUES (new.id, new.path); END"
        )
        connection.execute("INSERT INTO documents (path) VALUES ('wiki/old.md')")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        connection.commit()

    index = SearchIndex(db_path)
    index.rebuild(BRAIN, sample_brain)

    assert paths(index.query(BRAIN, "fieldnote")) == SAMPLE_PAGES
    with closing(sqlite3.connect(db_path)) as connection:
        names = {name for (name,) in connection.execute("SELECT name FROM sqlite_master")}
    assert not names & {"documents", "documents_fts", "documents_fts_data"}
