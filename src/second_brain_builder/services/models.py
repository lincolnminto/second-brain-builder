"""Input and output models of the use cases.

The API builds its response schemas from these models, and the MCP server returns
them as tool results. Paths in a Brain are ``str`` from the Brain root without a
leading ``/`` (``wiki/entities/grey-heron.md``). Datetimes are aware UTC values.
No output model holds a credential, except ``CreatedApiToken.secret``, which is
shown once.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from second_brain_builder.domain import (
    BrainStatus,
    JobKind,
    JobStatus,
    SourceStatus,
    TaskMode,
    TaskName,
)
from second_brain_builder.providers import ProviderKind

TokenKind = Literal["app", "pat"]
"""``app``: a GitHub App user token from the device flow. ``pat``: a personal access token."""


class _View(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- setup and worker ------------------------------------------------------------


class WorkerStatus(_View):
    """Whether the worker process runs. The UI shows a warning when it is offline."""

    online: bool
    worker_id: str | None = None
    last_seen_at: datetime | None = None


class HealthStatus(_View):
    """``ok`` is True when the database answers. The worker can be offline."""

    ok: bool
    database: bool
    worker: WorkerStatus


class AuthStatus(_View):
    """Whether the web UI needs a login (``SBB_PASSWORD`` is set)."""

    login_required: bool


class SetupStatus(_View):
    """What the onboarding screens need to know."""

    github_connected: bool
    github_login: str | None
    github_app_configured: bool
    """``SBB_GITHUB_CLIENT_ID`` is set, so the device flow can start."""
    brain_count: int
    provider_count: int
    ingest_configured: bool
    """An ingest Provider and model are set, so new Sources are ingested."""
    worker: WorkerStatus


# --- GitHub -----------------------------------------------------------------------


class GitHubAccountView(_View):
    """The connected GitHub account, without its token."""

    user_id: int
    login: str
    noreply_email: str
    token_kind: TokenKind
    expires_at: datetime | None
    """When the access token expires. Services refresh it before that time."""
    connected_at: datetime


class DeviceFlowStart(_View):
    """Show ``user_code`` and ``verification_uri`` to the user, then poll every ``interval`` s.

    The device code stays on the server, encrypted; the client never sees it.
    """

    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


DevicePollStatus = Literal["pending", "slow_down", "denied", "expired", "connected"]


class DeviceFlowPoll(_View):
    """The result of one device flow poll.

    ``pending`` and ``slow_down``: poll again after ``interval`` seconds.
    ``denied`` and ``expired``: start again. ``connected``: ``account`` is set.
    """

    status: DevicePollStatus
    interval: int | None = None
    account: GitHubAccountView | None = None


class RepoView(_View):
    """A GitHub repository that can hold a Brain. ``brain_id`` is set if one does."""

    full_name: str
    private: bool
    default_branch: str
    brain_id: str | None = None


# --- Providers and task settings ----------------------------------------------------


class ProviderInput(BaseModel):
    """A new Provider. ``api_key`` can be empty for a local server that needs no key."""

    model_config = ConfigDict(hide_input_in_errors=True)

    name: str
    kind: ProviderKind
    base_url: str
    api_key: SecretStr = SecretStr("")


class ProviderUpdate(BaseModel):
    """Changes to a Provider. ``None`` keeps a field; an empty ``api_key`` removes the key.

    A change of ``kind`` or ``base_url`` needs ``api_key`` too when the Provider
    has a stored key: the key goes only to the endpoint that it was entered for.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    name: str | None = None
    kind: ProviderKind | None = None
    base_url: str | None = None
    api_key: SecretStr | None = None


class ProviderView(_View):
    """A Provider, with its API key masked.

    ``api_key_last4`` holds the last four characters of the key, and only when the
    key has at least ``MASK_MIN_KEY_LENGTH`` characters, so that a short key is
    never shown in full.
    """

    id: int
    name: str
    kind: ProviderKind
    base_url: str
    has_api_key: bool
    api_key_last4: str | None
    created_at: datetime


class ProviderTestResult(_View):
    """The result of a Provider test: one call that lists the Provider's models."""

    ok: bool
    message: str
    model_count: int | None = None


class TaskSettingInput(BaseModel):
    """Where a task runs.

    ``local`` runs in the worker (transcription only: Whisper, ``model`` is the
    Whisper model or ``None`` for the default). ``provider`` needs ``provider_id``
    and ``model``.
    """

    mode: TaskMode
    provider_id: int | None = None
    model: str | None = None


class TaskSettingsInput(BaseModel):
    """The full task settings, as ``PUT /settings/tasks`` replaces them.

    ``None`` resets a task to its default: no ingest Provider (Sources are
    converted but not ingested), and local transcription.
    """

    ingest: TaskSettingInput | None = None
    transcription: TaskSettingInput | None = None


class TaskSettingView(_View):
    """The setting of one task. ``configured`` is False when the task cannot run."""

    task: TaskName
    mode: TaskMode
    provider_id: int | None
    model: str | None
    configured: bool


class TaskSettingsView(_View):
    ingest: TaskSettingView
    transcription: TaskSettingView


# --- Brains --------------------------------------------------------------------------


class BrainCreate(BaseModel):
    """A new Brain: in a new private repository, or in an existing one.

    Give exactly one of ``new_repo_name`` (a repository that the connected user
    gets) and ``existing_repo`` (``owner/name``). An existing public repository
    needs ``confirm_public=True``.
    """

    name: str
    language: str = "en"
    new_repo_name: str | None = None
    existing_repo: str | None = None
    confirm_public: bool = False

    @model_validator(mode="after")
    def _one_repo(self) -> BrainCreate:
        if (self.new_repo_name is None) == (self.existing_repo is None):
            raise ValueError("give exactly one of new_repo_name and existing_repo")
        return self


class BrainView(_View):
    """A Brain. ``slug`` names its default local clone directory for agents."""

    id: str
    name: str
    slug: str
    repo_full_name: str
    repo_url: str
    language: str
    status: BrainStatus
    created_at: datetime
    last_synced_at: datetime | None
    source_count: int


class LfsUsageView(_View):
    """Git LFS storage that the Brain's repository and its owner use, in bytes.

    GitHub counts the quota per repository owner, so ``owner_bytes`` adds up every
    Brain of ``owner`` and the uploads in the queue.
    """

    brain_id: str
    brain_bytes: int
    owner: str
    owner_bytes: int
    quota_bytes: int
    over_quota: bool


# --- Jobs ------------------------------------------------------------------------------


class JobView(_View):
    id: int
    brain_id: str | None
    kind: JobKind
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    input_tokens: int | None
    output_tokens: int | None


class JobEventView(_View):
    id: int
    job_id: int
    brain_id: str | None
    created_at: datetime
    kind: str
    data: dict[str, Any]


class BrainCreated(_View):
    brain: BrainView
    job: JobView


# --- Sources and Folders ---------------------------------------------------------------


class FolderNode(_View):
    """A Folder and its subfolders. The root node has ``name`` and ``path`` ``""``.

    ``source_count`` counts the Sources directly in the Folder,
    ``total_source_count`` also those in its subfolders.
    """

    name: str
    path: str
    source_count: int
    total_source_count: int
    children: list[FolderNode] = Field(default_factory=list)


class IngestEstimate(_View):
    """Parts remaining and estimated input plus output tokens for their Ingest."""

    parts_total: int
    parts_done: int
    estimated_tokens: int


class SourceView(_View):
    """A Source. ``slug`` is its id, unique in the Brain."""

    slug: str
    brain_id: str
    folder: str
    path: str | None
    """The Source file, for example ``sources/books/dune/dune.epub``."""
    canonical_path: str
    title: str | None
    media_type: str | None
    size_bytes: int | None
    sha256: str | None
    status: SourceStatus
    parts_done: int | None = None
    parts_total: int | None = None
    created_at: datetime
    updated_at: datetime


class PageRef(_View):
    path: str
    type: str
    title: str


class SourceDetail(_View):
    """A Source and the Wiki Pages that cite it, sorted by path."""

    source: SourceView
    citing_pages: list[PageRef]


class AddSourceResult(_View):
    """The upload was staged and a ``source.add`` job is in the queue."""

    job: JobView
    sha256: str
    size_bytes: int
    media_type: str


class LocalFile(_View):
    """A file of a Brain clone to send to the client.

    ``inline`` is True only for media types that a browser can show without
    running content from the file (images, PDF, plain text). Send every other
    type as an attachment: Brain content is untrusted.
    """

    path: Path
    filename: str
    media_type: str
    size_bytes: int
    inline: bool


# --- Pages --------------------------------------------------------------------------------


class PageSummaryView(_View):
    path: str
    type: str
    title: str
    description: str | None


class SourceRefView(_View):
    id: str
    resource: str
    title: str | None


class PageView(_View):
    """One Markdown file of a Brain: an OKF page, ``index.md``, or ``log.md``.

    ``type`` is ``""`` for a file without a ``type`` (``index.md``, ``AGENTS.md``).
    ``body`` is untrusted Markdown: render it without raw HTML.
    """

    path: str
    type: str
    title: str
    description: str | None
    tags: list[str]
    sources: list[SourceRefView]
    generated_by: str | None
    generated_at: datetime | None
    body: str


class GraphNodeView(_View):
    path: str
    type: str
    title: str


class GraphEdgeView(_View):
    from_path: str
    to_path: str


class GraphView(_View):
    nodes: list[GraphNodeView]
    edges: list[GraphEdgeView]


class SearchHitView(_View):
    """A search hit. ``snippet`` is escaped Markdown; render it as Markdown."""

    path: str
    title: str
    type: str
    snippet: str
    score: float


# --- API tokens and agents --------------------------------------------------------------


class ApiTokenView(_View):
    """An API token without its secret. ``prefix`` identifies it in the UI."""

    id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


class CreatedApiToken(_View):
    """A new API token. ``secret`` is shown once and never stored."""

    token: ApiTokenView
    secret: str


class BrainCloneCommand(_View):
    brain_id: str
    name: str
    command: str


class AgentSetup(_View):
    """What an agent needs: the MCP endpoint, the skill, and a clone of each Brain."""

    mcp_url: str
    mcp_add_command: str
    """Claude Code command that adds the MCP server. Replace ``<API_TOKEN>``."""
    skill_url: str
    skill_install_command: str
    clone_commands: list[BrainCloneCommand]


class AnswerInput(BaseModel):
    """A Query answer to save as a synthesis Wiki Page.

    The body cites Sources with footnotes whose labels are Source ids
    (``[^dune]``), and ``source_ids`` lists the Sources that it cites.
    """

    title: str
    body: str
    source_ids: list[str]
    description: str = ""
    tags: list[str] = Field(default_factory=list)
