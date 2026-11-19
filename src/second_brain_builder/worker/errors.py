"""Job errors and the error text that a failed job stores.

The text goes to the ``jobs`` table and to the UI, so it is written for people and
never holds a credential: known secrets are replaced, and so is anything that looks
like a token, as a second line of defense.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Iterable
from urllib.parse import quote

from pydantic import ValidationError

from second_brain_builder.brains import BrainSettingsError, GitConflictError, GitError
from second_brain_builder.conversion import (
    ConversionError,
    DrmProtectedError,
    UnsupportedFormatError,
)
from second_brain_builder.github import GitHubError
from second_brain_builder.ingest import InvalidAnswerError
from second_brain_builder.okf import OKFError
from second_brain_builder.providers import ProviderError
from second_brain_builder.services import ServiceError
from second_brain_builder.services.github import github_service_error
from second_brain_builder.worker.localgit import LocalGitError

REDACTED = "***"
MAX_ERROR_CHARS = 2000

# Token shapes that must never reach the jobs table, whatever produced the text.
_TOKEN_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}"),  # GitHub fine-grained tokens
    re.compile(r"\bsbb_[A-Za-z0-9_-]{16,}"),  # API tokens of this app
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),  # common LLM API keys
    re.compile(r"(?i)(authorization:\s*(?:basic|bearer|token)\s+)\S+"),
    re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|token)\s*[=:]\s*)[^\s,;&]+"),
    re.compile(r"(://)[^/\s@:]+:[^/\s@]+@"),  # credentials in a URL
)


class JobError(Exception):
    """A job failed for a reason that the message explains to the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def secret_forms(secret: str) -> list[str]:
    """Every form in which a token can show up in output: plain, URL-quoted, Basic auth."""
    if not secret:
        return []
    basic = base64.b64encode(f"x-access-token:{secret}".encode()).decode("ascii")
    return [secret, quote(secret, safe=""), basic]


def scrub(text: str, secrets: Iterable[str]) -> str:
    """Replace each known secret, and each token-like string, with ``***``."""
    for secret in sorted({form for form in secrets if form}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(
            lambda match: (match.group(1) if match.groups() else "") + REDACTED, text
        )
    return text


def describe_error(exc: Exception, secrets: Iterable[str]) -> str:
    """The text that a failed job stores: what failed, for the user, without secrets."""
    message = _message(exc)
    text = scrub(message, secrets)
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 3] + "..."
    return text


def _message(exc: Exception) -> str:
    if isinstance(exc, JobError):
        return exc.message
    if isinstance(exc, ServiceError):
        return exc.message
    if isinstance(exc, GitHubError):
        return github_service_error(exc).message
    if isinstance(exc, GitConflictError):
        return (
            "The Brain changed on GitHub while this job ran, and the changes conflict. "
            f"Run the job again. ({exc})"
        )
    if isinstance(exc, (GitError, LocalGitError)):
        return f"A git command failed: {exc}"
    if isinstance(exc, UnsupportedFormatError):
        return f"This file type cannot be converted: {exc}"
    if isinstance(exc, DrmProtectedError):
        return f"The file is protected by DRM or a password, so it cannot be converted: {exc}"
    if isinstance(exc, ConversionError):
        return f"The conversion failed: {exc}"
    if isinstance(exc, ProviderError):
        return f"The LLM Provider failed: {exc}"
    if isinstance(exc, InvalidAnswerError):
        return "The answer cannot be saved: " + "; ".join(exc.problems)
    if isinstance(exc, (OKFError, BrainSettingsError)):
        return f"A file of the Brain cannot be read: {exc}"
    if isinstance(exc, ValidationError):
        return "The job has a payload that this worker cannot read."
    return f"Unexpected error ({type(exc).__name__}): {exc}"
