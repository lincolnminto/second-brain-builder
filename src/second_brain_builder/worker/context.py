"""The state of one running job, shared by its handler and the runner."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, TypeVar

from second_brain_builder.brains import SETTINGS_PATH
from second_brain_builder.conversion import ConversionOptions
from second_brain_builder.conversion.formats import SUPPORTED_FORMATS, SourceKind
from second_brain_builder.domain import Usage
from second_brain_builder.github import RepoInfo
from second_brain_builder.ingest import IngestEvent
from second_brain_builder.services import (
    AppContext,
    GitHubAuth,
    IngestModel,
    JobPayload,
    get_github_auth,
    get_ingest_model,
    get_task_settings,
    get_transcriber,
    github_client,
    parse_payload,
)
from second_brain_builder.services.providers import provider_config
from second_brain_builder.store import BrainRow, Job
from second_brain_builder.worker.clone import BrainClone
from second_brain_builder.worker.errors import JobError, describe_error, secret_forms
from second_brain_builder.worker.rows import load_brain

_log = logging.getLogger(__name__)

P = TypeVar("P", bound=JobPayload)

PAGE_EVENTS = frozenset({"page_written", "page_deleted"})


@dataclass
class JobRun:
    """One job while a worker thread runs it.

    Handlers read the payload, open the Brain clone, and emit events through it.
    The runner uses it to clean up after a failure: ``clone`` is restored to the
    remote state, the failure hooks run, and ``secrets`` are removed from the error.
    """

    ctx: AppContext
    job: Job
    worker_id: str
    secrets: list[str] = field(default_factory=list)
    """Credentials that this job used. They never appear in its error text."""
    usage: Usage | None = None
    """LLM tokens that the job used, stored with the job."""
    clone: BrainClone | None = None
    """The clone that the job opened, if any."""
    touched: set[PurePosixPath] = field(default_factory=set)
    """Pages that events reported as written or deleted."""
    _failure_hooks: list[Callable[[], None]] = field(default_factory=list)
    _github: GitHubAuth | None = None

    # --- input ------------------------------------------------------------------------

    def payload(self, model: type[P]) -> P:
        """The job payload, validated against the model of its kind.

        Raises:
            JobError: The payload is for another kind, or names another Brain.
            pydantic.ValidationError: The payload does not match its model.
        """
        parsed = parse_payload(self.job.kind, self.job.payload)
        if not isinstance(parsed, model):
            raise JobError(f"A {self.job.kind} job cannot have a {model.__name__}.")
        if self.job.brain_id is not None and parsed.brain_id != self.job.brain_id:
            raise JobError("The payload of this job names another Brain than the job.")
        return parsed

    def brain(self, brain_id: str) -> BrainRow:
        """The Brain row. Raises ``NotFoundError`` when the Brain was disconnected."""
        return load_brain(self.ctx, brain_id)

    # --- events -----------------------------------------------------------------------

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        """Add an event for this job, for example ``page_written``."""
        if kind in PAGE_EVENTS and isinstance(data.get("path"), str):
            self.touched.add(PurePosixPath(data["path"]))
        self.ctx.jobs.emit(self.job.id, kind, data)

    def emit_ingest_event(self, event: IngestEvent) -> None:
        """Pass an ``IngestEvent`` on as a job event with the same kind and data."""
        self.emit(event.kind, event.data)

    def progress(self, step: str, **data: Any) -> None:
        """A ``progress`` event for a step of a job that is not an Ingest: ``{"step": ...}``."""
        self.emit("progress", {"step": step, **data})

    # --- credentials and models --------------------------------------------------------

    def github_auth(self) -> GitHubAuth:
        """Git credentials and the noreply identity of the connected GitHub account."""
        if self._github is None:
            self._github = get_github_auth(self.ctx)
            self.secrets.extend(secret_forms(self._github.auth.token.get_secret_value()))
        return self._github

    def repo_info(self, repo_full_name: str) -> RepoInfo:
        """The GitHub repository of a Brain, with its clone URL.

        Raises:
            JobError: The name is not ``owner/name``.
            GitHubError: GitHub failed.
        """
        self.github_auth()
        client = github_client(self.ctx)
        try:
            return client.get_repo(repo_full_name)
        except ValueError:
            raise JobError(
                f"Not a repository name in the form owner/name: {repo_full_name!r}."
            ) from None

    def ingest_model(self) -> IngestModel | None:
        """The chat model for Ingest, or ``None`` when no ingest Provider is set."""
        self._remember_provider(get_task_settings(self.ctx).ingest.provider_id)
        model = get_ingest_model(self.ctx)
        return model

    def _remember_provider(self, provider_id: int | None) -> None:
        if provider_id is not None:
            key = provider_config(self.ctx, provider_id).api_key.get_secret_value()
            self.secrets.extend(secret_forms(key))

    def conversion_options(self, media_type: str) -> ConversionOptions:
        """Conversion settings: the model cache, and the Provider transcriber for speech."""
        os.environ["HF_HOME"] = str(self.ctx.settings.models_dir)
        fmt = SUPPORTED_FORMATS.get(media_type)
        needs_speech = fmt is not None and fmt.kind in (SourceKind.AUDIO, SourceKind.VIDEO)
        if needs_speech:
            self._remember_provider(get_task_settings(self.ctx).transcription.provider_id)
        transcriber = get_transcriber(self.ctx) if needs_speech else None
        return ConversionOptions(models_dir=self.ctx.settings.models_dir, transcriber=transcriber)

    # --- the clone --------------------------------------------------------------------

    def open_clone(
        self, brain: BrainRow, *, clone_url: str | None = None, require_brain: bool = True
    ) -> BrainClone:
        """Prepare the clone of ``brain`` for this job (see ``BrainClone.prepare``).

        Without ``clone_url``, a missing clone is cloned from the URL that GitHub
        gives for the Brain's repository. With ``require_brain``, the clone must
        have ``.brain/brain.yaml``.

        Raises:
            JobError: ``require_brain`` is set and the repository is not a Brain.
            GitError, GitHubError, ServiceError: git or GitHub failed, or GitHub is
                not connected.
        """
        clone = BrainClone(self.ctx, brain.id, self.github_auth())
        self.clone = clone

        def url() -> str:
            self.progress("clone")
            if clone_url is not None:
                return clone_url
            return self.repo_info(brain.repo_full_name).clone_url

        clone.prepare(url)
        if require_brain and not (clone.root / SETTINGS_PATH).is_file():
            raise JobError(
                f"The repository {brain.repo_full_name} has no {SETTINGS_PATH}, so it is not "
                "a Brain. Retry the job that created the Brain."
            )
        return clone

    # --- failure ----------------------------------------------------------------------

    def on_failure(self, hook: Callable[[], None]) -> None:
        """Run ``hook`` if the job fails, after the clone is restored."""
        self._failure_hooks.append(hook)

    def clean_up_after_failure(self) -> None:
        """Restore the clone to the remote state, fix the search index, and run the hooks.

        Errors here are logged, never raised: the job fails with its own error.
        """
        clone = self.clone
        if clone is not None:
            try:
                clone.restore()
                pages = sorted(path for path in self.touched if path.suffix == ".md")
                if pages and clone.root.is_dir():
                    self.ctx.search.update(clone.brain_id, clone.root, pages)
            except Exception as exc:
                _log.error(
                    "Could not restore the clone after job %d failed: %s",
                    self.job.id,
                    describe_error(exc, self.secrets),
                )
        for hook in self._failure_hooks:
            try:
                hook()
            except Exception as exc:
                _log.error(
                    "A clean-up step of job %d failed: %s",
                    self.job.id,
                    describe_error(exc, self.secrets),
                )
