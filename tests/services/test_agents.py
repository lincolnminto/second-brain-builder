"""Agent setup, saved answers, the worker heartbeat, and the setup status."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import SecretStr

from second_brain_builder.domain import JobKind
from second_brain_builder.services import (
    AnswerInput,
    AppContext,
    AuthError,
    InvalidInputError,
    check_password,
    get_agent_setup,
    get_auth_status,
    get_setup_status,
    get_worker_status,
    list_jobs,
    record_worker_heartbeat,
    save_answer,
)

if TYPE_CHECKING:
    from tests.services.conftest import BrainFixture, FakeClock

ANSWER_BODY = (
    "Grey herons hunt in tide pools at low tide.[^tide-pools] "
    "They also wade along the shore.[^coastal-birds]"
)


def test_agent_setup_uses_the_public_url_and_clones_without_lfs(
    make_ctx: Any, brain: BrainFixture
) -> None:
    ctx: AppContext = make_ctx(public_url="https://brain.example.com/")

    setup = get_agent_setup(ctx)

    assert setup.mcp_url == "https://brain.example.com/mcp"
    assert "https://brain.example.com/mcp" in setup.mcp_add_command
    assert setup.skill_url == "https://brain.example.com/skills/second-brain/SKILL.md"
    assert setup.skill_url in setup.skill_install_command
    [clone] = setup.clone_commands
    assert clone.brain_id == brain.id
    assert clone.command == (
        "GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/test-user/shore-notes.git "
        "~/second-brain/shore-notes"
    )


def test_save_answer_enqueues_answer_save(ctx: AppContext, brain: BrainFixture) -> None:
    job = save_answer(
        ctx,
        brain.id,
        AnswerInput(
            title=" Where herons hunt ",
            body=ANSWER_BODY,
            source_ids=["tide-pools", "coastal-birds"],
            description="Feeding places of the grey heron.",
            tags=["birds", "birds"],
        ),
    )

    assert job.kind is JobKind.ANSWER_SAVE
    assert job.payload == {
        "brain_id": brain.id,
        "title": "Where herons hunt",
        "body": ANSWER_BODY,
        "source_ids": ["tide-pools", "coastal-birds"],
        "description": "Feeding places of the grey heron.",
        "tags": ["birds"],
    }


@pytest.mark.parametrize(
    ("body", "source_ids", "message"),
    [
        (
            ANSWER_BODY,
            ["tide-pools", "coastal-birds", "dune"],
            "not Source ids of this Brain: dune",
        ),
        ("Herons fish.[^moby-dick]", [], "not Source ids of this Brain: moby-dick"),
        ("Herons fish.[^tide-pools]", ["tide-pools", "coastal-birds"], "does not cite"),
        ("Herons fish.", [], "List the ids"),
    ],
    ids=["unknown-listed-id", "unknown-cited-id", "uncited-id", "no-sources"],
)
def test_save_answer_checks_the_source_ids(
    ctx: AppContext, brain: BrainFixture, body: str, source_ids: list[str], message: str
) -> None:
    with pytest.raises(InvalidInputError, match=message):
        save_answer(ctx, brain.id, AnswerInput(title="Herons", body=body, source_ids=source_ids))

    assert list_jobs(ctx, brain.id) == []


def test_worker_goes_offline_one_minute_after_its_last_heartbeat(
    ctx: AppContext, clock: FakeClock
) -> None:
    assert get_worker_status(ctx).online is False

    record_worker_heartbeat(ctx, "worker-1")
    status = get_worker_status(ctx)
    assert (status.online, status.worker_id, status.last_seen_at) == (True, "worker-1", clock.now)

    clock.advance(timedelta(seconds=61))
    assert get_worker_status(ctx).online is False


def test_setup_status_counts_what_is_set_up(
    ctx: AppContext, brain: BrainFixture, connected: Any
) -> None:
    status = get_setup_status(ctx)

    assert (status.github_connected, status.github_login) == (True, "test-user")
    assert (status.brain_count, status.provider_count, status.ingest_configured) == (1, 0, False)
    assert status.github_app_configured is True


def test_password_is_checked_only_when_one_is_set(make_ctx: Any) -> None:
    open_ctx: AppContext = make_ctx()
    locked: AppContext = make_ctx(password=SecretStr("correct horse"))

    assert get_auth_status(open_ctx).login_required is False
    check_password(open_ctx, SecretStr("anything"))
    assert get_auth_status(locked).login_required is True
    check_password(locked, SecretStr("correct horse"))
    with pytest.raises(AuthError) as caught:
        check_password(locked, SecretStr("correct horse "))
    assert caught.value.service == "app"
