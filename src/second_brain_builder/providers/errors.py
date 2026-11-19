"""Typed Provider errors.

Error messages never contain the API key. The clients build and raise these
errors outside of the SDK's ``except`` block, so no SDK exception (which holds the
HTTP request and the raw response body) is chained to them as ``__cause__`` or
``__context__``, even if building the error fails.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import SecretStr, ValidationError

__all__ = [
    "ProviderAuthError",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderRateLimitError",
    "ProviderResponseError",
]

_MAX_DETAIL_CHARS = 500
_REDACTED = "[redacted]"


class ProviderError(Exception):
    """A Provider call failed.

    ``status_code`` is the HTTP status of the Provider's answer, or None when
    there was no answer.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ProviderConnectionError(ProviderError):
    """The Provider could not be reached, or it did not answer in time."""


class ProviderAuthError(ProviderError):
    """The Provider rejected the API key (HTTP 401 or 403)."""


class ProviderNotFoundError(ProviderError):
    """HTTP 404: usually a wrong base URL or an unknown model name."""


class ProviderRateLimitError(ProviderError):
    """HTTP 429 that continued after every retry."""


class ProviderResponseError(ProviderError):
    """The Provider answered, but the answer does not have the expected format."""


def redact(text: str, api_key: SecretStr) -> str:
    """Replace each occurrence of the API key in ``text``."""
    secret = api_key.get_secret_value()
    return text.replace(secret, _REDACTED) if secret else text


def display_url(url: str) -> str:
    """Return ``url`` as ``scheme://host[:port]/path``, without user info or query.

    It never raises: it is called while a failed call is being reported.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "the configured base URL"
    host_and_port = parts.netloc.rpartition("@")[2]
    return f"{parts.scheme}://{host_and_port}{parts.path}"


def status_error(status_code: int, body: object, api_key: SecretStr) -> ProviderError:
    """Map an HTTP error answer to a typed error with a short, redacted message."""
    detail = _error_detail(body)
    suffix = f": {_clean(detail, api_key)}" if detail else "."
    if status_code in (401, 403):
        return ProviderAuthError(
            f"The Provider rejected the API key (HTTP {status_code}){suffix}",
            status_code=status_code,
        )
    if status_code == 404:
        return ProviderNotFoundError(
            f"The Provider returned HTTP 404. Check the base URL and the model name{suffix}",
            status_code=status_code,
        )
    if status_code == 429:
        return ProviderRateLimitError(
            f"The Provider is limiting the request rate (HTTP 429){suffix}",
            status_code=status_code,
        )
    return ProviderError(
        f"The Provider returned HTTP {status_code}{suffix}", status_code=status_code
    )


def connection_error(
    base_url: str, *, timed_out: bool, api_key: SecretStr
) -> ProviderConnectionError:
    """Map a network failure or a timeout to a typed error."""
    where = redact(display_url(base_url), api_key)
    if timed_out:
        return ProviderConnectionError(f"The Provider at {where} did not answer in time.")
    return ProviderConnectionError(f"Could not connect to the Provider at {where}.")


def response_error(error: ValidationError, status_code: int) -> ProviderResponseError:
    """Map a response that failed validation. The message has no response content."""
    first = error.errors(include_url=False, include_context=False, include_input=False)[0]
    location = ".".join(str(part) for part in first["loc"]) or "body"
    return ProviderResponseError(
        "The Provider answer does not have the expected format "
        f"({location}: {first['msg']}). Check the base URL and the Provider kind.",
        status_code=status_code,
    )


def _error_detail(body: object) -> str | None:
    """Find the human-readable message in an error body from either API family."""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message
        if isinstance(error, str):
            return error
        for key in ("message", "detail"):
            value = body.get(key)
            if isinstance(value, str):
                return value
        return None
    if isinstance(body, str):
        return body
    return None


def _clean(detail: str, api_key: SecretStr) -> str:
    text = " ".join(redact(detail, api_key).split())
    if len(text) > _MAX_DETAIL_CHARS:
        text = text[: _MAX_DETAIL_CHARS - 3] + "..."
    return text
