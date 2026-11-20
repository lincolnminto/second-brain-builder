"""Relocation preserves checkpoints only when rewritten Parts still match."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Literal
from unittest.mock import Mock

import pytest

from second_brain_builder.ingest import MapReduceState, PartNotes, split_into_chunks
from second_brain_builder.ingest.map_reduce_models import GroupFact, NoteGroup
from second_brain_builder.ingest.progress import (
    IngestProgress,
    document_progress,
    read_progress,
    write_progress,
)
from second_brain_builder.okf import Frontmatter, Page, SourceRef, read_page, write_page
from second_brain_builder.worker.clone import BrainClone
from second_brain_builder.worker.context import JobRun
from second_brain_builder.worker.handlers._relocate import moved_page, relocate


@pytest.mark.parametrize("mode", ["sequential", "map_reduce"])
@pytest.mark.parametrize("change", ["boundary", "count", "links_only"])
def test_relocation_rekeys_only_matching_parts(
    tmp_path: Path, mode: Literal["sequential", "map_reduce"], change: str
) -> None:
    old_dir = PurePosixPath("sources/a-very-long-folder-name")
    new_dir = PurePosixPath("sources/a")
    bird = old_dir / "bird/bird.md"
    canonical = PurePosixPath("sources/journal/notes/notes.md")
    link = f"[Bird](/{bird})"
    if change == "boundary":
        body = link + "x" * (64_005 - len(link)) + "claim" + "y" * 63_990
    elif change == "count":
        body = link + "x" * (64_010 - len(link))
    else:
        body = link + "x" * 40_000 + "\n\n" + "y" * 40_000
    root = tmp_path / "brain"
    for path, content in ((canonical, body), (bird, "Bird habitat.")):
        write_page(
            root,
            Page(path=path, frontmatter=Frontmatter(type="source", title=path.stem), body=content),
        )
    old_parts = split_into_chunks(body, max_chars=64_000)
    assert len(old_parts) == 2
    old_digest = hashlib.sha256((root / canonical).read_bytes()).hexdigest()
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    state = MapReduceState(
        canonical_sha256=old_digest,
        chunk_chars=64_000,
        parts_total=2,
        phase="reduce",
        map_parts_done=[1, 2],
        groups=[
            NoteGroup(
                key="bird",
                kind="entity",
                subtype="term",
                name="Bird",
                aliases=[],
                facts=[GroupFact(text="Bird habitat.", part_number=1)],
            )
        ],
        written=[bird],
        reduce_targets=[bird],
    )
    write_progress(
        root,
        "notes",
        IngestProgress(
            canonical_sha256=old_digest,
            chunk_chars=64_000,
            parts_total=2,
            parts_done=1 if mode == "sequential" else 2,
            mode=mode,
            phase="reduce" if mode == "map_reduce" else None,
            map_reduce_state=state if mode == "map_reduce" else None,
            written=[bird],
            updated_at=now,
        ),
    )
    directory = tmp_path / "data/ingest/brain/notes/map"
    directory.mkdir(parents=True)
    mapped = PartNotes(
        schema_version=2,
        canonical_sha256=old_digest,
        chunk_chars=64_000,
        part_number=1,
        items=[],
        summary_points=["Bird habitat."],
    )
    notes_path = directory / "part-0001.json"
    notes_path.write_text(mapped.model_dump_json())
    run = Mock(spec=JobRun)
    run.ctx = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path / "data"))
    clone = Mock(spec=BrainClone, root=root, brain_id="brain")
    clone.read_log.return_value = ""

    relocate(run, clone, {old_dir: new_dir})

    new_parts = split_into_chunks(read_page(root, canonical).body, max_chars=64_000)
    if change == "boundary":
        assert "claim" not in old_parts[0].text and "claim" in new_parts[0].text
        assert len(new_parts) == len(old_parts)
    elif change == "count":
        assert len(new_parts) == 1
    saved = read_progress(root, "notes")
    assert saved is not None
    new_digest = hashlib.sha256((root / canonical).read_bytes()).hexdigest()
    assert old_digest != new_digest
    expected_digest = new_digest if change == "links_only" else old_digest
    assert saved.canonical_sha256 == expected_digest
    assert saved.written == [new_dir / "bird/bird.md"]
    if saved.map_reduce_state is not None:
        assert saved.map_reduce_state.canonical_sha256 == expected_digest
        assert saved.map_reduce_state.written == saved.written
        assert saved.map_reduce_state.reduce_targets == saved.written
    assert PartNotes.model_validate_json(notes_path.read_text()).canonical_sha256 == expected_digest
    resumed = document_progress(root, canonical, 64_000, now)
    assert resumed.parts_done == (saved.parts_done if change == "links_only" else 0)


@pytest.mark.parametrize("suffix", ["#habitat", "?view=full", "?view=full#habitat", "#habitat?x"])
@pytest.mark.parametrize("relative", [False, True])
def test_frontmatter_references_move_paths_and_preserve_suffixes(
    suffix: str, relative: bool
) -> None:
    old_dir = PurePosixPath("sources/old")
    new_dir = PurePosixPath("sources/new")
    path = PurePosixPath("sources/old/notes/notes.md")
    resource = ("../bird/bird.md" if relative else "/sources/old/bird/bird.md") + suffix
    page = Page(
        path=path,
        frontmatter=Frontmatter(
            type="source",
            title="Notes",
            resource=resource,
            sources=[SourceRef(id="bird", resource=resource, title="Bird")],
        ),
        body="Notes.",
    )

    moved = moved_page(page, new_dir / "notes/notes.md", {old_dir: new_dir})

    assert moved.frontmatter is not None
    expected = ("../bird/bird.md" if relative else "/sources/new/bird/bird.md") + suffix
    assert moved.frontmatter.resource == expected
    assert moved.frontmatter.sources[0].resource == expected


@pytest.mark.parametrize("resource", ["#habitat", "?view=full"])
def test_frontmatter_reference_without_a_path_stays_unchanged(resource: str) -> None:
    path = PurePosixPath("sources/old/bird/bird.md")
    page = Page(
        path=path,
        frontmatter=Frontmatter(type="source", title="Bird", resource=resource),
        body="Bird.",
    )

    moved = moved_page(
        page,
        PurePosixPath("sources/new/bird/bird.md"),
        {PurePosixPath("sources/old"): PurePosixPath("sources/new")},
    )

    assert moved.frontmatter is not None
    assert moved.frontmatter.resource == resource
