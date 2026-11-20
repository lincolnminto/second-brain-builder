"""Folder use cases: the tree with empty Folders, the checks before each Folder job, and
the Folder filter of the page list and the graph."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from second_brain_builder.domain import JobKind, SourceStatus
from second_brain_builder.services import (
    AppContext,
    ConflictError,
    FolderNode,
    InvalidInputError,
    NotFoundError,
    add_source,
    create_folder,
    delete_folder,
    get_folder_tree,
    get_graph,
    list_jobs,
    list_pages,
    move_source,
    rename_folder,
)
from second_brain_builder.store import SourceRow

if TYPE_CHECKING:
    from tests.services.conftest import AddSourceRow, BrainFixture

TRIP_NOTES = "sources/journal/2026/trip-notes/trip-notes.md"
LOG_BOOK = "wiki/entities/log-book.md"
NOTES = b"Field notes from a synthetic salt marsh.\n"


def tree(node: FolderNode) -> dict[str, object]:
    """The Folder tree as nested dicts: ``{name: (source_count, children)}``."""
    return {child.name: (child.source_count, tree(child)) for child in node.children}


def keep(brain: BrainFixture, folder: str) -> None:
    """An empty Folder in the clone, as ``folder.create`` leaves it."""
    directory = brain.root / "sources" / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".gitkeep").write_text("")


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


@pytest.fixture
def journal(brain: BrainFixture, add_source_row: AddSourceRow) -> BrainFixture:
    """The Brain with one more Source, in ``journal/2026``, and a Wiki Page that cites it."""
    write(
        brain.root,
        TRIP_NOTES,
        "---\ntype: source\ntitle: Trip Notes\nresource: ./trip-notes.txt\n"
        'generated: {by: "process:sbb-conversion", at: "2026-11-18T12:00:00+00:00"}\n'
        "---\n\n# Trip Notes\n\nA day on the tidal flat.\n",
    )
    write(
        brain.root,
        LOG_BOOK,
        "---\ntype: entity\ntitle: Log Book\nsources:\n  - id: trip-notes\n"
        f"    resource: /{TRIP_NOTES}\n"
        'generated: {by: sbb/test-model, at: "2026-11-18T12:05:00+00:00"}\n'
        "---\n\nThe log book lists the tides.[^trip-notes] It names the "
        "[Grey Heron](/wiki/entities/grey-heron.md).\n\n"
        f"[^trip-notes]: [Trip Notes](/{TRIP_NOTES})\n",
    )
    add_source_row(brain.id, "trip-notes", "journal/2026", sha256="3" * 64, title="Trip Notes")
    return brain


# --- the tree ------------------------------------------------------------------------------


def test_tree_lists_empty_folders_from_their_markers(
    ctx: AppContext, brain: BrainFixture, add_source_row: AddSourceRow
) -> None:
    add_source_row(brain.id, "dune", "books/sci-fi", sha256="1" * 64, title="Dune")
    keep(brain, "work/clients")
    keep(brain, "books")

    assert tree(get_folder_tree(ctx, brain.id)) == {
        "books": (0, {"sci-fi": (1, {})}),
        "nature": (2, {}),
        "work": (0, {"clients": (0, {})}),
    }


# --- create ------------------------------------------------------------------------------


def test_create_enqueues_the_folder_as_a_slug_path(ctx: AppContext, brain: BrainFixture) -> None:
    job = create_folder(ctx, brain.id, " Work / Client Notes ")

    assert (job.kind, job.payload) == (
        JobKind.FOLDER_CREATE,
        {"brain_id": brain.id, "folder": "work/client-notes"},
    )


@pytest.mark.parametrize("folder", ["", "/", "../outside", "work/../..", ".git", "work/.hidden"])
def test_create_rejects_the_root_traversal_and_dot_segments(
    ctx: AppContext, brain: BrainFixture, folder: str
) -> None:
    with pytest.raises(InvalidInputError):
        create_folder(ctx, brain.id, folder)

    assert list_jobs(ctx, brain.id) == []


def test_create_refuses_an_existing_folder_or_a_source_directory(
    ctx: AppContext, brain: BrainFixture
) -> None:
    keep(brain, "work")

    with pytest.raises(ConflictError, match="exists already"):
        create_folder(ctx, brain.id, "nature")
    with pytest.raises(ConflictError, match="exists already"):
        create_folder(ctx, brain.id, "Work")
    with pytest.raises(InvalidInputError, match="directory of Source 'tide-pools'"):
        create_folder(ctx, brain.id, "nature/tide-pools/notes")
    assert list_jobs(ctx, brain.id) == []


def test_a_pending_folder_job_blocks_overlapping_changes(
    ctx: AppContext, brain: BrainFixture
) -> None:
    create_folder(ctx, brain.id, "work/clients")

    with pytest.raises(ConflictError, match="pending already"):
        create_folder(ctx, brain.id, "work/clients")
    with pytest.raises(ConflictError, match="pending already"):
        rename_folder(ctx, brain.id, "nature", "work")
    assert create_folder(ctx, brain.id, "archive").kind is JobKind.FOLDER_CREATE


def test_unknown_brain_is_not_found(ctx: AppContext) -> None:
    with pytest.raises(NotFoundError):
        create_folder(ctx, "no-such-brain", "work")
    with pytest.raises(NotFoundError):
        get_folder_tree(ctx, "no-such-brain")


# --- rename ------------------------------------------------------------------------------


def test_rename_enqueues_both_folders_as_slug_paths(ctx: AppContext, brain: BrainFixture) -> None:
    job = rename_folder(ctx, brain.id, "nature", "Coast/Rocky Shore")

    assert (job.kind, job.payload) == (
        JobKind.FOLDER_RENAME,
        {"brain_id": brain.id, "folder": "nature", "new_folder": "coast/rocky-shore"},
    )


@pytest.mark.parametrize(
    ("folder", "new_folder", "error", "message"),
    [
        ("nature", "nature", InvalidInputError, "has this name already"),
        ("nature", "nature/inner", InvalidInputError, "into itself"),
        ("nature", "", InvalidInputError, "root"),
        ("nature", "../outside", InvalidInputError, "dot parts"),
        ("missing", "found", NotFoundError, "does not exist"),
        ("nature", "work", ConflictError, "exists already"),
        ("work", "nature/tide-pools", InvalidInputError, "directory of Source"),
        ("work", "a/b/c/d/e/f/g/h", InvalidInputError, "at most 8 levels"),
    ],
)
def test_rename_checks(
    ctx: AppContext,
    brain: BrainFixture,
    folder: str,
    new_folder: str,
    error: type[Exception],
    message: str,
) -> None:
    keep(brain, "work/clients")

    with pytest.raises(error, match=message):
        rename_folder(ctx, brain.id, folder, new_folder)

    assert list_jobs(ctx, brain.id) == []


def test_rename_waits_for_a_source_added_into_the_new_path(
    ctx: AppContext, brain: BrainFixture
) -> None:
    add_source(ctx, brain.id, "marsh.txt", io.BytesIO(NOTES), folder="coast/marsh")

    with pytest.raises(ConflictError, match="added to or moved into 'coast'"):
        rename_folder(ctx, brain.id, "nature", "coast")


def test_rename_counts_the_folders_of_pending_uploads_in_the_depth_limit(
    ctx: AppContext, brain: BrainFixture
) -> None:
    keep(brain, "work")
    add_source(ctx, brain.id, "marsh.txt", io.BytesIO(NOTES), folder="work/b/c/d/e/f/g/h")

    with pytest.raises(InvalidInputError, match="at most 8 levels"):
        rename_folder(ctx, brain.id, "work", "p/q")
    assert rename_folder(ctx, brain.id, "work", "archive").kind is JobKind.FOLDER_RENAME


def test_sources_wait_for_a_pending_rename_or_delete_of_their_folder(
    ctx: AppContext, journal: BrainFixture
) -> None:
    keep(journal, "work/clients")
    rename_folder(ctx, journal.id, "nature", "coast")
    delete_folder(ctx, journal.id, "work")

    with pytest.raises(ConflictError, match="'nature' is being renamed or deleted"):
        move_source(ctx, journal.id, "trip-notes", "nature")
    with pytest.raises(ConflictError, match="'work' is being renamed or deleted"):
        move_source(ctx, journal.id, "trip-notes", "work/clients")
    with pytest.raises(ConflictError, match="'nature' is being renamed or deleted"):
        add_source(ctx, journal.id, "marsh.txt", io.BytesIO(NOTES), folder="Nature/Marsh")
    # The new path is free: the rename runs before the move.
    assert move_source(ctx, journal.id, "trip-notes", "coast/trips").kind is JobKind.SOURCE_MOVE


# --- delete ------------------------------------------------------------------------------


def test_delete_enqueues_an_empty_folder(ctx: AppContext, brain: BrainFixture) -> None:
    keep(brain, "work/clients")

    job = delete_folder(ctx, brain.id, "work")

    assert (job.kind, job.payload) == (
        JobKind.FOLDER_DELETE,
        {"brain_id": brain.id, "folder": "work"},
    )


def test_delete_refuses_a_folder_with_sources(
    ctx: AppContext, brain: BrainFixture, add_source_row: AddSourceRow
) -> None:
    add_source_row(brain.id, "dune", "books/sci-fi", sha256="1" * 64, title="Dune")

    with pytest.raises(ConflictError, match="holds 2 Sources"):
        delete_folder(ctx, brain.id, "nature")
    with pytest.raises(ConflictError, match="holds 1 Source\\."):
        delete_folder(ctx, brain.id, "books")
    with pytest.raises(NotFoundError):
        delete_folder(ctx, brain.id, "missing")
    with pytest.raises(InvalidInputError):
        delete_folder(ctx, brain.id, "work/..")
    assert list_jobs(ctx, brain.id) == []


def test_delete_waits_for_a_source_added_into_the_folder(
    ctx: AppContext, brain: BrainFixture
) -> None:
    keep(brain, "work")
    add_source(ctx, brain.id, "marsh.txt", io.BytesIO(NOTES), folder="work/marsh")

    with pytest.raises(ConflictError, match="added to or moved into 'work'"):
        delete_folder(ctx, brain.id, "work")


# --- the Folder filter -----------------------------------------------------------------------


def test_pages_filter_keeps_the_folder_sources_and_the_pages_that_cite_them(
    ctx: AppContext, journal: BrainFixture
) -> None:
    pages = [page.path for page in list_pages(ctx, journal.id, folder="Journal")]
    entities = list_pages(ctx, journal.id, types=["entity"], folder="journal/2026")
    everything = list_pages(ctx, journal.id)

    assert pages == [TRIP_NOTES, LOG_BOOK]
    assert [page.path for page in entities] == [LOG_BOOK]
    assert [page.path for page in list_pages(ctx, journal.id, folder="")] == [
        page.path for page in everything
    ]
    nature = {page.path for page in list_pages(ctx, journal.id, folder="nature")}
    assert LOG_BOOK not in nature
    assert "wiki/entities/grey-heron.md" in nature


def test_graph_filter_keeps_the_links_between_the_kept_pages(
    ctx: AppContext, journal: BrainFixture
) -> None:
    full = get_graph(ctx, journal.id)
    graph = get_graph(ctx, journal.id, folder="journal")

    assert [node.path for node in graph.nodes] == [TRIP_NOTES, LOG_BOOK]
    assert [(edge.from_path, edge.to_path) for edge in graph.edges] == [(LOG_BOOK, TRIP_NOTES)]
    assert (LOG_BOOK, "wiki/entities/grey-heron.md") in {
        (edge.from_path, edge.to_path) for edge in full.edges
    }
    # The cache keeps one graph per Folder.
    assert get_graph(ctx, journal.id) == full
    assert get_graph(ctx, journal.id, folder="journal") == graph


def test_a_folder_pushed_with_a_name_that_is_not_a_slug_can_be_filtered_and_renamed(
    ctx: AppContext, brain: BrainFixture
) -> None:
    field_log = "sources/Field Notes/field-log/field-log.md"
    write(
        brain.root,
        field_log,
        "---\ntype: source\ntitle: Field Log\nresource: ./field-log.txt\n"
        'generated: {by: "process:sbb-conversion", at: "2026-11-18T12:00:00+00:00"}\n'
        "---\n\n# Field Log\n\nA day on the dunes.\n",
    )
    with ctx.sessions.begin() as session:  # the row of a Source that a sync found
        session.add(
            SourceRow(
                brain_id=brain.id,
                slug="field-log",
                folder="Field Notes",
                path="sources/Field Notes/field-log/field-log.txt",
                sha256="4" * 64,
                title="Field Log",
                media_type="text/plain",
                size_bytes=100,
                status=SourceStatus.CONVERTED,
            )
        )

    assert [page.path for page in list_pages(ctx, brain.id, folder="Field Notes")] == [field_log]
    assert [node.path for node in get_graph(ctx, brain.id, folder="Field Notes").nodes] == [
        field_log
    ]
    with pytest.raises(ConflictError, match="holds 1 Source"):
        delete_folder(ctx, brain.id, "Field Notes")
    job = rename_folder(ctx, brain.id, "Field Notes", "field-notes")
    assert job.payload == {
        "brain_id": brain.id,
        "folder": "Field Notes",
        "new_folder": "field-notes",
    }


@pytest.mark.parametrize("folder", ["../outside", "nature/..", ".brain"])
def test_filters_reject_traversal_and_dot_segments(
    ctx: AppContext, brain: BrainFixture, folder: str
) -> None:
    with pytest.raises(InvalidInputError):
        list_pages(ctx, brain.id, folder=folder)
    with pytest.raises(InvalidInputError):
        get_graph(ctx, brain.id, folder=folder)
