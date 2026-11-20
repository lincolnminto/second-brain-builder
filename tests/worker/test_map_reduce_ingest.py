"""Map-reduce jobs with real bare remotes, durable notes, and scripted models."""

import hashlib
import json
import re
from threading import Barrier, Lock, RLock

import pytest

from second_brain_builder.domain import JobStatus, SourceStatus
from second_brain_builder.ingest import MapReduceState, PartNotes
from second_brain_builder.ingest.progress import (
    document_progress,
    progress_path,
    read_progress,
    write_progress,
)
from second_brain_builder.okf import read_page, write_page
from second_brain_builder.providers import ProviderError, Usage
from second_brain_builder.services import (
    cancel_job,
    delete_source,
    get_source,
    ingest_estimate,
    ingest_source,
    list_sources,
    move_source,
    reconvert_source,
    sync_brain,
)
from second_brain_builder.worker.context import JobRun
from second_brain_builder.worker.handlers import source_map_reduce


@pytest.fixture
def book(harness):
    harness.ctx.settings = harness.ctx.settings.model_copy(
        update={
            "ingest_part_tokens": 100,
            "ingest_map_concurrency": 2,
            "ingest_reduce_concurrency": 1,
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
        harness.answer(
            harness.write_wiki_page(
                "entity", f"place-{n:02d}", f"Place {n:02d}", "On the coast.[^book]"
            ),
        )
        for n in numbers
    ]


def maps(harness, brain):
    return harness.ctx.settings.data_dir / "ingest" / brain / "book" / "map"


def test_parallel_map_saves_and_usage_share_lock(harness, book, monkeypatch):
    brain, _ = book
    lock = RLock()
    monkeypatch.setattr(source_map_reduce, "RLock", lambda: lock, raising=False)
    original_write = source_map_reduce.write_progress
    original_notes = source_map_reduce._save_notes
    original_usage = harness.ctx.jobs.update_usage
    saves = []

    def write(root, slug, progress):
        assert lock._is_owned()
        assert progress.usage == progress.map_reduce_state.usage
        saves.append(progress.usage.input_tokens)
        original_write(root, slug, progress)

    def save_notes(directory, notes):
        assert lock._is_owned()
        original_notes(directory, notes)

    def update(*args, **kwargs):
        assert lock._is_owned()
        return original_usage(*args, **kwargs)

    monkeypatch.setattr(source_map_reduce, "write_progress", write)
    monkeypatch.setattr(source_map_reduce, "_save_notes", save_notes)
    monkeypatch.setattr(harness.ctx.jobs, "update_usage", update)
    barrier = Barrier(2)
    harness.script(notes(harness), notes(harness), before=lambda _: barrier.wait(10))
    ingest_source(harness.ctx, brain, "book", max_tokens=350)
    harness.run_ok()
    assert saves and saves == sorted(saves)
    assert read_progress(harness.fresh_clone(brain), "book").usage.input_tokens == 200


@pytest.mark.parametrize("mode", ["map_reduce", "sequential"])
def test_stop_before_first_part_keeps_converted_source(harness, book, monkeypatch, mode):
    brain, _ = book
    head = harness.remote_head(brain)
    model = harness.script()
    monkeypatch.setattr(JobRun, "stop_requested", lambda self: True)
    ingest_source(harness.ctx, brain, "book", mode=mode)
    harness.run()
    assert model.requests == 0
    assert harness.remote_head(brain) == head
    assert read_progress(harness.clone_path(brain), "book") is None
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.CONVERTED
    assert "Parts 1\u20130" not in (harness.fresh_clone(brain) / "log.md").read_text()


def test_uncommitted_maps_recover_usage_once_after_restart(harness, book):
    brain, canonical = book
    root = harness.clone_path(brain)
    digest = hashlib.sha256((root / canonical).read_bytes()).hexdigest()
    directory = maps(harness, brain)
    directory.mkdir(parents=True)
    for number in (1, 2):
        mapped = PartNotes(
            schema_version=2,
            canonical_sha256=digest,
            chunk_chars=400,
            part_number=number,
            usage=Usage(input_tokens=100, output_tokens=50),
            items=[],
            summary_points=["The coast."],
        )
        (directory / f"part-{number:04d}.json").write_text(mapped.model_dump_json())
    harness.script(notes(harness), notes(harness), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    completed = harness.run_ok()
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.usage == saved.map_reduce_state.usage == Usage(input_tokens=500, output_tokens=250)
    assert (completed.input_tokens, completed.output_tokens) == (300, 150)
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert read_progress(harness.fresh_clone(brain), "book").usage == saved.usage


@pytest.mark.parametrize("completed_parts", [0, 2])
def test_move_rekeys_map_files_and_nested_state(harness, book, completed_parts):
    brain, canonical = book
    other = harness.fresh_clone(brain)
    page = read_page(other, canonical)
    page.body += f"\n[Self](/{canonical})\n"
    write_page(other, page)
    harness.git(other, "commit", "--quiet", "--all", "--message", "Self link")
    harness.git(other, "push", "--quiet", "origin", "HEAD:main")
    if completed_parts:
        harness.script(notes(harness), notes(harness))
        ingest_source(harness.ctx, brain, "book", max_tokens=350)
        harness.run_ok()
    else:
        # A legacy checkpoint after a failure before the first completed Part.
        root = other
        saved = document_progress(root, canonical, 400, harness.ctx.clock())
        saved.mode = "map_reduce"
        saved.phase = "map"
        saved.map_reduce_state = MapReduceState(
            canonical_sha256=saved.canonical_sha256, chunk_chars=400, parts_total=4
        )
        write_progress(root, "book", saved)
        harness.git(root, "add", str(progress_path(root, "book")))
        harness.git(root, "commit", "--quiet", "--message", "Legacy zero-Part state")
        harness.git(root, "push", "--quiet", "origin", "HEAD:main")
        sync_brain(harness.ctx, brain)
        harness.run_ok()
    old = read_progress(harness.clone_path(brain), "book")
    move_source(harness.ctx, brain, "book", "archive")
    harness.run_ok()
    saved = read_progress(harness.clone_path(brain), "book")
    assert saved.canonical_sha256 != old.canonical_sha256
    assert saved.map_reduce_state.canonical_sha256 == saved.canonical_sha256
    assert all(
        PartNotes.model_validate_json(path.read_text()).canonical_sha256 == saved.canonical_sha256
        for path in maps(harness, brain).glob("*.json")
    )
    model = harness.script(*([notes(harness)] * (4 - completed_parts)), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert model.requests == 5 - completed_parts


@pytest.mark.parametrize("publish_error", [False, True])
def test_summary_error_records_error_and_preserves_original(
    harness, book, monkeypatch, publish_error
):
    from second_brain_builder.brains import GitError
    from second_brain_builder.worker.clone import BrainClone

    brain, _ = book
    harness.script(*([notes(harness)] * 4), ProviderError("summary outage"))
    if publish_error:

        def fail_publish(*args):
            raise GitError("publish outage")

        monkeypatch.setattr(BrainClone, "finish", fail_publish)
    ingest_source(harness.ctx, brain, "book")
    failed = harness.run()
    assert failed.status is JobStatus.FAILED
    assert "summary outage" in failed.error
    if not publish_error:
        saved = read_progress(harness.fresh_clone(brain), "book")
        assert saved.last_stop == "error"


def test_mode_switch_does_not_reuse_previous_map_files(harness, book):
    brain, _ = book
    harness.script(notes(harness), notes(harness))
    ingest_source(harness.ctx, brain, "book", max_tokens=350)
    harness.run_ok()
    saved = read_progress(harness.clone_path(brain), "book")
    saved.mode = "sequential"
    saved.phase = None
    saved.map_reduce_state = None
    write_progress(harness.clone_path(brain), "book", saved)
    root = harness.clone_path(brain)
    harness.git(root, "add", str(progress_path(root, "book")))
    harness.git(root, "commit", "--quiet", "--message", "Sequential progress")
    harness.git(root, "push", "--quiet", "origin", "HEAD:main")
    model = harness.script(*([notes(harness)] * 4), summary(harness))
    ingest_source(harness.ctx, brain, "book", mode="map_reduce")
    harness.run_ok()
    assert model.requests == 5


def test_unchanged_reconvert_keeps_map_files_and_reduce_progress(harness, book, monkeypatch):
    brain, _ = book
    now = harness.ctx.clock()
    monkeypatch.setattr(harness.ctx, "clock", lambda: now)
    reconvert_source(harness.ctx, brain, "book")
    harness.run_ok()
    harness.script(notes(harness))
    ingest_source(harness.ctx, brain, "book", mode="map_reduce", max_tokens=150)
    harness.run_ok()
    saved = read_progress(harness.clone_path(brain), "book")
    stored = {p.name: p.read_bytes() for p in maps(harness, brain).glob("*.json")}
    assert stored and saved.phase == "reduce"
    reconvert_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert read_progress(harness.clone_path(brain), "book") == saved
    assert {p.name: p.read_bytes() for p in maps(harness, brain).glob("*.json")} == stored
    model = harness.script(summary(harness))
    ingest_source(harness.ctx, brain, "book", mode="map_reduce")
    harness.run_ok()
    assert model.requests == 1


@pytest.mark.parametrize("concurrency", [1, 2])
def test_stop_map_drains_inflight_parts_and_continue_uses_saved_notes(harness, book, concurrency):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(
        update={"ingest_map_concurrency": concurrency}
    )
    barrier = Barrier(concurrency)

    def before(number):
        barrier.wait(10)
        if number == 1:
            cancel_job(harness.ctx, job.id)
        barrier.wait(10)

    model = harness.script(*([notes(harness)] * concurrency), before=before)
    job = ingest_source(harness.ctx, brain, "book")
    stopped = harness.run()
    assert stopped.status is JobStatus.CANCELLED
    assert model.requests == concurrency
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.map_parts_done == concurrency and saved.last_stop == "user_requested"
    assert saved.phase == "map" and saved.status == "partial"
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.PARTLY_INGESTED
    stored = {path.name: path.read_bytes() for path in maps(harness, brain).glob("*.json")}
    model = harness.script(*([notes(harness)] * (4 - concurrency)), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert model.requests == 5 - concurrency
    assert all((maps(harness, brain) / name).read_bytes() == data for name, data in stored.items())
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.INGESTED


def test_stop_reduce_publishes_current_group_and_continue_skips_it(harness, book):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(update={"ingest_map_concurrency": 1})
    harness.script(*([notes(harness, 2)] * 4))
    ingest_source(harness.ctx, brain, "book", max_tokens=600)
    harness.run_ok()

    def before(number):
        assert number == 1
        cancel_job(harness.ctx, job.id)

    model = harness.script(*group_answers(harness, [0]), before=before)
    job = ingest_source(harness.ctx, brain, "book")
    stopped = harness.run()
    assert stopped.status is JobStatus.CANCELLED
    assert stopped.error == "Stopped by the user after reduce group 1 of 2"
    assert model.requests == 1
    fresh = harness.fresh_clone(brain)
    saved = read_progress(fresh, "book")
    assert saved.reduce_groups_done == 1 and saved.last_stop == "user_requested"
    assert (fresh / "wiki/entities/place-00.md").is_file()
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.PARTLY_INGESTED
    model = harness.script(*group_answers(harness, [1]), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert model.requests == 2
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.INGESTED


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


def test_drop_totals_survive_resume_and_are_exposed_by_sources(harness, book, caplog):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(update={"ingest_map_concurrency": 1})
    partial = harness.answer(
        harness.call(
            "record_notes",
            items=[{"name": "PRIVATE CONTENT"}],
            summary_points=["The coast.", 42],
        )
    )
    harness.script(partial)
    ingest_source(harness.ctx, brain, "book", max_tokens=200)
    harness.run_ok()
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert (saved.notes_dropped_total, saved.parts_with_drops) == (2, 1)
    first_map = (maps(harness, brain) / "part-0001.json").read_bytes()
    extracted = PartNotes.model_validate_json(first_map)
    assert (extracted.dropped_items, extracted.dropped_summary_points) == (1, 1)
    assert extracted.dropped_codes == {"missing": 3, "string_type": 1}
    assert "Dropped invalid map notes for Part 1:" in caplog.text
    assert "PRIVATE CONTENT" not in caplog.text

    harness.script(partial, notes(harness), notes(harness), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert (maps(harness, brain) / "part-0001.json").read_bytes() == first_map
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert (saved.notes_dropped_total, saved.parts_with_drops) == (4, 2)
    for view in (
        list_sources(harness.ctx, brain)[0],
        get_source(harness.ctx, brain, "book").source,
    ):
        assert (view.notes_dropped_total, view.parts_with_drops) == (4, 2)


def test_failed_part_is_isolated_and_continue_retries_only_it(harness, book, caplog):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(update={"ingest_map_concurrency": 1})
    invalid = harness.answer(harness.call("record_notes", items=[], summary_points=[]))
    model = harness.script(
        notes(harness), invalid, invalid, invalid, notes(harness), notes(harness)
    )
    ingest_source(harness.ctx, brain, "book")
    failed = harness.run()
    assert failed.status is JobStatus.FAILED
    assert failed.error == "1 Parts failed extraction; Continue Ingest retries them"
    assert model.requests == 6
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.PARTLY_INGESTED
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.phase == "map" and saved.status == "partial"
    assert saved.last_stop == "extraction_failed"
    assert saved.map_reduce_state.map_parts_done == [1, 3, 4]
    assert [part.model_dump() for part in saved.map_failed_parts] == [
        {"part_number": 2, "error_kind": "NotesExtractionError"}
    ]
    assert saved.map_reduce_state.map_failed_parts == saved.map_failed_parts
    assert saved.usage.input_tokens == 600
    assert failed.input_tokens == 600
    assert "Map Part 2 failed extraction: NotesExtractionError" in caplog.text
    assert "Part 2 attempt 1 failed: invalid_notes" in caplog.text
    assert "Part 2 attempt 2 failed: invalid_notes" in caplog.text
    assert "Part 2 attempt 3 failed: invalid_notes" in caplog.text
    assert not (harness.fresh_clone(brain) / "wiki/summaries/book.md").exists()
    original = {path.name: path.read_bytes() for path in maps(harness, brain).glob("*.json")}
    assert set(original) == {"part-0001.json", "part-0003.json", "part-0004.json"}

    model = harness.script(notes(harness), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert model.requests == 2
    assert "Extract Part 2" in model.messages[0][0].content
    assert all(
        (maps(harness, brain) / name).read_bytes() == value for name, value in original.items()
    )
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.phase == "complete" and saved.map_failed_parts == []
    assert saved.usage.input_tokens == 800
    assert get_source(harness.ctx, brain, "book").source.status is SourceStatus.INGESTED


def test_all_failed_parts_keep_converted_source_without_progress_commit(harness, book):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(update={"ingest_map_concurrency": 1})
    harness.script(*[ProviderError("outage") for _ in range(4)])
    head = harness.remote_head(brain)
    ingest_source(harness.ctx, brain, "book")
    failed = harness.run()
    assert failed.error == "4 Parts failed extraction; Continue Ingest retries them"
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved is None
    assert harness.remote_head(brain) == head
    assert get_source(harness.ctx, brain, "book").source.status is SourceStatus.CONVERTED


@pytest.mark.parametrize(
    "field,value", [("schema_version", 1), ("chunk_chars", 401), ("schema_version", None)]
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
    first = group_answers(harness, [0])[0].model_copy(update={"usage": Usage(input_tokens=20000)})
    model = harness.script(first)
    ingest_source(harness.ctx, brain, "book", max_tokens=20100)
    harness.run_ok()
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert (saved.phase, saved.reduce_groups_done, saved.reduce_groups_total) == ("reduce", 1, 2)
    assert model.requests == 1
    assert saved.last_stop == "token_budget"
    assert ingest_estimate(harness.ctx, brain, "book").reduce_estimated_tokens > 0
    state = saved.map_reduce_state
    skipped = [
        state.groups[0].model_copy(
            update={
                "name": f"Skipped {n}",
                "key": f"skipped-{n}",
                "facts": state.groups[0].facts[:3],
            }
        )
        for n in range(90)
    ]
    state.groups = skipped + state.groups
    state.reduce_targets = [
        state.reduce_targets[0].with_name(f"skipped-{n}.md") for n in range(90)
    ] + state.reduce_targets
    state.reduce_groups_done += 90
    state.reduce_groups_skipped += 90
    saved.reduce_groups_done = state.reduce_groups_done
    saved.reduce_groups_total = state.reduce_groups_total
    saved.reduce_groups_skipped = state.reduce_groups_skipped
    write_progress(harness.clone_path(brain), "book", saved)
    assert ingest_estimate(harness.ctx, brain, "book").reduce_estimated_tokens == 40000
    model = harness.script(*group_answers(harness, [1]), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert model.requests == 2


def test_reduce_publishes_at_25_pages_and_keeps_checkpoint_after_failure(harness, book):
    brain, _ = book

    def before(number):
        # Four map calls, then one call for each completed group.
        if number == 30:
            saved = read_progress(harness.fresh_clone(brain), "book")
            assert saved.reduce_groups_done == 25
            assert any(
                "(Part 25 of 27 pages)" in message for message in harness.commit_messages(brain)
            )

    harness.script(
        *([notes(harness, 27)] * 4),
        *group_answers(harness, range(25)),
        ProviderError("writer outage"),
        *group_answers(harness, [26]),
        before=before,
    )
    ingest_source(harness.ctx, brain, "book")
    failed = harness.run()
    assert failed.status is JobStatus.FAILED
    assert failed.error == "1 pages failed; Continue Ingest retries them"
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.reduce_groups_done == 27
    assert saved.reduce_failed_groups[0].model_dump() == {
        "group_index": 26,
        "fact_count": 4,
        "error_kind": "ProviderError",
    }
    model = harness.script(*group_answers(harness, [25]), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    assert model.requests == 2
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.phase == "complete" and saved.reduce_groups_done == 27


def test_parallel_reduce_merges_targets_and_orders_real_git_checkpoints(harness, book):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(
        update={"ingest_reduce_concurrency": 2, "ingest_map_concurrency": 1}
    )
    extracted = notes(harness, 27)
    items = extracted.tool_calls[0].arguments["items"]
    items.append({**items[0], "name": "Place-00"})
    # Persist all map documents first. The resumed model must only see reduce.
    harness.script(*([extracted] * 4))
    ingest_source(harness.ctx, brain, "book", max_tokens=600)
    harness.run_ok()
    stored = {p.name: p.read_bytes() for p in maps(harness, brain).glob("*.json")}
    barrier = Barrier(2)
    lock = Lock()
    started = []
    written = []
    first_batch = []
    model = harness.script()

    def chat(system, messages, tools, max_tokens, *, effort=None):
        content = messages[0].content
        if "<<<SUMMARY-DATA " in content:
            assert len(written) == 27
            return summary(harness)
        assert "record_notes" not in {tool.name for tool in tools}
        match = re.search(
            r"<<<REDUCE-DATA (\w+)>>>\n(.*?)\n<<<END-REDUCE-DATA \1>>>", content, re.S
        )
        assert match is not None
        data = json.loads(match[2])
        target = data["target"]
        if len(messages) == 1:
            with lock:
                assert target not in started
                started.append(target)
                is_first_pair = len(started) <= 2
                if is_first_pair:
                    first_batch.append(target)
            if is_first_pair:
                barrier.wait(10)
            if target.endswith("place-00.md"):
                assert len(data["facts"]) == 8
        with lock:
            assert target not in written
            written.append(target)
        number = int(target.removesuffix(".md").rsplit("-", 1)[1])
        if number >= 25:
            checkpoint = read_progress(harness.fresh_clone(brain), "book")
            assert checkpoint.reduce_groups_done == 25
            assert len(checkpoint.map_reduce_state.written) == 25
        return harness.answer(
            harness.write_wiki_page(
                "entity", f"place-{number:02d}", f"Place {number:02d}", "On the coast.[^book]"
            ),
        )

    model.chat = chat
    job = ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.phase == "complete" and saved.reduce_groups_total == 27
    assert len(started) == len(set(started)) == 27 and len(first_batch) == 2
    assert len(set(saved.map_reduce_state.reduce_targets)) == 27
    assert all(
        (maps(harness, brain) / name).read_bytes() == value for name, value in stored.items()
    )
    commits = [
        message
        for message in reversed(harness.commit_messages(brain))
        if message.startswith("Ingest: Book")
    ]
    assert "(Part 25 of 27 pages)" in commits[-2]
    assert "run complete" in commits[-1]
    assert harness.ctx.jobs.get(job.id).input_tokens == 2800


def test_cached_500_fact_group_condenses_and_writes_once_with_real_git(harness, book):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(update={"ingest_map_concurrency": 1})
    extracted = notes(harness, 1)
    extracted.tool_calls[0].arguments["items"][0]["facts"] = [
        {"text": f"Coastal detail {n}.", "locator": f"Section {n}"} for n in range(125)
    ]
    harness.script(*([extracted] * 4))
    ingest_source(harness.ctx, brain, "book", max_tokens=600)
    harness.run_ok()
    stored = {p.name: p.read_bytes() for p in maps(harness, brain).glob("*.json")}
    model = harness.script()
    condensation_calls = []
    page_writes = []

    def chat(system, messages, tools, max_tokens, *, effort=None):
        content = messages[0].content
        if "<<<SUMMARY-DATA " in content:
            assert len(page_writes) == 1
            return summary(harness)
        if tools[0].name == "record_condensed_facts":
            assert effort == "low" and len(tools) == 1
            match = re.search(
                r"<<<CONDENSE-DATA (\w+)>>>\n(.*?)\n<<<END-CONDENSE-DATA \1>>>", content, re.S
            )
            assert match is not None
            records = json.loads(match[2])
            condensation_calls.append(records)
            return harness.answer(
                harness.call(
                    "record_condensed_facts",
                    facts=[
                        {
                            "text": "Concise coastal facts.",
                            "citations": [c for fact in records for c in fact["citations"]],
                        }
                    ],
                )
            )
        page_writes.append("wiki/entities/place-00.md")
        return group_answers(harness, [0])[0]

    model.chat = chat
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.phase == "complete" and not saved.reduce_failed_groups
    assert len(saved.map_reduce_state.groups[0].facts) == 500
    assert len(condensation_calls) == 9 and len(page_writes) == 1
    assert all(len(batch) <= 60 for batch in condensation_calls)
    assert all(
        (maps(harness, brain) / name).read_bytes() == value for name, value in stored.items()
    )


def test_one_fact_groups_are_counted_without_writing_pages(harness, book):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(update={"ingest_map_concurrency": 1})
    model = harness.script(
        notes(harness, 2),
        *([notes(harness)] * 3),
        harness.answer(harness.call("record_major_names", major_indices=[])),
        summary(harness),
    )
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    root = harness.fresh_clone(brain)
    saved = read_progress(root, "book")
    assert saved.reduce_groups_done == saved.reduce_groups_skipped == 2
    assert saved.phase == "complete" and model.requests == 6
    assert saved.name_index_entries == 2 and saved.name_index_pages == 1
    assert not (root / "wiki/entities/place-00.md").exists()
    assert not (root / "wiki/entities/place-01.md").exists()


def test_restart_reuses_valid_maps_and_restarts_reduce_and_triage(harness, book):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(update={"ingest_map_concurrency": 1})
    harness.script(
        notes(harness, 2),
        *([notes(harness, 1)] * 3),
        harness.answer(harness.call("record_major_names", major_indices=[])),
        *group_answers(harness, [0]),
        summary(harness),
    )
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    stored = {path.name: path.read_bytes() for path in maps(harness, brain).glob("*.json")}
    assert len(stored) == 4
    old_index = harness.clone_path(brain) / "wiki/name-indexes/book-p.md"
    assert old_index.is_file()
    first = get_source(harness.ctx, brain, "book").source
    assert first.name_index_entries == 1 and first.reduce_groups_major == 0
    model = harness.script(
        harness.answer(harness.call("record_major_names", major_indices=[0])),
        *group_answers(harness, [0, 1]),
        harness.answer(harness.call("read_page", path="wiki/summaries/book.md")),
        summary(harness),
    )
    ingest_source(harness.ctx, brain, "book", resume=False, mode="map_reduce")
    harness.run_ok()
    assert model.requests == 5
    assert stored == {path.name: path.read_bytes() for path in maps(harness, brain).glob("*.json")}
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.phase == "complete" and saved.reduce_groups_done == 2
    assert (
        saved.reduce_groups_major == 1 and saved.name_index_entries == saved.name_index_pages == 0
    )
    assert not old_index.exists()
    final = get_source(harness.ctx, brain, "book").source
    assert (
        final.reduce_groups_major == 1 and final.name_index_entries == final.name_index_pages == 0
    )


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
