"""Request and response models of the HTTP API.

The OpenAPI document (``/api/openapi.json``) is built from these models, and the web
UI generates its TypeScript types from it. Most responses are the output models of
``services``, re-exported here under the same names so that the schema names stay
stable. The models defined here exist only at the HTTP boundary: the error body,
login, and small request bodies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from second_brain_builder.services import (
    AddSourceResult,
    AgentSetup,
    ApiTokenView,
    BrainCreate,
    BrainCreated,
    BrainView,
    CitationPassage,
    CreatedApiToken,
    DeviceFlowPoll,
    DeviceFlowStart,
    DocumentOutline,
    DocumentSegments,
    FolderNode,
    GitHubAccountView,
    GraphView,
    HealthStatus,
    IngestEstimate,
    JobEventView,
    JobView,
    LfsUsageView,
    PageSummaryView,
    PageView,
    ProviderInput,
    ProviderTestResult,
    ProviderUpdate,
    ProviderView,
    RepoView,
    SearchHitView,
    SetupStatus,
    SourceDetail,
    SourceView,
    TaskSettingsInput,
    TaskSettingsView,
)

__all__ = [
    "AddSourceResult",
    "AgentSetup",
    "ApiTokenView",
    "AuthState",
    "BrainCreate",
    "BrainCreated",
    "BrainView",
    "CitationPassage",
    "CreatedApiToken",
    "DeviceFlowPoll",
    "DeviceFlowStart",
    "DocumentOutline",
    "DocumentSegments",
    "ErrorResponse",
    "FolderCreate",
    "FolderNode",
    "FolderRename",
    "GitHubAccountView",
    "GitHubPatInput",
    "GraphView",
    "HealthStatus",
    "IngestEstimate",
    "InstallUrl",
    "JobEventView",
    "JobView",
    "LfsUsageView",
    "LoginRequest",
    "PageSummaryView",
    "PageView",
    "ProviderInput",
    "ProviderTestResult",
    "ProviderUpdate",
    "ProviderView",
    "RepoView",
    "SearchHitView",
    "SetupStatus",
    "SourceDetail",
    "SourceIngest",
    "SourceMove",
    "SourceView",
    "TaskSettingsInput",
    "TaskSettingsView",
    "TokenCreate",
    "ValidationIssue",
]


class ValidationIssue(BaseModel):
    """One problem in a request that the API could not parse.

    ``location`` is where the problem is, for example ``["body", "name"]`` or
    ``["query", "limit"]``. The value that was sent is never repeated.
    """

    location: list[str | int]
    message: str
    type: str


class ErrorResponse(BaseModel):
    """The body of every error response of ``/api`` and ``/mcp``.

    ``code`` is stable and meant for clients; ``message`` is for people. The other
    fields are set only for the codes that need them:

    - ``duplicate_source`` (409): ``existing_source``, or ``pending_job`` when the
      same file is still being added.
    - ``upload_too_large`` (413): ``max_bytes``.
    - ``quota_exceeded`` (409): ``used_bytes``, ``upload_bytes``, ``quota_bytes``.
      Send the upload again with ``confirm_over_quota=true``.
    - ``public_repo`` (409): ``repo_full_name``. Send again with ``confirm_public``.
    - ``auth`` (401 or 502) and ``upstream`` (502 or 503): ``service``, and
      ``retry_at`` when a rate limit ends.
    - ``invalid_request`` (422): ``issues``.
    """

    code: str
    message: str
    existing_source: SourceView | None = None
    pending_job: JobView | None = None
    max_bytes: int | None = None
    used_bytes: int | None = None
    upload_bytes: int | None = None
    quota_bytes: int | None = None
    repo_full_name: str | None = None
    service: str | None = None
    retry_at: datetime | None = None
    issues: list[ValidationIssue] | None = None


class AuthState(BaseModel):
    """Whether the web UI needs a login, and whether this request has one.

    ``authenticated`` is True when no login is needed, or when the request has a
    valid session cookie or API token.
    """

    login_required: bool
    authenticated: bool


class LoginRequest(BaseModel):
    """The web UI password (``SBB_PASSWORD``)."""

    model_config = ConfigDict(hide_input_in_errors=True)

    password: SecretStr


class GitHubPatInput(BaseModel):
    """A GitHub personal access token that the user pasted. Whitespace around it is removed."""

    model_config = ConfigDict(hide_input_in_errors=True)

    token: SecretStr


class InstallUrl(BaseModel):
    """The GitHub page where the user installs the GitHub App on repositories."""

    url: str


class SourceMove(BaseModel):
    """The target Folder of a Source move. ``""`` is the root of ``sources/``."""

    folder: str


class FolderCreate(BaseModel):
    """A new Folder, such as ``work/clients``. Each part becomes a slug."""

    folder: str


class FolderRename(BaseModel):
    """A Folder and its new path. The new path can be in another Folder."""

    folder: str
    new_folder: str


class SourceIngest(BaseModel):
    """Resume matching progress by default, with an optional per-run token budget."""

    resume: bool = True
    mode: Literal["auto", "sequential", "map_reduce"] = "auto"
    max_tokens: int | None = Field(default=None, gt=0)


class TokenCreate(BaseModel):
    """A new API token. The name says which agent or machine uses it."""

    name: str
