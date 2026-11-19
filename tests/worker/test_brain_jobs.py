"""brain.create and brain.sync, with real git against local bare remotes."""

from __future__ import annotations

import json
from pathlib import Path

from second_brain_builder.brains import read_settings
from second_brain_builder.domain import BrainStatus, JobStatus, SourceStatus
from second_brain_builder.okf import validate_bundle
from second_brain_builder.services import (
    BrainCreate,
    create_brain,
    disconnect_brain,
    get_brain,
    list_sources,
    search,
    sync_brain,
)

BRAIN_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "index.md",
    "log.md",
    ".gitattributes",
    ".brain/brain.yaml",
]


def errors(root: Path) -> list[str]:
    return [f"{i.path}: {i.message}" for i in validate_bundle(root) if i.severity == "error"]


def test_brain_create_makes_the_private_repo_scaffolds_and_pushes(harness, lfs):
    created = create_brain(
        harness.ctx, BrainCreate(name="Shore Notes", language="pt-BR", new_repo_name="shore-notes")
    )

    job = harness.run_ok()

    create_requests = [r for r in harness.github.requests if r.method == "POST"]
    assert [json.loads(r.content) for r in create_requests] == [
        {"name": "shore-notes", "description": "Second Brain: Shore Notes", "private": True}
    ]
    brain = get_brain(harness.ctx, created.brain.id)
    assert brain.status is BrainStatus.READY
    assert brain.last_synced_at is not None
    assert harness.commit_messages(brain.id) == ["Create brain: Shore Notes"]
    author = harness.git(harness.remote(brain.id), "log", "-1", "--format=%an <%ae>", "main")
    assert author == f"test-user <{harness.noreply}>"

    fresh = harness.fresh_clone(brain.id)
    assert all((fresh / name).is_file() for name in BRAIN_FILES)
    settings = read_settings(fresh)
    assert (settings.name, settings.language) == ("Shore Notes", "pt-BR")
    assert errors(fresh) == []
    assert "**Create**: Shore Notes" in (fresh / "log.md").read_text()

    clone = harness.clone_path(brain.id)
    assert harness.git(clone, "status", "--porcelain") == ""
    if lfs:  # git lfs install --local, so LFS works without a global git config
        assert harness.git(clone, "config", "--local", "filter.lfs.process") != ""
    assert [e.data for e in harness.events(job.id, "progress")][:2] == [
        {"step": "create_repo"},
        {"step": "clone"},
    ]


def test_brain_create_connects_a_repository_that_is_a_brain_already(harness):
    brain_id = harness.create_brain()
    harness.seed_source(brain_id, "tide-pools", "Tide Pools")
    head = harness.remote_head(brain_id)
    repo = harness.repo(brain_id)
    disconnect_brain(harness.ctx, brain_id)

    created = create_brain(harness.ctx, BrainCreate(name="Shore Notes", existing_repo=repo))
    harness.run_ok()

    assert harness.remote_head(created.brain.id) == head  # nothing was written
    assert get_brain(harness.ctx, created.brain.id).status is BrainStatus.READY
    sources = list_sources(harness.ctx, created.brain.id)
    assert [(s.slug, s.folder, s.title, s.status) for s in sources] == [
        ("tide-pools", "nature", "Tide Pools", SourceStatus.CONVERTED)
    ]
    hits = search(harness.ctx, created.brain.id, "synthetic coast")
    assert [hit.path for hit in hits] == ["sources/nature/tide-pools/tide-pools.md"]


def test_brain_create_of_a_repository_with_brain_files_fails_and_marks_the_brain(harness):
    remote = harness.github.add_repo("test-user/old-notes")
    other = harness.tmp / "other"
    harness.git(harness.tmp, "clone", "--quiet", str(remote), str(other))
    (other / "index.md").write_text("# My own index\n")
    harness.git(other, "add", "--all")
    harness.git(other, "commit", "--quiet", "--message", "Add index")
    harness.git(other, "push", "--quiet", "origin", "HEAD:main")

    created = create_brain(
        harness.ctx, BrainCreate(name="Old Notes", existing_repo="test-user/old-notes")
    )
    job = harness.run()

    assert job.status is JobStatus.FAILED
    assert job.error is not None and "index.md" in job.error
    assert get_brain(harness.ctx, created.brain.id).status is BrainStatus.ERROR
    assert harness.commit_messages(created.brain.id) == ["Add index"]


def test_brain_sync_pulls_and_rebuilds_the_rows_and_the_search_index(harness):
    brain_id = harness.create_brain()
    harness.seed_source(brain_id, "tide-pools", "Tide Pools")
    harness.seed_source(brain_id, "coastal-birds", "Coastal Birds", folder="nature/birds")

    sync_brain(harness.ctx, brain_id)
    harness.run_ok()

    sources = list_sources(harness.ctx, brain_id)
    assert [(s.slug, s.folder, s.path) for s in sources] == [
        ("tide-pools", "nature", "sources/nature/tide-pools/tide-pools.txt"),
        ("coastal-birds", "nature/birds", "sources/nature/birds/coastal-birds/coastal-birds.txt"),
    ]
    assert [hit.path for hit in search(harness.ctx, brain_id, "Coastal Birds")] == [
        "sources/nature/birds/coastal-birds/coastal-birds.md"
    ]
    assert harness.git(harness.clone_path(brain_id), "rev-parse", "HEAD") == harness.remote_head(
        brain_id
    )
