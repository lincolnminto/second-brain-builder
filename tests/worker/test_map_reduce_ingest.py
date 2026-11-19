"""Map-reduce jobs with real bare remotes, durable notes, and scripted models."""

import hashlib
import json
import re
from threading import Barrier, Lock

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


def test_all_failed_parts_still_leave_source_partly_ingested(harness, book):
    brain, _ = book
    harness.ctx.settings = harness.ctx.settings.model_copy(update={"ingest_map_concurrency": 1})
    harness.script(*[ProviderError("outage") for _ in range(4)])
    ingest_source(harness.ctx, brain, "book")
    failed = harness.run()
    assert failed.error == "4 Parts failed extraction; Continue Ingest retries them"
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert saved.map_parts_done == 0 and len(saved.map_failed_parts) == 4
    assert {part.error_kind for part in saved.map_failed_parts} == {"ProviderError"}
    assert get_source(harness.ctx, brain, "book").source.status is SourceStatus.PARTLY_INGESTED


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
    model = harness.script(*group_answers(harness, [0]))
    ingest_source(harness.ctx, brain, "book", max_tokens=250)
    harness.run_ok()
    saved = read_progress(harness.fresh_clone(brain), "book")
    assert (saved.phase, saved.reduce_groups_done, saved.reduce_groups_total) == ("reduce", 1, 2)
    assert model.requests == 1
    assert saved.last_stop == "token_budget"
    assert ingest_estimate(harness.ctx, brain, "book").reduce_estimated_tokens > 0
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
    model = harness.script(notes(harness, 2), *([notes(harness)] * 3), summary(harness))
    ingest_source(harness.ctx, brain, "book")
    harness.run_ok()
    root = harness.fresh_clone(brain)
    saved = read_progress(root, "book")
    assert saved.reduce_groups_done == saved.reduce_groups_skipped == 2
    assert saved.phase == "complete" and model.requests == 5
    assert not (root / "wiki/entities/place-00.md").exists()
    assert not (root / "wiki/entities/place-01.md").exists()


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
