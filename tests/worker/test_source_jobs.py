"""source.add, source.ingest, source.reconvert, source.move, source.delete, and answer.save.

Conversion runs real Docling on small synthetic HTML and Markdown Sources. Ingest
uses a scripted ChatModel; everything else (git, OKF, search, the database) is real.
"""

from __future__ import annotations

import io
import os
import shutil
import wave
from pathlib import Path, PurePosixPath

import httpx2
import pytest
from pydantic import SecretStr

from second_brain_builder.domain import JobKind, JobStatus, SourceStatus, TaskMode
from second_brain_builder.ingest.progress import read_progress
from second_brain_builder.okf import read_page, validate_bundle, write_page
from second_brain_builder.providers import ProviderKind
from second_brain_builder.services import (
    AnswerInput,
    ProviderInput,
    TaskSettingInput,
    TaskSettingsInput,
    create_provider,
    delete_source,
    ingest_source,
    list_sources,
    move_source,
    put_task_settings,
    reconvert_source,
    save_answer,
    search,
    sync_brain,
)

TIDE = "sources/nature/tide-pools"
TIDE_CANONICAL = PurePosixPath(f"{TIDE}/tide-pools.md")
SUMMARY = PurePosixPath("wiki/summaries/tide-pools.md")
SEA_STAR = PurePosixPath("wiki/entities/sea-star.md")
HERMIT_CRAB = PurePosixPath("wiki/entities/hermit-crab.md")
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"


def errors(root: Path) -> list[str]:
    return [f"{i.path}: {i.message}" for i in validate_bundle(root) if i.severity == "error"]


def broken_links(root: Path) -> list[str]:
    return [i.message for i in validate_bundle(root) if i.code == "broken-link"]


def all_markdown(root: Path) -> str:
    return "\n".join(path.read_text() for path in root.rglob("*.md") if ".git" not in path.parts)


@pytest.fixture
def ingested(harness) -> str:
    """A Brain with the Source tide-pools (HTML with an image) added and ingested."""
    brain_id = harness.create_brain()
    harness.enable_ingest()
    harness.script_ingest(
        "tide-pools", "Tide Pools", [("sea-star", "Ochre Sea Star"), ("hermit-crab", "Hermit Crab")]
    )
    harness.add_file(brain_id, "Tide Pools.html", harness.tide_pools_html, folder="nature")
    harness.run_ok()  # source.add
    harness.run_ok()  # source.ingest
    return brain_id


def test_add_then_ingest_gives_a_valid_okf_bundle_on_the_remote(harness, lfs):
    brain_id = harness.create_brain()
    harness.enable_ingest()
    model = harness.script_ingest("tide-pools", "Tide Pools", [("sea-star", "Ochre Sea Star")])
    staged = harness.add_file(brain_id, "Tide Pools.html", harness.tide_pools_html, "nature")

    add_job = harness.run_ok()

    assert [job.kind for job in harness.queued()] == [JobKind.SOURCE_INGEST]
    assert not any((harness.ctx.settings.uploads_dir).iterdir())  # staged upload deleted
    written = [e.data for e in harness.events(add_job.id, "page_written")]
    assert [(d["path"], d["type"]) for d in written] == [(str(TIDE_CANONICAL), "source")]
    [row] = list_sources(harness.ctx, brain_id)
    assert (row.slug, row.folder, row.status) == ("tide-pools", "nature", SourceStatus.CONVERTED)
    assert (row.sha256, row.media_type) == (staged.sha256, "text/html")
    assert row.path == f"{TIDE}/tide-pools.html"

    ingest_job = harness.run_ok()

    assert model.requests == 2
    assert (ingest_job.input_tokens, ingest_job.output_tokens) == (200, 100)
    assert [e.data["path"] for e in harness.events(ingest_job.id, "page_written")] == [
        str(SUMMARY),
        str(SEA_STAR),
    ]
    assert list_sources(harness.ctx, brain_id)[0].status is SourceStatus.INGESTED
    assert harness.commit_messages(brain_id) == [
        "Ingest: Tide Pools of the Example Coast (Parts 1\u20131 of 1)",
        "Add source: Tide Pools of the Example Coast",
        "Create brain: Shore Notes",
    ]

    fresh = harness.fresh_clone(brain_id)
    assert errors(fresh) == []
    assert broken_links(fresh) == []
    canonical = read_page(fresh, TIDE_CANONICAL)
    assert canonical.frontmatter is not None
    assert canonical.frontmatter.resource == "./tide-pools.html"
    assert canonical.frontmatter.generated is not None
    assert canonical.frontmatter.generated.by == "process:sbb-conversion"
    facts = canonical.frontmatter.extensions["x-sbb-source"]
    assert facts["original_filename"] == "Tide Pools.html"
    assert facts["sha256"] == staged.sha256
    assert facts["converter"].startswith("docling ")
    assert "ochre sea star" in canonical.body
    [image] = list((fresh / TIDE / "assets").iterdir())
    assert f"(assets/{image.name})" in canonical.body
    index = (fresh / "index.md").read_text()
    for path in (TIDE_CANONICAL, SUMMARY, SEA_STAR):
        assert f"(/{path})" in index
    log = (fresh / "log.md").read_text()
    assert (
        f"* **Ingest**: [Tide Pools of the Example Coast](/{TIDE_CANONICAL})"
        " - Parts 1\u20131 of 1, 2 pages" in log
    )
    assert f"* **Add**: [Tide Pools of the Example Coast](/{TIDE_CANONICAL})" in log
    if lfs:  # the Source is in Git LFS; the Canonical Document and Assets are not
        assert (fresh / TIDE / "tide-pools.html").read_bytes().startswith(LFS_POINTER)
        assert image.read_bytes().startswith(b"\x89PNG")
    hits = search(harness.ctx, brain_id, "Ochre Sea Star", types=["entity"])
    assert [hit.path for hit in hits] == [str(SEA_STAR)]


def test_budget_stops_partially_and_next_run_completes_remaining_parts(harness):
    brain_id = harness.create_brain()
    canonical = harness.seed_source(brain_id, "survey", "Survey")
    other = harness.fresh_clone(brain_id)
    page = read_page(other, canonical)
    page.body = "# Survey\n\n" + "Alpha pools. " * 3000 + "\n\n## Beta\n\n" + "Beta pools. " * 3000
    write_page(other, page)
    harness.git(other, "commit", "--quiet", "--all", "--message", "Long survey")
    harness.git(other, "push", "--quiet", "origin", "HEAD:main")
    sync_brain(harness.ctx, brain_id)
    harness.run_ok()
    harness.enable_ingest()
    harness.script_ingest("survey", "Survey", [])
    ingest_source(harness.ctx, brain_id, "survey", max_tokens=300)
    harness.run_ok()
    assert list_sources(harness.ctx, brain_id)[0].status is SourceStatus.PARTLY_INGESTED
    partial = read_progress(harness.fresh_clone(brain_id), "survey")
    assert partial is not None
    assert (partial.parts_done, partial.parts_total) == (1, 2)

    harness.script(harness.answer(harness.call("finish", note="Done.")))
    ingest_source(harness.ctx, brain_id, "survey")
    harness.run_ok()
    assert list_sources(harness.ctx, brain_id)[0].status is SourceStatus.INGESTED
    complete = read_progress(harness.fresh_clone(brain_id), "survey")
    assert complete is not None
    assert complete.parts_done == complete.parts_total == 2
    assert complete.usage.input_tokens + complete.usage.output_tokens == 450
    assert "Parts 2\u20132 of 2" in harness.commit_messages(brain_id)[0]


def test_markdown_source_is_kept_as_original_markdown_and_needs_no_ingest_provider(harness):
    brain_id = harness.create_brain()
    harness.add_file(brain_id, "Coastal Birds.md", harness.coastal_birds_md)

    harness.run_ok()

    assert harness.queued() == []  # no ingest Provider: nothing to enqueue
    fresh = harness.fresh_clone(brain_id)
    source_dir = fresh / "sources" / "coastal-birds"
    assert sorted(path.name for path in source_dir.iterdir()) == [
        "coastal-birds.md",
        "coastal-birds.original.markdown",
    ]
    page = read_page(fresh, PurePosixPath("sources/coastal-birds/coastal-birds.md"))
    assert page.frontmatter is not None
    assert page.frontmatter.resource == "./coastal-birds.original.markdown"
    assert "Grey herons wade in the shallows" in page.body
    assert errors(fresh) == []


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are not installed",
)
def test_audio_conversion_uses_the_configured_provider_and_models_dir(harness, monkeypatch):
    brain_id = harness.create_brain()
    provider = create_provider(
        harness.ctx,
        ProviderInput(
            name="Speech Provider",
            kind=ProviderKind.OPENAI,
            base_url="https://speech.example.com/v1",
            api_key=SecretStr("synthetic-speech-key"),
        ),
    )
    put_task_settings(
        harness.ctx,
        TaskSettingsInput(
            transcription=TaskSettingInput(
                mode=TaskMode.PROVIDER, provider_id=provider.id, model="speech-test"
            )
        ),
    )
    requests = []

    def transcribe(request):
        assert os.environ["HF_HOME"] == str(harness.ctx.settings.models_dir)
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer synthetic-speech-key"
        assert b"speech-test" in request.read()
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "text": "Sea stars live in pools.",
                "duration": 1.0,
                "segments": [{"start": 0.0, "end": 1.0, "text": "Sea stars live in pools."}],
            },
        )

    harness.ctx.provider_transport = httpx2.MockTransport(transcribe)
    monkeypatch.setenv("HF_HOME", str(harness.tmp / "wrong-models"))
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    harness.add_file(brain_id, "Shore Talk.wav", audio.getvalue())

    harness.run_ok()

    assert len(requests) == 1
    fresh = harness.fresh_clone(brain_id)
    canonical = read_page(fresh, PurePosixPath("sources/shore-talk/shore-talk.md"))
    assert "[shore-talk.vtt](assets/shore-talk.vtt)" in canonical.body
    assert "Sea stars live in pools." in canonical.body
    assert (
        "Sea stars live in pools."
        in (fresh / "sources/shore-talk/assets/shore-talk.vtt").read_text()
    )
    assert errors(fresh) == []
    assert not any(harness.ctx.settings.uploads_dir.iterdir())


def test_move_rewrites_every_link_and_resource_to_the_new_folder(harness, ingested):
    job = move_source(harness.ctx, ingested, "tide-pools", "Coast/Rocky Shore")
    harness.run_ok()

    new_dir = "sources/coast/rocky-shore/tide-pools"
    fresh = harness.fresh_clone(ingested)
    assert not (fresh / "sources" / "nature").exists()
    assert sorted(p.name for p in (fresh / new_dir).iterdir()) == [
        "assets",
        "tide-pools.html",
        "tide-pools.md",
    ]
    text = all_markdown(fresh)
    assert "sources/nature" not in text  # pages, index.md, and log.md
    summary = read_page(fresh, SUMMARY)
    assert summary.frontmatter is not None
    assert [ref.resource for ref in summary.frontmatter.sources] == [f"/{new_dir}/tide-pools.md"]
    assert f"[^tide-pools]: [Tide Pools of the Example Coast](/{new_dir}/tide-pools.md)" in (
        summary.body
    )
    assert errors(fresh) == []
    assert broken_links(fresh) == []
    assert "* **Move**: [Tide Pools of the Example Coast]" in (fresh / "log.md").read_text()
    assert harness.commit_messages(ingested)[0] == "Move source: Tide Pools of the Example Coast"
    [row] = list_sources(harness.ctx, ingested)
    assert (row.folder, row.path) == ("coast/rocky-shore", f"{new_dir}/tide-pools.html")
    assert row.status is SourceStatus.INGESTED
    deleted = [e.data["path"] for e in harness.events(job.id, "page_deleted")]
    written = {e.data["path"] for e in harness.events(job.id, "page_written")}
    assert deleted == [str(TIDE_CANONICAL)]
    assert {f"{new_dir}/tide-pools.md", str(SUMMARY), str(SEA_STAR)} <= written
    assert [
        hit.path for hit in search(harness.ctx, ingested, "ochre sea star", types=["source"])
    ] == [f"{new_dir}/tide-pools.md"]


def test_move_into_the_directory_of_a_source_fails_and_changes_nothing(harness, ingested):
    head = harness.remote_head(ingested)

    move_source(harness.ctx, ingested, "tide-pools", "nature/tide-pools")
    job = harness.run()

    assert job.status is JobStatus.FAILED
    assert job.error == "nature/tide-pools is in the directory of a Source, not a Folder."
    assert harness.remote_head(ingested) == head
    assert list_sources(harness.ctx, ingested)[0].folder == "nature"


def test_delete_removes_the_source_its_orphan_pages_and_its_citations(harness, ingested):
    assert (harness.clone_path(ingested) / ".brain/ingest/tide-pools.yaml").is_file()
    harness.add_file(ingested, "Coastal Birds.md", harness.coastal_birds_md, folder="nature")
    harness.script_ingest("coastal-birds", "Coastal Birds", [])
    harness.run_ok()  # source.add
    harness.run_ok()  # source.ingest
    save_answer(
        harness.ctx,
        ingested,
        AnswerInput(
            title="Who lives on the shore?",
            body="Sea stars live in pools.[^tide-pools] Herons wade nearby.[^coastal-birds]\n",
            source_ids=["tide-pools", "coastal-birds"],
            description="Animals of the shore.",
        ),
    )
    harness.run_ok()  # answer.save
    synthesis = PurePosixPath("wiki/syntheses/who-lives-on-the-shore.md")

    job = delete_source(harness.ctx, ingested, "tide-pools")
    harness.run_ok()

    fresh = harness.fresh_clone(ingested)
    assert not (fresh / "sources" / "nature" / "tide-pools").exists()
    assert not (fresh / ".brain/ingest/tide-pools.yaml").exists()
    assert not (fresh / ".brain/ingest/tide-pools.yaml").exists()
    for gone in (SUMMARY, SEA_STAR, HERMIT_CRAB):
        assert not (fresh / gone).exists()
    kept = read_page(fresh, synthesis)
    assert kept.frontmatter is not None
    assert [ref.id for ref in kept.frontmatter.sources] == ["coastal-birds"]
    assert "[^tide-pools]" not in kept.body
    assert (fresh / "wiki/summaries/coastal-birds.md").is_file()
    assert "tide-pools" not in (fresh / "index.md").read_text()
    log = (fresh / "log.md").read_text()
    assert "* **Delete**: Tide Pools of the Example Coast - 3 pages deleted, 1 page updated" in log
    assert errors(fresh) == []
    assert broken_links(fresh) == []
    assert harness.commit_messages(ingested)[0] == "Delete source: Tide Pools of the Example Coast"
    assert [row.slug for row in list_sources(harness.ctx, ingested)] == ["coastal-birds"]
    deleted = {e.data["path"] for e in harness.events(job.id, "page_deleted")}
    assert deleted == {str(SUMMARY), str(SEA_STAR), str(HERMIT_CRAB), str(TIDE_CANONICAL)}
    assert search(harness.ctx, ingested, "Ochre Sea Star") == []


def test_reconvert_writes_the_canonical_document_again_and_keeps_the_added_facts(harness, ingested):
    clone = harness.clone_path(ingested)
    assert (clone / ".brain/ingest/tide-pools.yaml").is_file()
    before = read_page(clone, TIDE_CANONICAL)
    assert before.frontmatter is not None
    # Someone damaged the Canonical Document on GitHub.
    other = harness.fresh_clone(ingested)
    (other / TIDE_CANONICAL).write_text(
        (other / TIDE_CANONICAL).read_text().replace("hermit crabs", "XXXX")
    )
    harness.git(other, "commit", "--quiet", "--all", "--message", "Edit by hand")
    harness.git(other, "push", "--quiet", "origin", "HEAD:main")

    reconvert_source(harness.ctx, ingested, "tide-pools")
    harness.run_ok()

    after = read_page(clone, TIDE_CANONICAL)
    assert not (clone / ".brain/ingest/tide-pools.yaml").exists()
    assert after.frontmatter is not None
    assert "hermit crabs" in after.body
    old_facts = before.frontmatter.extensions["x-sbb-source"]
    new_facts = after.frontmatter.extensions["x-sbb-source"]
    assert new_facts["added_at"] == old_facts["added_at"]
    assert new_facts["original_filename"] == "Tide Pools.html"
    assert (
        harness.commit_messages(ingested)[0] == "Reconvert source: Tide Pools of the Example Coast"
    )
    assert list_sources(harness.ctx, ingested)[0].status is SourceStatus.INGESTED
    assert len(list((clone / TIDE / "assets").iterdir())) == 1
    assert errors(harness.fresh_clone(ingested)) == []


def test_answer_with_an_unknown_source_fails_without_a_commit(harness, ingested):
    head = harness.remote_head(ingested)
    harness.ctx.jobs.enqueue(
        JobKind.ANSWER_SAVE,
        ingested,
        {
            "brain_id": ingested,
            "title": "A guess",
            "body": "Whales visit.[^whales]\n",
            "source_ids": ["whales"],
            "description": "",
            "tags": [],
        },
    )

    job = harness.run()

    assert job.status is JobStatus.FAILED
    assert job.error is not None and job.error.startswith("The answer cannot be saved: ")
    assert "whales" in job.error
    assert harness.remote_head(ingested) == head
