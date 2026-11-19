"""Typed errors of the use cases.

The HTTP API maps each class to a status code, and the MCP server maps each one to
a tool error. Every error has a stable ``code`` for clients and a ``message`` for
people. No message contains a credential.

Suggested HTTP mapping (the API owns the final choice):

- ``NotFoundError``: 404.
- ``ConflictError`` and ``DuplicateSourceError``: 409.
- ``InvalidInputError``: 422; ``UploadTooLargeError``: 413.
- ``ConfirmationRequiredError`` (``QuotaExceededError``, ``PublicRepoError``): 409,
  with ``code`` and the details, so the UI can ask the user and send the request
  again with the confirmation flag.
- ``NotConnectedError`` and ``NotConfiguredError``: 409.
- ``AuthError``: 401 when ``service`` is ``"app"``; else 502, because the user of
  this app is signed in and an external service rejected a stored credential.
- ``UpstreamError``: 502, or 503 when ``retry_at`` is set (a rate limit).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Literal

if TYPE_CHECKING:
    from second_brain_builder.services.models import JobView, SourceView

AuthService = Literal["app", "github", "provider"]
"""Who rejected a credential: this app (an API token), GitHub, or an LLM Provider."""

ExternalService = Literal["github", "provider"]
"""An external service that a use case calls."""


class ServiceError(Exception):
    """Base class of every use-case error."""

    code: ClassVar[str] = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(ServiceError):
    """The Brain, Source, page, job, Provider, or token does not exist."""

    code = "not_found"


class ConflictError(ServiceError):
    """The request conflicts with the current state, for example a job that already runs."""

    code = "conflict"


class DuplicateSourceError(ConflictError):
    """The Brain already has a Source with the same SHA-256, or one is being added.

    ``existing`` is the Source that has the same content. It is ``None`` when the
    other copy is still in the queue: ``pending_job`` is then the job that adds it.
    """

    code = "duplicate_source"

    def __init__(
        self,
        message: str,
        *,
        existing: SourceView | None = None,
        pending_job: JobView | None = None,
    ) -> None:
        super().__init__(message)
        self.existing = existing
        self.pending_job = pending_job


class InvalidInputError(ServiceError):
    """The input is not valid: a bad name, path, folder, token format, or setting."""

    code = "invalid_input"


class UploadTooLargeError(InvalidInputError):
    """The upload is larger than ``SBB_MAX_UPLOAD_BYTES``. Nothing was kept."""

    code = "upload_too_large"

    def __init__(self, message: str, *, max_bytes: int) -> None:
        super().__init__(message)
        self.max_bytes = max_bytes


class ConfirmationRequiredError(ServiceError):
    """The request is valid but needs an explicit confirmation from the user.

    Send it again with the confirmation flag that the subclass names.
    """

    code = "confirmation_required"


class QuotaExceededError(ConfirmationRequiredError):
    """The upload would take the Git LFS usage of the repository owner over the quota.

    Send it again with ``confirm_over_quota=True`` to add it anyway. All values are
    bytes. ``used_bytes`` includes uploads that are still in the queue.
    """

    code = "quota_exceeded"

    def __init__(
        self, message: str, *, used_bytes: int, upload_bytes: int, quota_bytes: int
    ) -> None:
        super().__init__(message)
        self.used_bytes = used_bytes
        self.upload_bytes = upload_bytes
        self.quota_bytes = quota_bytes


class PublicRepoError(ConfirmationRequiredError):
    """The existing repository is public. Send it again with ``confirm_public=True``."""

    code = "public_repo"

    def __init__(self, message: str, *, repo_full_name: str) -> None:
        super().__init__(message)
        self.repo_full_name = repo_full_name


class NotConnectedError(ServiceError):
    """No GitHub account is connected."""

    code = "not_connected"


class NotConfiguredError(ServiceError):
    """A setting that the request needs is missing.

    Examples: ``SBB_GITHUB_CLIENT_ID`` for the device flow, or an ingest Provider
    for an Ingest.
    """

    code = "not_configured"


class AuthError(ServiceError):
    """A credential was rejected or cannot be used.

    ``service`` is ``"app"`` for an API token of this app, ``"github"`` when GitHub
    rejected the connected account's token (connect GitHub again), and
    ``"provider"`` when an LLM Provider rejected its API key.
    """

    code = "auth"

    def __init__(self, message: str, *, service: AuthService) -> None:
        super().__init__(message)
        self.service = service


class UpstreamError(ServiceError):
    """GitHub or a Provider failed: no answer, a rate limit, or an unexpected answer.

    ``status_code`` is the HTTP status of the answer, if one arrived.
    ``retry_at`` is when a rate limit ends, if the service said so.
    """

    code = "upstream"

    def __init__(
        self,
        message: str,
        *,
        service: ExternalService,
        status_code: int | None = None,
        retry_at: datetime | None = None,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.status_code = status_code
        self.retry_at = retry_at
