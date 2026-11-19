"""Typed errors for GitHub calls.

Each error carries the HTTP status (``status_code``) and GitHub's own message
(``github_message``) when a response arrived. No error carries a token: tokens
travel only in headers and form bodies, never in URLs, and a secret that GitHub
echoes back in a message is redacted before the message reaches an error.
"""

from datetime import datetime
from typing import Any


class GitHubError(Exception):
    """A GitHub call failed.

    ``status_code`` is ``None`` when the failure has no HTTP status: no response
    arrived, or httpx could not use the response (too many redirects, or a body
    that does not decode). ``github_message`` is the message from the response
    body, if the body had one.

    Every error survives ``pickle`` and ``copy`` with all of its attributes, so it
    can cross a process boundary, for example from a worker process.
    """

    def __init__(
        self,
        summary: str,
        *,
        status_code: int | None = None,
        github_message: str | None = None,
    ) -> None:
        self.summary = summary
        self.status_code = status_code
        self.github_message = github_message
        super().__init__(_format_message(summary, status_code, github_message))

    def __reduce__(self) -> tuple[Any, ...]:
        # By default, pickle and copy rebuild an exception as cls(*self.args). Here
        # args holds only the formatted message, and GitHubOAuthError requires the
        # keyword-only argument ``error``, so that call fails. Rebuild the error
        # without __init__, then restore every attribute from __dict__.
        return (_rebuild, (type(self), self.args), self.__dict__)


class GitHubConnectionError(GitHubError):
    """No response arrived: a network error or a timeout."""


class GitHubAuthError(GitHubError):
    """HTTP 401. The token is not valid, has expired, or was revoked."""


class GitHubPermissionError(GitHubError):
    """HTTP 403. The token cannot do this action or cannot see this resource."""


class GitHubNotFoundError(GitHubError):
    """HTTP 404. The resource does not exist, or the token cannot see it."""


class GitHubValidationError(GitHubError):
    """HTTP 422. GitHub rejected the input, for example a repo name that is taken."""


class GitHubRateLimitError(GitHubError):
    """HTTP 403 or 429 because of a rate limit.

    ``reset_at`` is the UTC time after which a retry can succeed, if GitHub sent it.
    """

    def __init__(
        self,
        summary: str,
        *,
        status_code: int | None = None,
        github_message: str | None = None,
        reset_at: datetime | None = None,
    ) -> None:
        super().__init__(summary, status_code=status_code, github_message=github_message)
        self.reset_at = reset_at


class GitHubOAuthError(GitHubError):
    """The OAuth endpoint (device flow or token refresh) returned an ``error`` code.

    ``error`` is GitHub's code, for example ``bad_refresh_token`` or
    ``incorrect_device_code``. ``description`` is GitHub's ``error_description``.
    """

    def __init__(
        self,
        summary: str,
        *,
        error: str,
        description: str | None = None,
        status_code: int | None = None,
    ) -> None:
        github_message = f"{error}: {description}" if description else error
        super().__init__(summary, status_code=status_code, github_message=github_message)
        self.error = error
        self.description = description


class DeviceFlowDisabledError(GitHubOAuthError):
    """The GitHub App does not have the device flow enabled in its settings."""


def _rebuild(cls: type[GitHubError], args: tuple[Any, ...]) -> GitHubError:
    """Create an empty ``cls`` with ``args``. ``__reduce__`` then restores its attributes."""
    return cls.__new__(cls, *args)


def _format_message(summary: str, status_code: int | None, github_message: str | None) -> str:
    parts = [summary]
    if status_code is not None:
        parts.append(f"HTTP {status_code}")
    if github_message:
        parts.append(github_message)
    return ": ".join(parts)
