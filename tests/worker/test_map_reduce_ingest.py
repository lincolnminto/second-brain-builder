"""Map-reduce jobs with real bare remotes, durable notes, and scripted models."""

import hashlib
import json
from threading import Barrier

import pytest

from second_brain_builder.domain import JobStatus, SourceStatus
from second_brain_builder.ingest import PartNotes
from second_brain_builder.ingest.progress import read_progress
from second_brain_builder.okf import read_page, write_page
from second_brain_builder.providers import ProviderError
from second_brain_builder.services import (
    delete_source,
    get_source,
    ingest_estimate,
    ingest_source,
    list_sources,
    reconvert_source,
    sync_brain,
)


@pytest.fixture
def book(harness):
    harness.ctx.settings = harness.ctx.settings.model_copy(
        update={
            "ingest_part_tokens": 100,
            "ingest_map_concurrency": 2,
            "ingest_map_reduce_min_parts": 4,
        }
    )
    brain = harness.create_brain()
    canonical = harness.seed_source(brain, "book", "Book")
    other = harness.fresh_clone(brain)
    page = read_page(other, canonical)
    page.body = "\n\n".join(f"## Section {n}\n\n" + "Coast. " * 40 for n in range(4))
    write_page(other, page)
    harness.git(other, "commit", "--quiet", "--all", "--message", "Long book")
    harness.git(other, "push", "--quiet", "origin", "HEAD:main")
    sync_brain(harness.ctx, brain)
    harness.run_ok()
    harness.enable_ingest()
    return brain, canonical


def notes(harness, count=0):
    return harness.answer(
        harness.call(
            "record_notes",
            items=[
                {
                    "kind": "entity",
                    "subtype": "place",
                    "name": f"Place {n:02d}",
                    "aliases": [],
                    "facts": [{"text": "On the coast."}],
                }
                for n in range(count)
            ],
            summary_points=["The coast."],
        )
    )


def summary(harness):
    return harness.answer(
        harness.write_wiki_page("summary", "book", "Book", "The coast.[^book]"),
        harness.call("finish"),
    )


def group_answers(harness, numbers):
    return [
        answer
        for n in numbers
        for answer in (
            harness.answer(harness.call("search", query=f"Place {n:02d}")),
            harness.answer(
                harness.write_wiki_page(
                    "entity", f"place-{n:02d}", f"Place {n:02d}", "On the coast.[^book]"
                ),
                harness.call("finish"),
            ),
        )
    ]


def maps(harness, brain):
    return harness.ctx.settings.data_dir / "ingest" / brain / "book" / "map"


@pytest.mark.parametrize("threshold,mode,requests", [(4, "map_reduce", 5), (5, "sequential", 4)])
def test_auto_threshold(harness, book, threshold, mode, requests):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(
        update={"ingest_map_reduce_min_parts": threshold}
    )
    answers = (
        [notes(harness)] * 4 + [summary(harness)]
        if mode == "map_reduce"
        else [summary(harness)] + [harness.answer(harness.call("finish"))] * 3
    )
    model = harness.script(*answers)
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.mode == mode
    assert saved.status == "complete"
    assert model.requests == requests
    head = harness.remote_head(brain)
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert harness.remote_head(brain) == head


def test_parallel_map_budget_and_resume_reuses_each_file(harness, book):
    brain, _ = book
    barrier = Barrier(2)
    model = harness.script(notes(harness), notes(harness), before=lambda _: barrier.wait(5))
    job = ingest_source(harness.ctx, brain, "book", max_tokens=350)
    harness.run_ok()
    assert model.requests == 2
    files = sorted(maps(harness, brain).glob("part-*.json"))
    assert len(files) == 2
    original = {path.name: path.read_bytes() for path in files}
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert (saved.phase, saved.map_parts_done, saved.parts_total) == ("map", 2, 4)
    assert list_sources(harness.ctx, brain)[0].phase == "map"
    assert get_source(harness.ctx, brain, "book").source.map_parts_done == 2
    assert harness.ctx.jobs.get(job.id).input_tokens == 200
    assert harness.events(job.id, "progress")[-1].data["map_parts_done"] == 2
    estimate = ingest_estimate(harness.ctx, brain, "book")
    assert estimate.mode == "map_reduce" and estimate.map_estimated_tokens > 0
    assert estimate.reduce_estimated_tokens > 0

    model = harness.script(notes(harness), notes(harness), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    completed = harness.run_ok()
    assert model.requests == 3
    assert len(list(maps(harness, brain).glob("part-*.json"))) == 4
    assert all(
        (maps(harness, brain) / name).read_bytes() == value for name, value in original.items()
    )
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.INGESTED
    assert (completed.input_tokens, completed.output_tokens) == (300, 150)
    assert ingest_estimate(harness.ctx, brain, "book").estimated_tokens == 0
    assert (harness.fresh_clone(brain) / "log.md").read_text().count("Map Parts") == 2


def test_changed_document_invalidates_all_stored_notes(harness, book):
    brain, canonical = book
    harness.script(notes(harness), notes(harness))
    ingest_source(harness.ctx, brain, "book", max_tokens=350)
    harness.run_ok()
    other = harness.fresh_clone(brain)
    page = read_page(other, canonical)
    page.body = page.body.replace("Coast", "Shore")
    write_page(other, page)
    digest = hashlib.sha256((other / canonical).read_bytes()).hexdigest()
    harness.git(other, "commit", "--quiet", "--all", "--message", "Change book")
    harness.git(other, "push", "--quiet", "origin", "HEAD:main")
    model = harness.script(*([notes(harness)] * 4), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert model.requests == 5
    assert all(
        PartNotes.model_validate_json(path.read_text()).canonical_sha256 == digest
        for path in maps(harness, brain).glob("*.json")
    )


@pytest.mark.parametrize(
    "field,value", [("schema_version", 2), ("chunk_chars", 401), ("schema_version", None)]
)
def test_invalid_cache_metadata_is_reextracted(harness, book, field, value):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(update={"ingest_map_concurrency": 1})
    harness.script(notes(harness))
    ingest_source(harness.ctx, brain, "book", max_tokens=200)
    harness.run_ok()
    path = maps(harness, brain) / "part-0001.json"
    data = json.loads(path.read_text())
    if value is None:
        data.pop(field)
    else:
        data[field] = value
    path.write_text(json.dumps(data))
    model = harness.script(*([notes(harness)] * 4), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert model.requests == 5


def test_reduce_budget_stops_at_group_boundary(harness, book):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(update={"ingest_map_concurrency": 1})
    # Complete map first, with no remaining run budget for reduce.
    harness.script(*([notes(harness, 2)] * 4))
    ingest_source(harness.ctx, brain, "book", max_tokens=600)
    harness.run_ok()
    model = harness.script(*group_answers(harness, [0]))
    ingest_source(harness.ctx, brain, "book", max_tokens=450)
    harness.run_ok()
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert (saved.phase, saved.reduce_groups_done, saved.reduce_groups_total) == ("reduce", 1, 2)
    assert model.requests == 2
    assert saved.last_stop == "token_budget"
    assert ingest_estimate(harness.ctx, brain, "book").reduce_estimated_tokens > 0
    model = harness.script(*group_answers(harness, [1]), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert model.requests == 3


def test_reduce_publishes_at_25_pages_and_keeps_checkpoint_after_failure(harness, book):
    brain, _ = book

    def before(number):
        # Four map calls, then two calls for each completed group.
        if number == 55:
            saved = read_progress(harness.fresh_clone(brain), "book")
            assert saved.reduce_groups_done == 25
            assert any(
                "(Part 25 of 27 pages)" in message for message in harness.commit_messages(brain)
            )

    harness.script(
        *([notes(harness, 27)] * 4),
        *group_answers(harness, range(25)),
        ProviderError("writer outage"),
        before=before,
    )
    ingest_source(harness.ctx, brain, "book")
    failed = harness.run()
    assert failed.status is JobStatus.FAILED and "writer outage" in failed.error
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.reduce_groups_done == 25
    model = harness.script(*group_answers(harness, [25, 26]), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert model.requests == 5
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.phase == "complete" and saved.reduce_groups_done == 27


@pytest.mark.parametrize("action", [delete_source, reconvert_source])
def test_source_removal_and_reconversion_remove_map_files(harness, book, action):
    brain, _ = book
    harness.script(notes(harness), notes(harness))
    ingest_source(harness.ctx, brain, "book", max_tokens=350)
    harness.run_ok()
    assert maps(harness, brain).is_dir()
    action(harness.ctx, brain, "book")
    harness.run_ok()
    assert not maps(harness, brain).exists()
