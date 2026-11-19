"""Device flow and token refresh for the project's GitHub App.

The device flow needs no client secret, and neither does the refresh of a token
that came from the device flow. GitHub answers OAuth errors with an ``error``
code in a JSON body, often with HTTP 200, so each response is checked for that
code before its status.

Every function sends its parameters as a form body, never as URL query
parameters, so no device code or token appears in a URL, an httpx log line, or
an httpx error.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone

import httpx
from pydantic import BaseModel, SecretStr

from second_brain_builder.github._http import (
    OAUTH_URL,
    TIMEOUT,
    USER_AGENT,
    error_for_response,
    parse_model,
    redact,
    response_json,
    send,
)
from second_brain_builder.github.errors import (
    DeviceFlowDisabledError,
    GitHubError,
    GitHubOAuthError,
)
from second_brain_builder.github.models import (
    DeviceCode,
    DeviceDenied,
    DeviceExpired,
    DevicePending,
    DevicePollResult,
    DeviceSlowDown,
    TokenKind,
    TokenSet,
)

DEVICE_CODE_URL = f"{OAUTH_URL}/login/device/code"
ACCESS_TOKEN_URL = f"{OAUTH_URL}/login/oauth/access_token"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_POLL_INTERVAL = 5
"""Seconds between polls when the caller does not know the interval."""
SLOW_DOWN_STEP = 5
"""Seconds that GitHub adds to the poll interval after each ``slow_down``."""


class _TokenResponse(BaseModel):
    """A successful token response. Expiry fields are present only for expiring tokens."""

    access_token: SecretStr
    expires_in: int | None = None
    refresh_token: SecretStr | None = None
    refresh_token_expires_in: int | None = None


def start_device_flow(
    client_id: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> DeviceCode:
    """Ask GitHub for a device code and a user code (step 1 of the device flow).

    A GitHub App gets its permissions from the app settings, so no ``scope`` is sent.

    Raises:
        DeviceFlowDisabledError: the app does not have the device flow enabled.
        GitHubOAuthError: GitHub returned another OAuth error code.
        GitHubError: any other failure, with its HTTP status.
    """
    summary = "GitHub device flow start failed"
    response = _post_form(DEVICE_CODE_URL, {"client_id": client_id}, transport, summary)
    body = response_json(response)
    _raise_for_oauth_error(body, response, summary, secrets=())
    if not response.is_success:
        raise error_for_response(response, summary, secrets=())
    return parse_model(DeviceCode, body, summary)


def poll_device_flow(
    client_id: str,
    device_code: str,
    *,
    interval: int = DEFAULT_POLL_INTERVAL,
    transport: httpx.BaseTransport | None = None,
) -> DevicePollResult:
    """Poll once for the user's authorization (step 3 of the device flow).

    ``interval`` is the poll interval that the caller uses now. It is used only if
    a ``slow_down`` response does not state the new interval; then the new
    interval is ``interval + 5``.

    Returns a ``TokenSet`` of kind ``app`` when the user authorized the app, or the
    outcome that tells the caller what to do next. Expiry fields in the token
    response become absolute UTC datetimes.

    Raises:
        DeviceFlowDisabledError: the app does not have the device flow enabled.
        GitHubOAuthError: any other OAuth error code, such as ``incorrect_device_code``.
        GitHubError: any other failure, with its HTTP status.
    """
    summary = "GitHub device flow poll failed"
    issued_at = datetime.now(timezone.utc)
    response = _post_form(
        ACCESS_TOKEN_URL,
        {"client_id": client_id, "device_code": device_code, "grant_type": DEVICE_GRANT_TYPE},
        transport,
        summary,
    )
    body = response_json(response)
    error = body.get("error") if isinstance(body, dict) else None
    if error == "authorization_pending":
        return DevicePending()
    if error == "slow_down":
        new_interval = body.get("interval")
        if isinstance(new_interval, int) and new_interval > 0:
            return DeviceSlowDown(interval=new_interval)
        return DeviceSlowDown(interval=interval + SLOW_DOWN_STEP)
    if error == "expired_token":
        return DeviceExpired()
    if error == "access_denied":
        return DeviceDenied()
    _raise_for_oauth_error(body, response, summary, secrets=(device_code,))
    if not response.is_success:
        raise error_for_response(response, summary, secrets=(device_code,))
    return _token_set("app", body, issued_at, summary)


def refresh_token(
    client_id: str,
    tokens: TokenSet,
    *,
    transport: httpx.BaseTransport | None = None,
) -> TokenSet:
    """Exchange the refresh token in ``tokens`` for a new token set.

    Tokens from the device flow need no client secret, so only ``client_id`` is
    sent. After a refresh, GitHub revokes the old access token and refresh token.

    Raises:
        ValueError: ``tokens`` has no refresh token (a PAT or a non-expiring token).
        GitHubOAuthError: GitHub rejected the refresh, for example with
            ``bad_refresh_token``. The user must connect GitHub again.
        GitHubError: any other failure, with its HTTP status.
    """
    if tokens.refresh_token is None:
        raise ValueError("The token set has no refresh token, so it cannot be refreshed.")
    summary = "GitHub token refresh failed"
    secret_refresh = tokens.refresh_token.get_secret_value()
    secrets = (secret_refresh, tokens.access_token.get_secret_value())
    issued_at = datetime.now(timezone.utc)
    response = _post_form(
        ACCESS_TOKEN_URL,
        {"client_id": client_id, "grant_type": "refresh_token", "refresh_token": secret_refresh},
        transport,
        summary,
    )
    body = response_json(response)
    _raise_for_oauth_error(body, response, summary, secrets=secrets)
    if not response.is_success:
        raise error_for_response(response, summary, secrets=secrets)
    return _token_set(tokens.kind, body, issued_at, summary)


def _post_form(
    url: str,
    data: Mapping[str, str],
    transport: httpx.BaseTransport | None,
    summary: str,
) -> httpx.Response:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    with httpx.Client(transport=transport, timeout=TIMEOUT, headers=headers) as client:
        request = client.build_request("POST", url, data=dict(data))
        return send(client, request, summary)


def _raise_for_oauth_error(
    body: object,
    response: httpx.Response,
    summary: str,
    secrets: Sequence[str],
) -> None:
    if not isinstance(body, dict):
        return
    error = body.get("error")
    if not isinstance(error, str) or not error:
        return
    description = body.get("error_description")
    error_class = DeviceFlowDisabledError if error == "device_flow_disabled" else GitHubOAuthError
    raise error_class(
        summary,
        error=redact(error, secrets),
        description=redact(description, secrets) if isinstance(description, str) else None,
        status_code=response.status_code,
    )


def _token_set(kind: TokenKind, body: object, issued_at: datetime, summary: str) -> TokenSet:
    """Build a ``TokenSet``. Relative lifetimes count from ``issued_at``.

    ``issued_at`` is taken before the request is sent, so the stored expiry is a
    little early, never late.
    """
    parsed = parse_model(_TokenResponse, body, summary)
    if not parsed.access_token.get_secret_value():
        raise GitHubError(f"{summary}: GitHub returned an empty access token")
    return TokenSet(
        kind=kind,
        access_token=parsed.access_token,
        expires_at=_after(issued_at, parsed.expires_in),
        refresh_token=parsed.refresh_token,
        refresh_expires_at=_after(issued_at, parsed.refresh_token_expires_in),
    )


def _after(start: datetime, seconds: int | None) -> datetime | None:
    return None if seconds is None else start + timedelta(seconds=seconds)
