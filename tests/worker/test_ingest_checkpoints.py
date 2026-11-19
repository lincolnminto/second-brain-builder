"""Resumable ingestion contracts with scripted models and real bare Git remotes."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath
from threading import Event

import httpx2
import pytest
from pydantic import SecretStr

from second_brain_builder.domain import JobKind, JobStatus, SourceStatus, TaskMode
from second_brain_builder.ingest.progress import document_progress, read_progress
from second_brain_builder.okf import read_page, write_page
from second_brain_builder.providers import ChatResponse, Message, ProviderError, ProviderKind, Usage
from second_brain_builder.services import (
    ConflictError,
    ProviderInput,
    TaskSettingInput,
    TaskSettingsInput,
    create_provider,
    delete_source,
    ingest_estimate,
    ingest_source,
    list_sources,
    move_source,
    put_task_settings,
    reconvert_source,
    retry_job,
    search,
    sync_brain,
)
from second_brain_builder.services.providers import get_ingest_model


@pytest.fixture
def survey(harness):
    brain = harness.create_brain()
    canonical = harness.seed_source(brain, "survey", "Survey")
    other = harness.fresh_clone(brain)
    page = read_page(other, canonical)
    page.body = "# Survey\n\n" + "Alpha pools. " * 3000 + "\n\n## Beta\n\n" + "Beta pools. " * 3000
    write_page(other, page)
    harness.git(other, "commit", "--quiet", "--all", "--message", "Long survey")
    harness.git(other, "push", "--quiet", "origin", "HEAD:main")
    sync_brain(harness.ctx, brain)
    harness.run_ok()
    harness.enable_ingest()
    return brain, canonical


def first_part(harness):
    return harness.answer(
        harness.write_wiki_page("summary", "survey", "Survey", "# Survey\n\nPools.[^survey]\n"),
        harness.call("finish", note="Remember the deep pools."),
    )


def test_completed_part_is_pushed_before_next_request_and_survives_failure(harness, survey):
    brain, _ = survey

    def before(number):
        if number == 2:
            saved = read_progress(harness.fresh_clone(brain), "survey")
            assert saved is not None and saved.parts_done == 1
            assert "Ingest: Survey (Part 1 of 2)" in harness.commit_messages(brain)

    harness.script(
        first_part(harness),
        harness.answer(
            harness.write_wiki_page("entity", "discarded", "Discarded", "Pools.[^survey]")
        ),
        ProviderError("scripted outage"),
        before=before,
    )
    ingest_source(harness.ctx, brain, "survey")
    failed = harness.run()
    assert failed.status is JobStatus.FAILED
    assert "scripted outage" in failed.error
    assert (failed.input_tokens, failed.output_tokens) == (200, 100)
    fresh = harness.fresh_clone(brain)
    saved = read_progress(fresh, "survey")
    assert saved is not None and saved.parts_done == 1
    assert saved.completed_usage == Usage(input_tokens=100, output_tokens=50)
    assert saved.recent_part_tokens == [150]
    assert saved.usage == Usage(input_tokens=200, output_tokens=100)
    assert not (fresh / "wiki/entities/discarded.md").exists()
    assert search(harness.ctx, brain, "Discarded", types=["entity"]) == []
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.PARTLY_INGESTED
    assert ingest_estimate(harness.ctx, brain, "survey").estimated_tokens == 150

    model = harness.script(harness.answer(harness.call("finish")))
    retry_job(harness.ctx, failed.id)
    harness.run_ok()
    assert model.requests == 1
    assert read_progress(harness.fresh_clone(brain), "survey").recent_part_tokens == [150, 150]
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.INGESTED


def test_budget_predicts_next_part_and_resume_receives_context(harness, survey):
    brain, _ = survey
    harness.script(first_part(harness))
    # 150 spent is below 200, but the estimated next Part would exceed it.
    ingest_source(harness.ctx, brain, "survey", max_tokens=200)
    harness.run_ok()
    saved = read_progress(harness.fresh_clone(brain), "survey")
    assert saved is not None and saved.parts_done == 1
    assert saved.notes == ["Part 1: Remember the deep pools."]
    assert saved.written == [PurePosixPath("wiki/summaries/survey.md")]

    model = harness.script(harness.answer(harness.call("finish")))
    ingest_source(harness.ctx, brain, "survey", max_tokens=1)
    harness.run_ok()
    message = model.messages[0][0].content
    assert "Remember the deep pools." in message
    assert "/wiki/summaries/survey.md" in message
    assert model.requests == 1

    head = harness.remote_head(brain)
    log = (harness.clone_path(brain) / "log.md").read_text()
    ingest_source(harness.ctx, brain, "survey")
    harness.run_ok()  # no model needs to be supplied for an already complete Source
    assert harness.remote_head(brain) == head
    assert (harness.clone_path(brain) / "log.md").read_text() == log


@pytest.mark.parametrize("stop", ["refusal", "end_turn", "hard_cap"])
def test_first_part_failure_never_publishes_progress_or_writes(harness, survey, stop):
    brain, _ = survey
    head = harness.remote_head(brain)
    response = ChatResponse(text="Cannot continue.", stop_reason=stop, usage=Usage(input_tokens=10))
    if stop == "hard_cap":
        response = first_part(harness).model_copy(update={"usage": Usage(input_tokens=200_000)})
    harness.script(response, response)
    ingest_source(harness.ctx, brain, "survey", max_tokens=1)
    failed = harness.run()
    assert failed.status is JobStatus.FAILED
    assert harness.remote_head(brain) == head
    assert read_progress(harness.clone_path(brain), "survey") is None
    assert not (harness.clone_path(brain) / "wiki/summaries/survey.md").exists()
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.FAILED
    if stop == "hard_cap":
        assert "hard safety cap" in failed.error


def test_page_limit_completes_part_instead_of_repeating_it(harness, survey, monkeypatch):
    brain, _ = survey
    monkeypatch.setattr("second_brain_builder.ingest.loop.MAX_PAGES_PER_PART", 1)
    refused = harness.write_wiki_page("entity", "extra", "Extra", "Extra.[^survey]")
    model = harness.script(
        harness.answer(first_part(harness).tool_calls[0]),
        harness.answer(refused),
        harness.answer(harness.call("finish")),
    )
    ingest_source(harness.ctx, brain, "survey", max_tokens=1)
    harness.run_ok()
    saved = read_progress(harness.fresh_clone(brain), "survey")
    assert saved is not None and saved.parts_done == 1
    results = [
        message.content for message in model.messages[-1] if message.tool_call_id == refused.id
    ]
    assert len(results) == 1 and "Call finish" in results[0]
    assert not (harness.clone_path(brain) / "wiki/entities/extra.md").exists()


def test_failed_checkpoint_push_is_retained_and_published_before_resume(
    harness, survey, monkeypatch
):
    from second_brain_builder.brains import GitRepo

    brain, _ = survey
    push = GitRepo.push

    def fail_push(self):
        raise RuntimeError("scripted push failure")

    harness.script(first_part(harness))
    monkeypatch.setattr(GitRepo, "push", fail_push)
    ingest_source(harness.ctx, brain, "survey", max_tokens=1)
    failed = harness.run()
    assert failed.status is JobStatus.FAILED
    local = read_progress(harness.clone_path(brain), "survey")
    assert local is not None and local.parts_done == 1
    assert read_progress(harness.fresh_clone(brain), "survey") is None
    monkeypatch.setattr(GitRepo, "push", push)
    model = harness.script(harness.answer(harness.call("finish")))
    retry_job(harness.ctx, failed.id)
    harness.run_ok()
    assert model.requests == 1
    saved = read_progress(harness.fresh_clone(brain), "survey")
    assert saved is not None and saved.parts_done == 2


def test_retry_keeps_ingest_options(harness, survey):
    brain, _ = survey
    harness.script(ProviderError("outage"))
    original = ingest_source(harness.ctx, brain, "survey", resume=False, max_tokens=123)
    harness.run()
    retried = retry_job(harness.ctx, original.id)
    assert retried.payload == original.payload


@pytest.mark.parametrize("pending", ["ingest", "reconvert", "move"])
def test_source_mutations_are_exclusive_and_delete_cancels_queued(harness, survey, pending):
    brain, _ = survey
    actions = {
        "ingest": lambda: ingest_source(harness.ctx, brain, "survey"),
        "reconvert": lambda: reconvert_source(harness.ctx, brain, "survey"),
        "move": lambda: move_source(harness.ctx, brain, "survey", "archive"),
    }
    job = actions[pending]()
    for action in actions.values():
        with pytest.raises(ConflictError):
            action()
    deletion = delete_source(harness.ctx, brain, "survey")
    assert harness.ctx.jobs.get(job.id).status is JobStatus.CANCELLED
    assert deletion.kind is JobKind.SOURCE_DELETE


def test_delete_rejects_running_job(harness, survey):
    brain, _ = survey
    queued = ingest_source(harness.ctx, brain, "survey")
    running = harness.ctx.jobs.claim("test-running")
    assert running.id == queued.id
    with pytest.raises(ConflictError, match="running"):
        delete_source(harness.ctx, brain, "survey")


def test_concurrent_ingest_admission_only_enqueues_once(harness, survey, monkeypatch):
    brain, _ = survey
    first_enqueue = Event()
    second_started = Event()
    second_enqueue = Event()
    enqueue = harness.ctx.jobs.enqueue

    def hold_first_enqueue(kind, brain_id, payload):
        if first_enqueue.is_set():
            second_enqueue.set()
        else:
            first_enqueue.set()
            assert second_started.wait(5)
            second_enqueue.wait(1)
        return enqueue(kind, brain_id, payload)

    monkeypatch.setattr(harness.ctx.jobs, "enqueue", hold_first_enqueue)

    def submit(number):
        if number == 1:
            assert first_enqueue.wait(5)
            second_started.set()
        try:
            return ingest_source(harness.ctx, brain, "survey")
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sum(result is not None for result in results) == 1


def test_move_preserves_completed_prefix_after_canonical_links_change(harness, survey):
    brain, canonical = survey
    other = harness.fresh_clone(brain)
    page = read_page(other, canonical)
    page.body += f"\n[This document](/{canonical})\n"
    write_page(other, page)
    harness.git(other, "commit", "--quiet", "--all", "--message", "Add link")
    harness.git(other, "push", "--quiet", "origin", "HEAD:main")
    harness.script(first_part(harness))
    ingest_source(harness.ctx, brain, "survey", max_tokens=1)
    harness.run_ok()
    before = read_progress(harness.clone_path(brain), "survey")
    move_source(harness.ctx, brain, "survey", "archive")
    harness.run_ok()
    root = harness.fresh_clone(brain)
    saved = document_progress(
        root,
        PurePosixPath("sources/archive/survey/survey.md"),
        before.chunk_chars,
        harness.ctx.clock(),
    )
    assert saved.parts_done == 1
    assert saved.canonical_sha256 != before.canonical_sha256
    assert saved.notes == before.notes
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.PARTLY_INGESTED


@pytest.mark.parametrize("contents", ["parts_done: [broken yaml", "parts_done: nope"])
def test_corrupt_progress_does_not_crash_estimate_or_sync(harness, survey, contents):
    brain, _ = survey
    other = harness.fresh_clone(brain)
    target = other / ".brain/ingest/survey.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(contents)
    harness.git(other, "add", "--all")
    harness.git(other, "commit", "--quiet", "--message", "Corrupt progress")
    harness.git(other, "push", "--quiet", "origin", "HEAD:main")
    sync_brain(harness.ctx, brain)
    harness.run_ok()
    assert ingest_estimate(harness.ctx, brain, "survey").parts_done == 0
    ingest_source(harness.ctx, brain, "survey")
    failed = harness.run()
    assert failed.status is JobStatus.FAILED
    assert "progress file is invalid" in failed.error


def test_cache_rejection_is_persisted_across_models_per_provider(harness):
    harness.ctx.chat_model_factory = None
    provider = create_provider(
        harness.ctx,
        ProviderInput(
            name="Anthropic",
            kind=ProviderKind.ANTHROPIC,
            base_url="https://anthropic.example",
            api_key=SecretStr("synthetic"),
        ),
    )
    put_task_settings(
        harness.ctx,
        TaskSettingsInput(
            ingest=TaskSettingInput(mode=TaskMode.PROVIDER, provider_id=provider.id, model="model")
        ),
    )
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(
                422,
                json={
                    "error": {"type": "invalid_request_error", "message": "system must be a string"}
                },
            )
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "model",
                "content": [{"type": "text", "text": "Done"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    harness.ctx.provider_transport = httpx2.MockTransport(respond)
    for _ in range(2):
        model = get_ingest_model(harness.ctx)
        model.chat_model.chat("System", [Message(role="user", content="Part")], [], 100)
    assert len(requests) == 3
    assert b"cache_control" in requests[0].content
    assert all(b"cache_control" not in request.content for request in requests[1:])


def test_restart_removes_search_entries_from_crashed_uncommitted_part(harness, survey):
    brain, _ = survey
    harness.script(first_part(harness))
    ingest_source(harness.ctx, brain, "survey", max_tokens=1)
    harness.run_ok()
    root = harness.clone_path(brain)
    ghost = PurePosixPath("wiki/entities/ghost.md")
    page = read_page(root, PurePosixPath("wiki/summaries/survey.md"))
    page.path = ghost
    page.frontmatter = page.frontmatter.model_copy(update={"type": "entity", "title": "Ghost"})
    page.body = "# Ghost\n\nGhosted pages.[^survey]\n"
    write_page(root, page)
    harness.ctx.search.update(brain, root, [ghost])
    assert search(harness.ctx, brain, "Ghosted", types=["entity"])
    move_source(harness.ctx, brain, "survey", "archive")
    harness.run_ok()
    assert not (root / ghost).exists()
    assert search(harness.ctx, brain, "Ghosted", types=["entity"]) == []
    assert list_sources(harness.ctx, brain)[0].status is SourceStatus.PARTLY_INGESTED


def test_progress_replace_is_atomic(harness, survey, monkeypatch):
    import os

    from second_brain_builder.ingest.progress import write_progress

    brain, canonical = survey
    root = harness.clone_path(brain)
    old = document_progress(root, canonical, 64000, harness.ctx.clock())
    write_progress(root, "survey", old)
    new = old.model_copy(update={"parts_done": 1})
    replace = os.replace
    calls = []

    def inspect(source, destination):
        assert read_progress(root, "survey") == old
        assert source != str(destination)
        calls.append(destination)
        replace(source, destination)

    monkeypatch.setattr(os, "replace", inspect)
    write_progress(root, "survey", new)
    assert len(calls) == 1
    assert read_progress(root, "survey") == new


def test_safety_cap_uses_history_and_aborted_usage_does_not_inflate_estimate(harness, survey):
    brain, _ = survey
    first = first_part(harness).model_copy(update={"usage": Usage(input_tokens=60_000)})
    write = harness.answer(
        harness.write_wiki_page("entity", "aborted", "Aborted", "Pools.[^survey]")
    )
    write.usage = Usage(input_tokens=50_000)
    cap = harness.answer(harness.call("finish"))
    cap.usage = Usage(input_tokens=190_000)
    harness.script(first, write, cap)
    ingest_source(harness.ctx, brain, "survey")
    failed = harness.run()
    assert failed.status is JobStatus.FAILED
    assert "Part 2" in failed.error and "240000 tokens" in failed.error
    saved = read_progress(harness.fresh_clone(brain), "survey")
    assert saved.parts_done == 1
    assert saved.usage.input_tokens == 300_000
    assert saved.completed_usage.input_tokens == 60_000
    assert saved.recent_part_tokens == [60_000]
    assert ingest_estimate(harness.ctx, brain, "survey").estimated_tokens == 60_000
    assert not (harness.clone_path(brain) / "wiki/entities/aborted.md").exists()
