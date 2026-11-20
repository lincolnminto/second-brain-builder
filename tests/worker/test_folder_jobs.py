"""folder.create, folder.rename, and folder.delete, with real git against local bare remotes."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.domain import JobStatus, SourceStatus
from second_brain_builder.ingest.progress import IngestProgress, read_progress, write_progress
from second_brain_builder.okf import (
    Frontmatter,
    Page,
    SourceRef,
    Stamp,
    bundle_href,
    read_page,
    validate_bundle,
    write_page,
)
from second_brain_builder.services import (
    ConflictError,
    FolderNode,
    create_folder,
    delete_folder,
    get_folder_tree,
    list_sources,
    rename_folder,
    retry_job,
    search,
    sync_brain,
)

TIDE_POOLS = PurePosixPath("sources/nature/tide-pools/tide-pools.md")
COASTAL_BIRDS = PurePosixPath("sources/nature/birds/coastal-birds/coastal-birds.md")
TRIP_NOTES = PurePosixPath("sources/journal/trip-notes/trip-notes.md")
NEW_TIDE_POOLS = PurePosixPath("sources/coast/shore/tide-pools/tide-pools.md")
NEW_COASTAL_BIRDS = PurePosixPath("sources/coast/shore/birds/coastal-birds/coastal-birds.md")
GREY_HERON = PurePosixPath("wiki/entities/grey-heron.md")
DUNE = PurePosixPath("wiki/entities/dune.md")
AT = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def errors(root: Path) -> list[str]:
    return [f"{i.path}: {i.message}" for i in validate_bundle(root) if i.severity == "error"]


def broken_links(root: Path) -> list[str]:
    return [i.message for i in validate_bundle(root) if i.code == "broken-link"]


def all_markdown(root: Path) -> str:
    return "\n".join(path.read_text() for path in root.rglob("*.md") if ".git" not in path.parts)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(node: FolderNode) -> dict[str, object]:
    """The Folder tree as nested dicts: ``{name: (source_count, children)}``."""
    return {child.name: (child.source_count, tree(child)) for child in node.children}


def seed_brain(harness) -> str:
    """Sources in ``nature``, ``nature/birds``, and ``journal``, and a Wiki Page that cites them.

    The Canonical Documents of tide-pools (in ``nature``) and trip-notes (outside
    it) link to coastal-birds, so a rename of ``nature`` changes their bytes. Both
    have the progress of a half-done Ingest.
    """
    brain_id = harness.create_brain()
    harness.seed_source(brain_id, "tide-pools", "Tide Pools", folder="nature")
    harness.seed_source(brain_id, "coastal-birds", "Coastal Birds", folder="nature/birds")
    harness.seed_source(brain_id, "trip-notes", "Trip Notes", folder="journal")
    clone = harness.fresh_clone(brain_id)
    for path in (TIDE_POOLS, TRIP_NOTES):
        page = read_page(clone, path)
        write_page(
            clone,
            page.model_copy(
                update={"body": page.body + f"\nSee also [Coastal Birds](/{COASTAL_BIRDS}).\n"}
            ),
        )
    write_page(
        clone,
        Page(
            path=GREY_HERON,
            frontmatter=Frontmatter(
                type="entity",
                title="Grey Heron",
                description="A wading bird.",
                sources=[
                    SourceRef(id="tide-pools", resource=f"/{TIDE_POOLS}", title="Tide Pools"),
                    SourceRef(
                        id="coastal-birds", resource=f"/{COASTAL_BIRDS}", title="Coastal Birds"
                    ),
                ],
                generated=Stamp(by="sbb/test-model", at=AT),
            ),
            body=(
                "# Grey Heron\n\nIt hunts in rock pools.[^tide-pools] It wades at dawn."
                "[^coastal-birds] See the [bird guide]"
                f"(../../{COASTAL_BIRDS}) and the [trip notes](/{TRIP_NOTES}).\n\n"
                f"[^tide-pools]: [Tide Pools](/{TIDE_POOLS})\n"
                f"[^coastal-birds]: [Coastal Birds](/{COASTAL_BIRDS})\n"
            ),
        ),
    )
    for slug, path in (("tide-pools", TIDE_POOLS), ("trip-notes", TRIP_NOTES)):
        write_progress(
            clone,
            slug,
            IngestProgress(
                canonical_sha256=digest(clone / path),
                chunk_chars=64000,
                parts_total=2,
                parts_done=1,
                updated_at=AT,
            ),
        )
    harness.git(clone, "add", "--all")
    harness.git(clone, "commit", "--quiet", "--message", "Seed pages")
    harness.git(clone, "push", "--quiet", "origin", "HEAD:main")
    sync_brain(harness.ctx, brain_id)
    harness.run_ok()
    return brain_id


def test_rename_moves_the_folder_rewrites_every_link_and_pushes(harness):
    brain_id = seed_brain(harness)
    old_digest = digest(harness.clone_path(brain_id) / TIDE_POOLS)
    old_trip_digest = digest(harness.clone_path(brain_id) / TRIP_NOTES)

    job = rename_folder(harness.ctx, brain_id, "nature", "Coast/Shore")
    harness.run_ok()

    assert job.payload == {"brain_id": brain_id, "folder": "nature", "new_folder": "coast/shore"}
    assert harness.commit_messages(brain_id)[0] == "Rename folder: nature to coast/shore"
    fresh = harness.fresh_clone(brain_id)
    assert not (fresh / "sources" / "nature").exists()
    assert (fresh / NEW_TIDE_POOLS).is_file()
    assert (fresh / NEW_COASTAL_BIRDS).is_file()
    assert (fresh / NEW_TIDE_POOLS.parent / "tide-pools.txt").is_file()
    assert "sources/nature" not in all_markdown(fresh)  # pages, index.md, and log.md
    heron = read_page(fresh, GREY_HERON)
    assert heron.frontmatter is not None
    assert [ref.resource for ref in heron.frontmatter.sources] == [
        f"/{NEW_TIDE_POOLS}",
        f"/{NEW_COASTAL_BIRDS}",
    ]
    assert f"[trip notes](/{TRIP_NOTES})" in heron.body  # outside the Folder: unchanged
    assert f"[Coastal Birds](/{NEW_COASTAL_BIRDS})" in read_page(fresh, NEW_TIDE_POOLS).body
    assert errors(fresh) == []
    assert broken_links(fresh) == []
    assert "* **Move**: Folder nature - renamed to coast/shore" in (fresh / "log.md").read_text()

    rows = {row.slug: row for row in list_sources(harness.ctx, brain_id)}
    assert {slug: (row.folder, row.path) for slug, row in rows.items()} == {
        "coastal-birds": ("coast/shore/birds", f"{NEW_COASTAL_BIRDS.parent}/coastal-birds.txt"),
        "tide-pools": ("coast/shore", f"{NEW_TIDE_POOLS.parent}/tide-pools.txt"),
        "trip-notes": ("journal", f"{TRIP_NOTES.parent}/trip-notes.txt"),
    }
    # The link rewrite changed the Canonical Documents, moved or not; progress follows them.
    progress = read_progress(fresh, "tide-pools")
    assert progress is not None
    assert progress.canonical_sha256 == digest(fresh / NEW_TIDE_POOLS) != old_digest
    assert (progress.parts_done, progress.parts_total) == (1, 2)
    assert rows["tide-pools"].status is SourceStatus.PARTLY_INGESTED
    assert f"[Coastal Birds](/{NEW_COASTAL_BIRDS})" in read_page(fresh, TRIP_NOTES).body
    trip_progress = read_progress(fresh, "trip-notes")
    assert trip_progress is not None
    assert trip_progress.canonical_sha256 == digest(fresh / TRIP_NOTES) != old_trip_digest

    assert tree(get_folder_tree(harness.ctx, brain_id)) == {
        "coast": (0, {"shore": (1, {"birds": (1, {})})}),
        "journal": (1, {}),
    }
    deleted = sorted(e.data["path"] for e in harness.events(job.id, "page_deleted"))
    written = {e.data["path"] for e in harness.events(job.id, "page_written")}
    assert deleted == [str(COASTAL_BIRDS), str(TIDE_POOLS)]
    assert {str(NEW_TIDE_POOLS), str(NEW_COASTAL_BIRDS), str(GREY_HERON)} <= written
    hits = search(harness.ctx, brain_id, "Tide Pools", types=["source"])
    assert [hit.path for hit in hits] == [str(NEW_TIDE_POOLS)]


def test_create_then_delete_an_empty_folder(harness):
    brain_id = harness.create_brain()

    job = create_folder(harness.ctx, brain_id, "Work/Field Drafts")
    harness.run_ok()

    assert job.payload == {"brain_id": brain_id, "folder": "work/field-drafts"}
    fresh = harness.fresh_clone(brain_id)
    assert (fresh / "sources/work/field-drafts/.gitkeep").read_bytes() == b""
    assert errors(fresh) == []
    assert tree(get_folder_tree(harness.ctx, brain_id)) == {"work": (0, {"field-drafts": (0, {})})}

    delete_folder(harness.ctx, brain_id, "work/field-drafts")
    harness.run_ok()

    fresh = harness.fresh_clone(brain_id)
    assert not (fresh / "sources/work/field-drafts").exists()
    assert (fresh / "sources/work/.gitkeep").is_file()  # the Folder above stays
    assert tree(get_folder_tree(harness.ctx, brain_id)) == {"work": (0, {})}
    # The empty markers stay plain files: no Git LFS normalization commit follows.
    assert harness.commit_messages(brain_id) == [
        "Delete folder: work/field-drafts",
        "Create folder: work/field-drafts",
        "Create brain: Shore Notes",
    ]


def test_rename_moves_empty_subfolders_and_keeps_the_folder_above(harness):
    brain_id = harness.create_brain()
    create_folder(harness.ctx, brain_id, "projects/old/drafts")
    harness.run_ok()

    rename_folder(harness.ctx, brain_id, "projects/old", "archive/old")
    harness.run_ok()

    fresh = harness.fresh_clone(brain_id)
    assert (fresh / "sources/archive/old/drafts/.gitkeep").is_file()
    assert (fresh / "sources/projects/.gitkeep").is_file()
    assert not (fresh / "sources/projects/old").exists()
    assert tree(get_folder_tree(harness.ctx, brain_id)) == {
        "archive": (0, {"old": (0, {"drafts": (0, {})})}),
        "projects": (0, {}),
    }


def test_delete_fails_when_a_source_arrived_after_the_job_was_queued(harness):
    brain_id = harness.create_brain()
    create_folder(harness.ctx, brain_id, "archive")
    harness.run_ok()
    delete_folder(harness.ctx, brain_id, "archive")
    harness.seed_source(brain_id, "late-notes", "Late Notes", folder="archive")
    head = harness.remote_head(brain_id)

    job = harness.run()

    assert job.status is JobStatus.FAILED
    assert job.error == "The Folder archive holds Sources (late-notes). Move or delete them first."
    assert harness.remote_head(brain_id) == head
    fresh = harness.fresh_clone(brain_id)
    assert (fresh / "sources/archive/.gitkeep").is_file()
    assert (fresh / "sources/archive/late-notes/late-notes.md").is_file()


def test_rename_onto_a_folder_that_appeared_meanwhile_fails_and_changes_nothing(harness):
    brain_id = seed_brain(harness)
    rename_folder(harness.ctx, brain_id, "journal", "diary")
    harness.seed_source(brain_id, "dream-log", "Dream Log", folder="diary")
    head = harness.remote_head(brain_id)

    job = harness.run()

    assert job.status is JobStatus.FAILED
    assert job.error == "The Folder diary exists already. Choose another name."
    assert harness.remote_head(brain_id) == head
    assert {row.folder for row in list_sources(harness.ctx, brain_id)} >= {"journal", "diary"}


def test_a_rename_that_makes_a_folder_too_deep_fails_also_on_retry(harness):
    brain_id = harness.create_brain()
    create_folder(harness.ctx, brain_id, "drafts")
    harness.run_ok()
    rename_folder(harness.ctx, brain_id, "drafts", "p/q/r/s/t")
    # Pushed from GitHub before the job runs: 9 levels after the rename.
    harness.seed_source(brain_id, "deep-notes", "Deep Notes", folder="drafts/a/b/c/d")
    head = harness.remote_head(brain_id)

    job = harness.run()

    assert job.status is JobStatus.FAILED
    assert job.error == (
        "A Folder can have at most 8 levels, also after the rename (p/q/r/s/t/a/b/c/d)."
    )
    assert harness.remote_head(brain_id) == head
    sync_brain(harness.ctx, brain_id)
    harness.run_ok()
    with pytest.raises(ConflictError, match="at most 8 levels"):
        retry_job(harness.ctx, job.id)
    assert harness.queued() == []


def test_rename_gives_a_folder_pushed_with_a_name_that_is_not_a_slug_a_slug_path(harness):
    brain_id = harness.create_brain()
    canonical = harness.seed_source(brain_id, "field-log", "Field Log", folder="Field Notes")
    clone = harness.fresh_clone(brain_id)
    write_page(
        clone,
        Page(
            path=DUNE,
            frontmatter=Frontmatter(
                type="entity",
                title="Dune",
                description="A hill of sand.",
                sources=[
                    SourceRef(id="field-log", resource=bundle_href(canonical), title="Field Log")
                ],
                generated=Stamp(by="sbb/test-model", at=AT),
            ),
            body=f"# Dune\n\nDunes move.[^field-log]\n\n"
            f"[^field-log]: [Field Log]({bundle_href(canonical)})\n",
        ),
    )
    harness.git(clone, "add", "--all")
    harness.git(clone, "commit", "--quiet", "--message", "Seed a page")
    harness.git(clone, "push", "--quiet", "origin", "HEAD:main")
    sync_brain(harness.ctx, brain_id)
    harness.run_ok()

    rename_folder(harness.ctx, brain_id, "Field Notes", "field-notes")
    harness.run_ok()

    fresh = harness.fresh_clone(brain_id)
    new_canonical = PurePosixPath("sources/field-notes/field-log/field-log.md")
    assert not (fresh / "sources" / "Field Notes").exists()
    assert (fresh / new_canonical).is_file()
    dune = read_page(fresh, DUNE)
    assert dune.frontmatter is not None
    assert [ref.resource for ref in dune.frontmatter.sources] == [f"/{new_canonical}"]
    assert f"[Field Log](/{new_canonical})" in dune.body
    assert broken_links(fresh) == []
    assert [row.folder for row in list_sources(harness.ctx, brain_id)] == ["field-notes"]
