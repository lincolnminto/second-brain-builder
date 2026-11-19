"""Settings and helpers that the OpenAI and Anthropic clients share."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TypeVar

import httpx2
from pydantic import BaseModel, SecretStr, ValidationError

from second_brain_builder.providers.errors import ProviderError, redact, response_error
from second_brain_builder.providers.models import StopReason

# Chat and transcription calls are not streamed, so the read timeout must cover
# the whole answer: a local model on a CPU can take many minutes for one long
# answer. Setting a timeout also stops the Anthropic SDK from refusing large
# non-streaming requests.
REQUEST_TIMEOUT = httpx2.Timeout(900.0, connect=10.0)

# Every Provider lists its models quickly, and the app waits for the list while
# it answers an HTTP request.
LIST_MODELS_TIMEOUT = httpx2.Timeout(30.0, connect=10.0)

# Connection errors and HTTP 408, 409, 429, and 5xx are retried up to this many
# times. A timeout is never retried: the Provider can still be generating the
# answer, and a new request would start the generation again from zero. So the
# worst case for one call is one REQUEST_TIMEOUT.
MAX_RETRIES = 4

_INITIAL_RETRY_DELAY = 0.5
_MAX_RETRY_DELAY = 8.0
# A longer Retry-After is ignored in favor of the normal backoff, as the SDKs do.
_MAX_RETRY_AFTER = 60.0
_RETRY_STATUSES = frozenset({408, 409, 429})

# An answer that stopped for one of these reasons can hold an incomplete tool
# call, so the clients return no tool calls for it.
CUT_OFF_STOP_REASONS = frozenset({StopReason.MAX_TOKENS.value, StopReason.REFUSAL.value})

ModelT = TypeVar("ModelT", bound=BaseModel)
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class Failure:
    """One failed attempt, translated to the typed error that the caller gets."""

    error: ProviderError
    retry: bool
    headers: httpx2.Headers | None = None


def redact_errors(request: Callable[[], ResultT], api_key: SecretStr) -> ResultT:
    """Redact all Provider errors, including errors from response normalization."""
    try:
        return request()
    except ProviderError as exc:
        error = type(exc)(redact(exc.message, api_key), status_code=exc.status_code)
    # Do not chain the original exception: its message can contain the key.
    raise error


def send_with_retries(
    request: Callable[[], ResultT],
    sdk_error: type[Exception],
    translate: Callable[[Exception], Failure],
) -> ResultT:
    """Run ``request``, and retry it while ``translate`` marks the failure as retryable.

    The SDK clients are built with ``max_retries=0``, because the SDKs also retry
    timeouts. ``translate`` runs, and the typed error is raised, outside of the
    ``except`` block, so no SDK exception is chained to the error.
    """
    attempt = 0
    while True:
        try:
            return request()
        except sdk_error as exc:
            failed = exc
        failure = translate(failed)
        if not failure.retry or attempt >= MAX_RETRIES:
            raise failure.error
        time.sleep(retry_delay(attempt, failure.headers))
        attempt += 1


def should_retry_status(response: httpx2.Response) -> bool:
    """Return True for an HTTP error that a later attempt can fix."""
    # The OpenAI and Anthropic APIs send this header to override the default.
    should_retry = response.headers.get("x-should-retry")
    if should_retry == "true":
        return True
    if should_retry == "false":
        return False
    return response.status_code in _RETRY_STATUSES or response.status_code >= 500


def retry_delay(attempt: int, headers: httpx2.Headers | None) -> float:
    """Return the seconds to wait before retry number ``attempt + 1``."""
    retry_after = _retry_after(headers) if headers is not None else None
    if retry_after is not None and 0 <= retry_after <= _MAX_RETRY_AFTER:
        return retry_after
    return min(_INITIAL_RETRY_DELAY * 2.0**attempt, _MAX_RETRY_DELAY)


def _retry_after(headers: httpx2.Headers) -> float | None:
    """Read ``retry-after-ms``, or ``retry-after`` in seconds or as an HTTP date."""
    milliseconds = headers.get("retry-after-ms")
    if milliseconds:
        try:
            return float(milliseconds) / 1000
        except ValueError:
            pass
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (when - datetime.now(timezone.utc)).total_seconds()


def make_http_client(
    transport: httpx2.BaseTransport | None, timeout: httpx2.Timeout
) -> httpx2.Client | None:
    """Return an HTTP client on ``transport``, or None to let the SDK make its own."""
    if transport is None:
        return None
    _check_transport(transport)
    return httpx2.Client(transport=transport, timeout=timeout, follow_redirects=True)


def _check_transport(transport: object) -> None:
    # Untyped callers can pass an httpx transport. The SDKs use httpx2, and an
    # httpx transport fails deep inside it with a bare AssertionError.
    if not isinstance(transport, httpx2.BaseTransport):
        raise TypeError(
            "transport must be an httpx2.BaseTransport, such as httpx2.MockTransport. "
            "The Provider SDKs use httpx2, not httpx."
        )


def parse_response(model: type[ModelT], content: bytes, status_code: int) -> ModelT:
    """Validate a JSON answer. Raises ``ProviderResponseError`` if it does not fit."""
    try:
        return model.model_validate_json(content)
    except ValidationError as exc:
        error = response_error(exc, status_code)
    raise error
