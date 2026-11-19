"""Contract tests for the device flow and token refresh against a scripted github.com."""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
from pydantic import SecretStr

from second_brain_builder.github import (
    DeviceDenied,
    DeviceExpired,
    DeviceFlowDisabledError,
    DevicePending,
    DeviceSlowDown,
    GitHubConnectionError,
    GitHubError,
    GitHubOAuthError,
    TokenSet,
    poll_device_flow,
    refresh_token,
    start_device_flow,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "github"
CLIENT_ID = "Iv1.synthetic0client"
DEVICE_CODE = "0000synthetic0device0code0for0tests0000"
ACCESS_TOKEN = "ghu_syntheticAccessToken000000000000001"
REFRESH_TOKEN = "ghr_syntheticRefreshToken00000000000001"
NEW_ACCESS_TOKEN = "ghu_syntheticAccessToken000000000000002"
NEW_REFRESH_TOKEN = "ghr_syntheticRefreshToken00000000000002"
SHORT_REFRESH_TOKEN = "ghr_short01"  # Pydantic prints short inputs in full, long ones cut
EIGHT_HOURS = 28_800
SIX_MONTHS = 15_897_600
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class OAuthServer:
    """A scripted github.com OAuth endpoint that records each request."""

    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self.body = body
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    def only_form(self) -> dict[str, str]:
        """The form fields of the one request that the server received."""
        assert len(self.requests) == 1
        return dict(parse_qsl(self.requests[0].content.decode(), keep_blank_values=True))


def _oauth_error(code: str, **extra: object) -> dict[str, object]:
    return {
        "error": code,
        "error_description": f"Synthetic description for {code}.",
        "error_uri": "https://docs.github.com/developers/apps/authorizing-oauth-apps",
        **extra,
    }


def _expiring_token_body(access: str, refresh: str) -> dict[str, object]:
    return {
        "access_token": access,
        "expires_in": EIGHT_HOURS,
        "refresh_token": refresh,
        "refresh_token_expires_in": SIX_MONTHS,
        "scope": "",
        "token_type": "bearer",
    }


def _leaked(exposed: str, secrets: list[str]) -> list[str]:
    """Return the secrets that appear in ``exposed``."""
    return [secret for secret in secrets if secret in exposed]


# --- start_device_flow ----------------------------------------------------------


def test_start_device_flow_sends_only_client_id_and_returns_codes() -> None:
    server = OAuthServer(200, json.loads((FIXTURES / "device_code.json").read_text()))

    code = start_device_flow(CLIENT_ID, transport=server.transport)

    assert code.device_code == DEVICE_CODE
    assert code.user_code == "TEST-0001"
    assert code.verification_uri == "https://github.com/login/device"
    assert (code.expires_in, code.interval) == (900, 5)
    request = server.requests[0]
    assert (request.method, str(request.url)) == ("POST", "https://github.com/login/device/code")
    assert request.headers["Accept"] == "application/json"
    # A GitHub App takes its permissions from the app settings: no scope is sent.
    assert server.only_form() == {"client_id": CLIENT_ID}


def test_start_device_flow_raises_when_the_app_has_it_disabled() -> None:
    server = OAuthServer(400, _oauth_error("device_flow_disabled"))

    with pytest.raises(DeviceFlowDisabledError) as info:
        start_device_flow(CLIENT_ID, transport=server.transport)

    assert info.value.error == "device_flow_disabled"
    assert info.value.status_code == 400


# --- poll_device_flow -----------------------------------------------------------


def test_poll_returns_app_token_set_with_absolute_utc_expiry() -> None:
    server = OAuthServer(200, _expiring_token_body(ACCESS_TOKEN, REFRESH_TOKEN))

    before = datetime.now(timezone.utc)
    result = poll_device_flow(CLIENT_ID, DEVICE_CODE, transport=server.transport)
    after = datetime.now(timezone.utc)

    assert isinstance(result, TokenSet)
    assert result.kind == "app"
    assert result.access_token.get_secret_value() == ACCESS_TOKEN
    assert result.refresh_token is not None
    assert result.refresh_token.get_secret_value() == REFRESH_TOKEN
    assert result.expires_at is not None
    assert result.refresh_expires_at is not None
    assert result.expires_at.utcoffset() == timedelta(0)
    lifetime = timedelta(seconds=EIGHT_HOURS)
    assert before + lifetime <= result.expires_at <= after + lifetime
    refresh_lifetime = timedelta(seconds=SIX_MONTHS)
    assert before + refresh_lifetime <= result.refresh_expires_at <= after + refresh_lifetime

    request = server.requests[0]
    assert str(request.url) == "https://github.com/login/oauth/access_token"
    assert request.headers["Accept"] == "application/json"
    assert server.only_form() == {
        "client_id": CLIENT_ID,
        "device_code": DEVICE_CODE,
        "grant_type": DEVICE_GRANT,
    }


def test_poll_returns_token_without_expiry_when_github_sends_none() -> None:
    server = OAuthServer(200, {"access_token": ACCESS_TOKEN, "scope": "", "token_type": "bearer"})

    result = poll_device_flow(CLIENT_ID, DEVICE_CODE, transport=server.transport)

    assert isinstance(result, TokenSet)
    assert result.expires_at is None
    assert result.refresh_token is None
    assert result.refresh_expires_at is None


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        ("authorization_pending", DevicePending),
        ("access_denied", DeviceDenied),
        ("expired_token", DeviceExpired),
    ],
)
def test_poll_maps_error_codes_to_outcomes(error: str, outcome: type[object]) -> None:
    server = OAuthServer(200, _oauth_error(error))

    result = poll_device_flow(CLIENT_ID, DEVICE_CODE, transport=server.transport)

    assert type(result) is outcome


@pytest.mark.parametrize(
    ("body", "current_interval", "expected_interval"),
    [
        (_oauth_error("slow_down", interval=10), 5, 10),  # GitHub states the new interval
        (_oauth_error("slow_down"), 7, 12),  # it does not: add 5 seconds
    ],
)
def test_poll_slow_down_gives_the_new_interval(
    body: dict[str, object], current_interval: int, expected_interval: int
) -> None:
    server = OAuthServer(200, body)

    result = poll_device_flow(
        CLIENT_ID, DEVICE_CODE, interval=current_interval, transport=server.transport
    )

    assert result == DeviceSlowDown(interval=expected_interval)


@pytest.mark.parametrize(
    ("error", "error_class"),
    [
        ("device_flow_disabled", DeviceFlowDisabledError),
        ("incorrect_device_code", GitHubOAuthError),
        ("incorrect_client_credentials", GitHubOAuthError),
        ("unsupported_grant_type", GitHubOAuthError),
    ],
)
def test_poll_raises_for_errors_the_caller_cannot_wait_out(
    error: str, error_class: type[GitHubOAuthError]
) -> None:
    server = OAuthServer(200, _oauth_error(error))

    with pytest.raises(GitHubOAuthError) as info:
        poll_device_flow(CLIENT_ID, DEVICE_CODE, transport=server.transport)

    assert type(info.value) is error_class
    assert info.value.error == error
    assert info.value.status_code == 200
    assert error in str(info.value)


@pytest.mark.parametrize(
    ("status", "body", "kept_text"),
    [
        # An OAuth error code whose description echoes the device code.
        (
            200,
            {
                **_oauth_error("incorrect_device_code"),
                "error_description": f"The device code {DEVICE_CODE} is not valid.",
            },
            "is not valid",
        ),
        # A failed HTTP response without an OAuth error code that echoes it.
        (500, {"message": f"Server error for device code {DEVICE_CODE}"}, "Server error"),
    ],
)
def test_poll_errors_hide_the_device_code(
    status: int,
    body: dict[str, object],
    kept_text: str,
    caplog: pytest.LogCaptureFixture,
    exposed_text: Callable[[BaseException], str],
) -> None:
    caplog.set_level(logging.DEBUG)
    server = OAuthServer(status, body)

    with pytest.raises(GitHubError) as info:
        poll_device_flow(CLIENT_ID, DEVICE_CODE, transport=server.transport)

    assert info.value.status_code == status
    assert kept_text in str(info.value)
    assert _leaked(exposed_text(info.value) + caplog.text, [DEVICE_CODE]) == []


def test_network_failure_raises_connection_error_without_status() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic connection failure", request=request)

    with pytest.raises(GitHubConnectionError) as info:
        poll_device_flow(CLIENT_ID, DEVICE_CODE, transport=httpx.MockTransport(fail))

    assert info.value.status_code is None


# --- refresh_token --------------------------------------------------------------


def _expiring_token_set() -> TokenSet:
    issued = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return TokenSet(
        kind="app",
        access_token=SecretStr(ACCESS_TOKEN),
        expires_at=issued + timedelta(seconds=EIGHT_HOURS),
        refresh_token=SecretStr(REFRESH_TOKEN),
        refresh_expires_at=issued + timedelta(seconds=SIX_MONTHS),
    )


def test_refresh_sends_refresh_grant_without_secret_and_returns_new_tokens() -> None:
    server = OAuthServer(200, _expiring_token_body(NEW_ACCESS_TOKEN, NEW_REFRESH_TOKEN))

    before = datetime.now(timezone.utc)
    result = refresh_token(CLIENT_ID, _expiring_token_set(), transport=server.transport)

    assert result.kind == "app"
    assert result.access_token.get_secret_value() == NEW_ACCESS_TOKEN
    assert result.refresh_token is not None
    assert result.refresh_token.get_secret_value() == NEW_REFRESH_TOKEN
    assert result.expires_at is not None
    assert result.expires_at >= before + timedelta(seconds=EIGHT_HOURS)
    request = server.requests[0]
    assert str(request.url) == "https://github.com/login/oauth/access_token"
    # The refresh token travels in the body only, so it cannot end up in a URL log.
    assert request.url.query == b""
    assert server.only_form() == {
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    }


def test_rejected_refresh_raises_oauth_error_that_hides_tokens(
    caplog: pytest.LogCaptureFixture, exposed_text: Callable[[BaseException], str]
) -> None:
    caplog.set_level(logging.DEBUG)
    body = _oauth_error("bad_refresh_token")
    body["error_description"] = f"The refresh token {REFRESH_TOKEN} is incorrect or expired."
    server = OAuthServer(200, body)

    with pytest.raises(GitHubOAuthError) as info:
        refresh_token(CLIENT_ID, _expiring_token_set(), transport=server.transport)

    assert info.value.error == "bad_refresh_token"
    assert "is incorrect or expired" in str(info.value)
    assert _leaked(exposed_text(info.value) + caplog.text, [ACCESS_TOKEN, REFRESH_TOKEN]) == []


def test_refresh_of_a_token_set_without_refresh_token_sends_nothing() -> None:
    server = OAuthServer(200, _expiring_token_body(NEW_ACCESS_TOKEN, NEW_REFRESH_TOKEN))
    pat = TokenSet(kind="pat", access_token=SecretStr("ghp_syntheticPersonalToken0000000001"))

    with pytest.raises(ValueError, match="no refresh token"):
        refresh_token(CLIENT_ID, pat, transport=server.transport)

    assert server.requests == []


@pytest.mark.parametrize(
    "body",
    [
        # A field with the wrong type next to real tokens.
        {
            "access_token": NEW_ACCESS_TOKEN,
            "expires_in": "soon",
            "refresh_token": NEW_REFRESH_TOKEN,
        },
        # A missing access token: Pydantic would print the whole input object.
        {"refresh_token": SHORT_REFRESH_TOKEN},
    ],
)
def test_malformed_token_response_raises_without_any_token(
    body: dict[str, object], exposed_text: Callable[[BaseException], str]
) -> None:
    server = OAuthServer(200, body)

    with pytest.raises(GitHubError) as info:
        refresh_token(CLIENT_ID, _expiring_token_set(), transport=server.transport)

    assert "unexpected response" in str(info.value)
    secrets = [
        ACCESS_TOKEN,
        REFRESH_TOKEN,
        NEW_ACCESS_TOKEN,
        NEW_REFRESH_TOKEN,
        SHORT_REFRESH_TOKEN,
    ]
    assert _leaked(exposed_text(info.value), secrets) == []
