"""GitHub connection use cases against a scripted GitHub (httpx.MockTransport)."""

from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from second_brain_builder.services import (
    AppContext,
    AuthError,
    ConflictError,
    DeviceFlowStart,
    InvalidInputError,
    NotConfiguredError,
    NotConnectedError,
    connect_github_pat,
    disconnect_github,
    get_github_account,
    get_github_auth,
    github_install_url,
    list_github_repos,
    poll_github_device_flow,
    start_github_device_flow,
)
from second_brain_builder.services import github as services_github
from second_brain_builder.store import CredentialRow, GitHubAccountRow, SettingRow

if TYPE_CHECKING:
    from tests.services.conftest import FakeClock, FakeGitHub, InsertBrain

FIXTURES = Path(__file__).parents[1] / "fixtures" / "github"
API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
PAT = "ghp_syntheticPersonalToken000000000001"
APP_TOKEN = "ghu_syntheticAccessToken000000000000001"
REFRESH_TOKEN = "ghr_syntheticRefreshToken00000000000001"
NEW_APP_TOKEN = "ghu_syntheticAccessToken000000000000002"
NEW_REFRESH_TOKEN = "ghr_syntheticRefreshToken00000000000002"
DEVICE_CODE = json.loads((FIXTURES / "device_code.json").read_text())


def token_body(access: str, refresh: str, expires_in: int) -> dict[str, Any]:
    return {
        "access_token": access,
        "token_type": "bearer",
        "scope": "",
        "expires_in": expires_in,
        "refresh_token": refresh,
        "refresh_token_expires_in": 15_811_200,
    }


def connect_with_device_flow(ctx: AppContext, github: FakeGitHub, expires_in: int) -> None:
    github.on("POST", DEVICE_CODE_URL, DEVICE_CODE)
    start_github_device_flow(ctx)
    github.on("POST", TOKEN_URL, token_body(APP_TOKEN, REFRESH_TOKEN, expires_in))
    github.user()
    assert poll_github_device_flow(ctx).status == "connected"
    github.requests.clear()


def stored_ciphertexts(ctx: AppContext) -> list[bytes]:
    with ctx.read_sessions() as session:
        return list(session.scalars(select(CredentialRow.ciphertext)))


# --- personal access token ------------------------------------------------------------


def test_pat_is_stripped_checked_with_github_and_stored_encrypted(
    ctx: AppContext, github: FakeGitHub
) -> None:
    github.user()

    account = connect_github_pat(ctx, SecretStr(f"  {PAT}\n"))

    assert (account.login, account.user_id, account.token_kind) == ("test-user", 1000001, "pat")
    assert account.noreply_email == "1000001+test-user@users.noreply.github.com"
    assert github.requests[0].headers["Authorization"] == f"Bearer {PAT}"
    [ciphertext] = stored_ciphertexts(ctx)
    assert PAT.encode() not in ciphertext
    access = get_github_auth(ctx)
    assert access.auth.token.get_secret_value() == PAT
    assert access.identity.name == "test-user"
    assert access.identity.email == "1000001+test-user@users.noreply.github.com"


def test_pat_with_inner_whitespace_is_invalid_input_and_sends_nothing(
    ctx: AppContext, github: FakeGitHub
) -> None:
    with pytest.raises(InvalidInputError) as caught:
        connect_github_pat(ctx, SecretStr("ghp_synthetic token"))

    assert github.requests == []
    assert "synthetic" not in caught.value.message
    assert get_github_account(ctx) is None


def test_pat_that_github_rejects_is_invalid_input(ctx: AppContext, github: FakeGitHub) -> None:
    github.on("GET", f"{API}/user", {"message": "Bad credentials"}, status=401)

    with pytest.raises(InvalidInputError, match="GitHub rejected the token"):
        connect_github_pat(ctx, SecretStr(PAT))

    assert get_github_account(ctx) is None
    assert stored_ciphertexts(ctx) == []


def test_a_new_connection_replaces_the_old_account_and_its_credential(
    ctx: AppContext, github: FakeGitHub
) -> None:
    github.user()
    connect_github_pat(ctx, SecretStr(PAT))
    connect_github_pat(ctx, SecretStr(PAT.replace("1", "2")))

    with ctx.read_sessions() as session:
        assert len(session.scalars(select(GitHubAccountRow)).all()) == 1
    assert len(stored_ciphertexts(ctx)) == 1
    assert get_github_auth(ctx).auth.token.get_secret_value() == PAT.replace("1", "2")


# --- device flow ----------------------------------------------------------------------


def test_device_flow_keeps_the_device_code_on_the_server(
    ctx: AppContext, github: FakeGitHub
) -> None:
    github.on("POST", DEVICE_CODE_URL, DEVICE_CODE)

    start = start_github_device_flow(ctx)

    assert start == DeviceFlowStart(
        user_code="TEST-0001",
        verification_uri="https://github.com/login/device",
        expires_in=900,
        interval=5,
    )
    assert DEVICE_CODE["device_code"] not in start.model_dump_json()
    with ctx.read_sessions() as session:
        stored = json.dumps([row.value for row in session.scalars(select(SettingRow))])
    assert DEVICE_CODE["device_code"] not in stored


def test_device_flow_polls_until_the_user_authorizes(
    ctx: AppContext, github: FakeGitHub, clock: FakeClock
) -> None:
    github.on("POST", DEVICE_CODE_URL, DEVICE_CODE)
    start_github_device_flow(ctx)

    github.on("POST", TOKEN_URL, {"error": "authorization_pending"})
    assert poll_github_device_flow(ctx).status == "pending"
    github.on("POST", TOKEN_URL, {"error": "slow_down", "interval": 10})
    slow = poll_github_device_flow(ctx)
    assert (slow.status, slow.interval) == ("slow_down", 10)
    github.on("POST", TOKEN_URL, {"error": "authorization_pending"})
    assert poll_github_device_flow(ctx).interval == 10

    github.on("POST", TOKEN_URL, token_body(APP_TOKEN, REFRESH_TOKEN, 28_800))
    github.user()
    result = poll_github_device_flow(ctx)

    assert result.status == "connected"
    assert result.account is not None
    assert result.account.token_kind == "app"
    assert result.account.expires_at is not None
    assert result.account.expires_at > clock.now + timedelta(hours=7)
    sent_codes = {
        github.form(index)["device_code"]
        for index, request in enumerate(github.requests)
        if str(request.url) == TOKEN_URL
    }
    assert sent_codes == {DEVICE_CODE["device_code"]}
    assert get_github_auth(ctx).auth.token.get_secret_value() == APP_TOKEN
    with pytest.raises(ConflictError, match="No GitHub device flow is in progress"):
        poll_github_device_flow(ctx)


def test_device_flow_that_expired_on_the_server_does_not_call_github(
    ctx: AppContext, github: FakeGitHub, clock: FakeClock
) -> None:
    github.on("POST", DEVICE_CODE_URL, DEVICE_CODE)
    start_github_device_flow(ctx)
    clock.advance(timedelta(seconds=901))
    github.requests.clear()

    assert poll_github_device_flow(ctx).status == "expired"
    assert github.requests == []


def test_device_flow_needs_a_client_id(make_ctx: Any, github: FakeGitHub) -> None:
    context = make_ctx(github_client_id=None)

    with pytest.raises(NotConfiguredError):
        start_github_device_flow(context)
    assert github.requests == []


# --- token refresh --------------------------------------------------------------------


def test_app_token_that_expires_within_five_minutes_is_refreshed_once(
    ctx: AppContext, github: FakeGitHub, clock: FakeClock
) -> None:
    connect_with_device_flow(ctx, github, expires_in=8 * 3600)
    clock.advance(timedelta(hours=8) - timedelta(minutes=4))
    # The github module dates the new token with the real time, which is 8 hours
    # behind the test clock now; a 16-hour token is still new for the test clock.
    github.on("POST", TOKEN_URL, token_body(NEW_APP_TOKEN, NEW_REFRESH_TOKEN, 16 * 3600))

    access = get_github_auth(ctx)

    assert access.auth.token.get_secret_value() == NEW_APP_TOKEN
    form = github.form(0)
    assert (form["grant_type"], form["refresh_token"]) == ("refresh_token", REFRESH_TOKEN)
    assert get_github_auth(ctx).auth.token.get_secret_value() == NEW_APP_TOKEN
    assert len(github.requests) == 1  # the stored token is new now
    account = get_github_account(ctx)
    assert account is not None
    assert account.expires_at is not None
    assert account.expires_at > clock.now + timedelta(hours=7)


def test_app_token_with_more_than_five_minutes_left_is_used_as_it_is(
    ctx: AppContext, github: FakeGitHub, clock: FakeClock
) -> None:
    connect_with_device_flow(ctx, github, expires_in=8 * 3600)
    clock.advance(timedelta(hours=8) - timedelta(minutes=6))

    assert get_github_auth(ctx).auth.token.get_secret_value() == APP_TOKEN
    assert github.requests == []


def test_rejected_refresh_asks_the_user_to_connect_again(
    ctx: AppContext, github: FakeGitHub, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No other process stores new tokens, so the wait for them only makes the test slow.
    monkeypatch.setattr(services_github, "REFRESH_RACE_WAIT_SECONDS", 0.1)
    connect_with_device_flow(ctx, github, expires_in=60)
    github.on("POST", TOKEN_URL, {"error": "bad_refresh_token", "error_description": "expired"})

    with pytest.raises(AuthError) as caught:
        get_github_auth(ctx)

    assert caught.value.service == "github"
    assert REFRESH_TOKEN not in caught.value.message


def test_refresh_that_lost_a_race_uses_the_tokens_of_the_winner(
    ctx: AppContext, make_ctx: Any, github: FakeGitHub
) -> None:
    connect_with_device_flow(ctx, github, expires_in=60)
    other_process = make_ctx()

    def winner_refreshes_first(request: httpx.Request) -> httpx.Response:
        # The other process used the same refresh token a moment earlier.
        github.on("POST", TOKEN_URL, token_body(NEW_APP_TOKEN, NEW_REFRESH_TOKEN, 8 * 3600))
        assert get_github_auth(other_process).auth.token.get_secret_value() == NEW_APP_TOKEN
        return httpx.Response(200, json={"error": "bad_refresh_token"})

    github.on_call("POST", TOKEN_URL, winner_refreshes_first)

    assert get_github_auth(ctx).auth.token.get_secret_value() == NEW_APP_TOKEN


def test_refresh_that_lost_a_race_waits_for_the_winner_to_store_its_tokens(
    ctx: AppContext, make_ctx: Any, github: FakeGitHub
) -> None:
    connect_with_device_flow(ctx, github, expires_in=60)
    other_process = make_ctx()
    winners: list[threading.Thread] = []

    def winner_stores_its_tokens_later() -> None:
        time.sleep(0.3)
        get_github_auth(other_process)

    def loser_is_rejected(request: httpx.Request) -> httpx.Response:
        # GitHub took the same refresh token from the other process a moment earlier.
        # That process stores the new tokens only after this one gets its answer.
        github.on("POST", TOKEN_URL, token_body(NEW_APP_TOKEN, NEW_REFRESH_TOKEN, 8 * 3600))
        winner = threading.Thread(target=winner_stores_its_tokens_later)
        winners.append(winner)
        winner.start()
        return httpx.Response(200, json={"error": "bad_refresh_token"})

    github.on_call("POST", TOKEN_URL, loser_is_rejected)
    try:
        access = get_github_auth(ctx)
    finally:
        for winner in winners:
            winner.join()

    assert access.auth.token.get_secret_value() == NEW_APP_TOKEN


def test_expired_token_without_refresh_token_needs_a_new_connection(
    ctx: AppContext, github: FakeGitHub, clock: FakeClock
) -> None:
    body = {"access_token": APP_TOKEN, "token_type": "bearer", "expires_in": 60}
    github.on("POST", DEVICE_CODE_URL, DEVICE_CODE)
    start_github_device_flow(ctx)
    github.on("POST", TOKEN_URL, body)
    github.user()
    poll_github_device_flow(ctx)
    clock.advance(timedelta(minutes=2))

    with pytest.raises(AuthError, match="expired"):
        get_github_auth(ctx)


# --- account, repos, and install URL --------------------------------------------------


def test_disconnect_removes_the_account_and_its_credential(
    ctx: AppContext, github: FakeGitHub
) -> None:
    github.user()
    connect_github_pat(ctx, SecretStr(PAT))

    disconnect_github(ctx)

    assert get_github_account(ctx) is None
    assert stored_ciphertexts(ctx) == []
    with pytest.raises(NotConnectedError):
        get_github_auth(ctx)


def test_repos_show_which_repository_holds_a_brain(
    ctx: AppContext, github: FakeGitHub, connected: Any, insert_brain: InsertBrain
) -> None:
    brain_id = insert_brain("Test-User/Notes")
    repos = [
        {
            "full_name": name,
            "private": True,
            "clone_url": f"https://github.com/{name}.git",
            "default_branch": "main",
        }
        for name in ("test-user/notes", "test-user/archive")
    ]
    github.on("GET", f"{API}/user/repos?per_page=100", repos)

    result = {repo.full_name: repo.brain_id for repo in list_github_repos(ctx)}

    assert result == {"test-user/archive": None, "test-user/notes": brain_id}


def test_install_url_needs_the_app_slug(make_ctx: Any) -> None:
    assert github_install_url(make_ctx()) == (
        "https://github.com/apps/synthetic-app/installations/new"
    )
    with pytest.raises(NotConfiguredError):
        github_install_url(make_ctx(github_app_slug=None))
