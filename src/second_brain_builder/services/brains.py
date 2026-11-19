"""Brains: list, create, get, disconnect, sync, and Git LFS usage.

Creating and syncing a Brain are jobs. Disconnecting a Brain removes it from this
app only: the GitHub repository stays as it is.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from second_brain_builder.brains import BrainSettings, GitError, GitIdentity, GitRepo
from second_brain_builder.domain import BrainStatus, JobKind, JobStatus
from second_brain_builder.services._common import (
    brain_row,
    brain_view,
    job_view,
    load_brain,
    one_line_name,
    source_counts,
)
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import (
    ConflictError,
    InvalidInputError,
    NotConnectedError,
    NotFoundError,
    PublicRepoError,
)
from second_brain_builder.services.github import get_github_account, github_client, github_errors
from second_brain_builder.services.models import (
    BrainCreate,
    BrainCreated,
    BrainView,
    JobView,
    LfsUsageView,
)
from second_brain_builder.services.payloads import BrainCreatePayload, BrainSyncPayload
from second_brain_builder.services.uploads import pending_upload_bytes, remove_staged_upload
from second_brain_builder.store import BrainRow, JobRow, SourceRow

_log = logging.getLogger(__name__)

_REPO_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}")

READ_ONLY_IDENTITY = GitIdentity(name="second-brain-builder", email="noreply@localhost")
"""Identity for ``GitRepo`` objects that only read. They never commit."""


# --- read ---------------------------------------------------------------------------------


def list_brains(ctx: AppContext) -> list[BrainView]:
    """Every Brain, sorted by name."""
    with ctx.read_sessions() as session:
        counts = source_counts(session)
        rows = session.scalars(select(BrainRow).order_by(func.lower(BrainRow.name))).all()
        return [brain_view(row, counts.get(row.id, 0)) for row in rows]


def get_brain(ctx: AppContext, brain_id: str) -> BrainView:
    """One Brain. Raises ``NotFoundError``."""
    with ctx.read_sessions() as session:
        row = brain_row(session, brain_id)
        count = session.scalar(
            select(func.count(SourceRow.id)).where(SourceRow.brain_id == brain_id)
        )
        return brain_view(row, count or 0)


def resolve_brain(ctx: AppContext, ref: str) -> BrainView:
    """Find a Brain by id, repository (``owner/name``), slug, or name, ignoring case.

    Agents name Brains in MCP tool calls; the ids are long.

    Raises:
        NotFoundError: No Brain matches.
        InvalidInputError: More than one Brain matches; use the id.
    """
    text = ref.strip()
    brains = list_brains(ctx)
    for brain in brains:
        if brain.id == text:
            return brain
    key = text.lower()
    matches = [
        brain
        for brain in brains
        if key in {brain.repo_full_name.lower(), brain.slug, brain.name.lower()}
    ]
    if not matches:
        names = ", ".join(brain.slug for brain in brains) or "none"
        raise NotFoundError(f"No Brain is named {text!r}. Brains: {names}.")
    if len(matches) > 1:
        raise InvalidInputError(f"More than one Brain matches {text!r}. Use the Brain id.")
    return matches[0]


# --- create ---------------------------------------------------------------------------------


def create_brain(ctx: AppContext, data: BrainCreate) -> BrainCreated:
    """Add a Brain and enqueue ``brain.create``.

    With ``new_repo_name``, the worker creates a private repository of the
    connected user. With ``existing_repo``, the repository must exist and be
    visible to the connected account, and a public one needs ``confirm_public``.

    Raises:
        NotConnectedError: GitHub is not connected.
        InvalidInputError: The name, language, or repository name is not valid.
        NotFoundError: The existing repository was not found, or the token cannot see it.
        PublicRepoError: The existing repository is public and ``confirm_public`` is False.
        ConflictError: A Brain uses the repository already, or a repository with
            the new name exists.
        AuthError, UpstreamError: GitHub rejected the token or failed.
    """
    name = one_line_name(data.name, "Brain name")
    language = _language(ctx, name, data.language)
    account = get_github_account(ctx)
    if account is None:
        raise NotConnectedError("Connect GitHub before you create a Brain.")
    client = github_client(ctx)
    if data.new_repo_name is not None:
        repo_name = data.new_repo_name.strip()
        if not _is_repo_name(repo_name):
            raise InvalidInputError(
                "A repository name has 1 to 100 letters, digits, '-', '_', or '.', "
                "and is not '.' or '..'."
            )
        repo_full_name = f"{account.login}/{repo_name}"
        _check_not_a_brain(ctx, repo_full_name)
        try:
            with github_errors():
                client.get_repo(repo_full_name)
        except NotFoundError:
            pass
        else:
            raise ConflictError(
                f"The repository {repo_full_name} exists already. "
                "Choose another name, or use the existing repository."
            )
        create_repo = True
    else:
        requested = (data.existing_repo or "").strip()
        try:
            with github_errors():
                repo = client.get_repo(requested)
        except ValueError:
            raise InvalidInputError(
                f"Not a repository name in the form owner/name: {requested!r}."
            ) from None
        except NotFoundError as exc:
            raise NotFoundError(
                f"The repository {requested} was not found, or the GitHub App cannot "
                "access it. Install the app on the repository."
            ) from exc
        repo_full_name = repo.full_name
        _check_not_a_brain(ctx, repo_full_name)
        if not repo.private and not data.confirm_public:
            raise PublicRepoError(
                f"The repository {repo_full_name} is public: everyone can read the Brain. "
                "Confirm to use it anyway.",
                repo_full_name=repo_full_name,
            )
        create_repo = False

    try:
        with ctx.sessions.begin() as session:
            row = BrainRow(
                name=name,
                repo_full_name=repo_full_name,
                language=language,
                status=BrainStatus.CREATING,
                created_at=ctx.clock(),
            )
            session.add(row)
            session.flush()
            brain_id = row.id
    except IntegrityError:
        raise ConflictError(f"A Brain uses the repository {repo_full_name} already.") from None
    payload = BrainCreatePayload(
        brain_id=brain_id, repo_full_name=repo_full_name, create_repo=create_repo
    )
    try:
        job = ctx.jobs.enqueue(JobKind.BRAIN_CREATE, brain_id, payload.to_json())
    except BaseException:
        with ctx.sessions.begin() as session:
            session.delete(brain_row(session, brain_id))
        raise
    return BrainCreated(brain=get_brain(ctx, brain_id), job=job_view(job))


# --- disconnect and sync ---------------------------------------------------------------------


def disconnect_brain(ctx: AppContext, brain_id: str) -> None:
    """Remove a Brain from this app. The GitHub repository is never deleted.

    The Brain's rows (Sources, jobs, events), its search index tables, its clone,
    and the staged uploads of its ``source.add`` jobs (queued, failed, or
    cancelled) are removed.

    Raises:
        NotFoundError: No such Brain.
        ConflictError: A job of the Brain is running. Try again when it ends.
    """
    # One BEGIN IMMEDIATE transaction checks for a running job and deletes the
    # rows. JobQueue.claim needs the same write lock, so the worker cannot start
    # a job of the Brain between the check and the delete.
    with ctx.sessions.begin() as session:
        row = brain_row(session, brain_id)
        running = session.scalar(
            select(JobRow.id)
            .where(JobRow.brain_id == brain_id, JobRow.status == JobStatus.RUNNING)
            .limit(1)
        )
        if running is not None:
            raise ConflictError(
                f"A job of this Brain is running (job {running}). Disconnect it when the job ends."
            )
        upload_payloads = list(
            session.scalars(
                select(JobRow.payload).where(
                    JobRow.brain_id == brain_id, JobRow.kind == JobKind.SOURCE_ADD
                )
            )
        )
        session.delete(row)
    # No job of the Brain exists now, so nothing else uses these files.
    ctx.search.remove_brain(brain_id)
    _remove_clone(ctx, brain_id)
    for payload in upload_payloads:
        remove_staged_upload(ctx, payload)


def sync_brain(ctx: AppContext, brain_id: str) -> JobView:
    """Enqueue ``brain.sync``: pull, then rebuild the Source rows and the search index.

    Raises:
        NotFoundError: No such Brain.
    """
    load_brain(ctx, brain_id)
    payload = BrainSyncPayload(brain_id=brain_id)
    return job_view(ctx.jobs.enqueue(JobKind.BRAIN_SYNC, brain_id, payload.to_json()))


# --- Git LFS usage -----------------------------------------------------------------------------


def get_lfs_usage(ctx: AppContext, brain_id: str) -> LfsUsageView:
    """Git LFS storage of the Brain and of all Brains of the same repository owner.

    Raises:
        NotFoundError: No such Brain.
    """
    row = load_brain(ctx, brain_id)
    owner = repo_owner(row.repo_full_name)
    owner_bytes = owner_lfs_bytes(ctx, owner)
    quota = ctx.settings.lfs_quota_bytes
    return LfsUsageView(
        brain_id=brain_id,
        brain_bytes=clone_lfs_bytes(ctx, brain_id),
        owner=owner,
        owner_bytes=owner_bytes,
        quota_bytes=quota,
        over_quota=owner_bytes > quota,
    )


def repo_owner(repo_full_name: str) -> str:
    return repo_full_name.partition("/")[0]


def clone_lfs_bytes(ctx: AppContext, brain_id: str) -> int:
    """Git LFS bytes that the clone's refs use; 0 when there is no clone yet."""
    root = ctx.clone_path(brain_id)
    if not (root / ".git").exists():
        return 0
    try:
        return GitRepo(root, READ_ONLY_IDENTITY, None).lfs_usage_bytes()
    except GitError as exc:
        _log.warning("Cannot read the Git LFS usage of Brain %s: %s", brain_id, exc)
        return 0


def owner_lfs_bytes(ctx: AppContext, owner: str) -> int:
    """Git LFS bytes of every Brain of ``owner``, plus their uploads in the queue.

    GitHub counts the LFS storage quota per repository owner.
    """
    with ctx.read_sessions() as session:
        brain_ids = [
            brain_id
            for brain_id, repo_full_name in session.execute(
                select(BrainRow.id, BrainRow.repo_full_name)
            )
            if repo_owner(repo_full_name).lower() == owner.lower()
        ]
    committed = sum(clone_lfs_bytes(ctx, brain_id) for brain_id in brain_ids)
    return committed + pending_upload_bytes(ctx, set(brain_ids))


# --- helpers -------------------------------------------------------------------------------------


def _language(ctx: AppContext, name: str, language: str) -> str:
    try:
        settings = BrainSettings(name=name, language=language.strip(), created_at=ctx.clock())
    except ValidationError:
        raise InvalidInputError(
            f"Not a BCP 47 language tag: {language!r}. Examples: en, pt-BR."
        ) from None
    return settings.language


def _is_repo_name(name: str) -> bool:
    return _REPO_NAME_RE.fullmatch(name) is not None and name not in {".", ".."}


def _check_not_a_brain(ctx: AppContext, repo_full_name: str) -> None:
    with ctx.read_sessions() as session:
        existing = session.scalar(
            select(BrainRow.id).where(func.lower(BrainRow.repo_full_name) == repo_full_name.lower())
        )
    if existing is not None:
        raise ConflictError(f"A Brain uses the repository {repo_full_name} already.")


def _remove_clone(ctx: AppContext, brain_id: str) -> None:
    clone = ctx.clone_path(brain_id)
    brains_dir = ctx.settings.brains_dir.resolve()
    if clone.is_symlink():
        clone.unlink()
        return
    if not clone.exists():
        return
    if clone.resolve().parent != brains_dir:  # never remove anything outside brains_dir
        _log.warning("Did not remove %s: it is not a clone in %s.", clone, brains_dir)
        return
    try:
        shutil.rmtree(clone)
    except OSError as exc:
        _log.warning("Could not remove the clone of Brain %s: %s", brain_id, exc)


def clone_root(ctx: AppContext, brain_id: str) -> Path:
    """The clone of an existing Brain. Raises ``NotFoundError`` for an unknown id."""
    load_brain(ctx, brain_id)
    return ctx.clone_path(brain_id)
