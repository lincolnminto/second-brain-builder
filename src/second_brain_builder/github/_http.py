"""Helpers shared by the OAuth functions and ``GitHubClient``.

The rule for every helper here: no token reaches an exception message, a
traceback, or a log line. Tokens go only in headers and form bodies, never in
URLs. Pydantic and httpx errors can quote a token, so no ``GitHubError`` keeps
one as its ``__cause__`` or ``__context__``: the helpers raise the
``GitHubError`` after the ``except`` block, and keep only what is safe to show.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from second_brain_builder.github.errors import (
    GitHubAuthError,
    GitHubConnectionError,
    GitHubError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubValidationError,
)

API_URL = "https://api.github.com"
OAUTH_URL = "https://github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "second-brain-builder"
TIMEOUT = httpx.Timeout(30.0)
REDACTED = "[redacted]"

ModelT = TypeVar("ModelT", bound=BaseModel)


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of each non-empty secret in ``text``."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def response_json(response: httpx.Response) -> Any:
    """Return the decoded JSON body, or ``None`` if the body is not JSON."""
    try:
        return response.json()
    except ValueError:  # json.JSONDecodeError and UnicodeDecodeError
        return None


def parse_model(model: type[ModelT], data: object, summary: str) -> ModelT:
    """Validate ``data`` as ``model``. Raise ``GitHubError`` without input values.

    The error names the fields that failed, never their values. The
    ``ValidationError``, whose text can contain a token, is neither its cause nor
    its context.
    """
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'body'}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False, include_context=False)
        )
    raise GitHubError(f"{summary}: unexpected response from GitHub ({problems})")


def send(
    client: httpx.Client,
    request: httpx.Request,
    summary: str,
) -> httpx.Response:
    """Send ``request``. Turn every httpx failure into a ``GitHubError``.

    No response arrived (a network error, a timeout, or a protocol error):
    ``GitHubConnectionError``. httpx could not use the response (too many
    redirects, or a body that does not decode): ``GitHubError``.

    The error keeps only the name of the httpx error, not its message, cause, or
    context: some httpx errors quote the request. For example, h11 quotes a
    header value that it rejects, and the Authorization header holds the token.
    """
    try:
        return client.send(request)
    except httpx.TransportError as exc:
        failure = type(exc).__name__
        error_class: type[GitHubError] = GitHubConnectionError
    except httpx.RequestError as exc:  # TooManyRedirects, DecodingError
        failure = type(exc).__name__
        error_class = GitHubError
    raise error_class(f"{summary}: {failure}")


def error_for_response(
    response: httpx.Response,
    summary: str,
    secrets: Iterable[str],
) -> GitHubError:
    """Map a failed response to the typed error for its status."""
    status = response.status_code
    message = _github_message(response_json(response))
    if message is None:
        message = response.reason_phrase or None
    if message is not None:
        message = redact(message, secrets)

    if status == 429 or (status == 403 and _is_rate_limited(response)):
        return GitHubRateLimitError(
            summary,
            status_code=status,
            github_message=message,
            reset_at=_rate_limit_reset(response),
        )
    error_class = _ERRORS_BY_STATUS.get(status, GitHubError)
    return error_class(summary, status_code=status, github_message=message)


_ERRORS_BY_STATUS: dict[int, type[GitHubError]] = {
    401: GitHubAuthError,
    403: GitHubPermissionError,
    404: GitHubNotFoundError,
    422: GitHubValidationError,
}


def _github_message(body: object) -> str | None:
    """Join ``message`` and each ``errors[].message`` from a REST error body."""
    if not isinstance(body, dict):
        return None
    parts: list[str] = []
    message = body.get("message")
    if isinstance(message, str) and message:
        parts.append(message)
    errors = body.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, str) and item:
                parts.append(item)
            elif isinstance(item, dict):
                detail = item.get("message") or item.get("code")
                if isinstance(detail, str) and detail:
                    parts.append(detail)
    return "; ".join(parts) or None


def _is_rate_limited(response: httpx.Response) -> bool:
    # Primary limit: x-ratelimit-remaining is 0. Secondary limit: retry-after is set.
    return response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers


def _rate_limit_reset(response: httpx.Response) -> datetime | None:
    retry_after = response.headers.get("retry-after")
    if retry_after is not None and retry_after.isdigit():
        return datetime.now(timezone.utc) + timedelta(seconds=int(retry_after))
    reset = response.headers.get("x-ratelimit-reset")
    if reset is not None and reset.isdigit():
        return datetime.fromtimestamp(int(reset), tz=timezone.utc)
    return None
