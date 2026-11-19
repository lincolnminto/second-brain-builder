"""``brain.create`` and ``brain.sync``."""

from __future__ import annotations

import logging

from second_brain_builder.brains import (
    SETTINGS_PATH,
    BrainSettings,
    read_settings,
    scaffold_brain,
)
from second_brain_builder.domain import BrainStatus
from second_brain_builder.github import (
    GitHubClient,
    GitHubNotFoundError,
    GitHubValidationError,
    RepoInfo,
)
from second_brain_builder.services import BrainCreatePayload, BrainSyncPayload, github_client
from second_brain_builder.worker.context import JobRun
from second_brain_builder.worker.errors import JobError
from second_brain_builder.worker.rows import reconcile_sources, set_brain_status

_log = logging.getLogger(__name__)


def create_brain(run: JobRun) -> None:
    """Create the private repository if asked, clone it, scaffold the Brain, commit, and push.

    A repository that is a Brain already (it has ``.brain/brain.yaml``, for example
    after a disconnect, or after an attempt that pushed and then crashed) is
    connected as it is: nothing is written to it.
    """
    payload = run.payload(BrainCreatePayload)
    brain = run.brain(payload.brain_id)
    run.on_failure(lambda: set_brain_status(run.ctx, brain.id, BrainStatus.ERROR, synced=False))
    run.github_auth()
    client = github_client(run.ctx)
    if payload.create_repo:
        repo = _create_or_get_repo(run, client, payload.repo_full_name, brain.name)
    else:
        repo = run.repo_info(payload.repo_full_name)
    clone = run.open_clone(brain, clone_url=repo.clone_url, require_brain=False)
    if (clone.root / SETTINGS_PATH).is_file():
        settings = read_settings(clone.root)
        _log.info("Repository %s is a Brain already: %s", repo.full_name, settings.name)
    else:
        settings = BrainSettings(
            name=brain.name, language=brain.language, created_at=brain.created_at
        )
        try:
            scaffold_brain(clone.root, settings)
        except FileExistsError as exc:
            raise JobError(
                f"The repository {repo.full_name} has some Brain files already, but no "
                f"{SETTINGS_PATH}. Remove them, or use another repository. ({exc})"
            ) from None
    clone.finish(f"Create brain: {settings.name}", [])
    set_brain_status(run.ctx, brain.id, BrainStatus.READY, synced=True)


def sync_brain(run: JobRun) -> None:
    """Pull, then rebuild the ``sources`` rows and the search index from the clone."""
    payload = run.payload(BrainSyncPayload)
    brain = run.brain(payload.brain_id)
    clone = run.open_clone(brain)
    read_settings(clone.root)
    run.progress("index")
    run.ctx.search.rebuild(brain.id, clone.root)
    reconcile_sources(run.ctx, brain.id, clone.root)
    set_brain_status(run.ctx, brain.id, BrainStatus.READY, synced=True)


def _create_or_get_repo(
    run: JobRun, client: GitHubClient, repo_full_name: str, brain_name: str
) -> RepoInfo:
    """Create the private repository; if an earlier attempt created it, use that one."""
    name = repo_full_name.partition("/")[2]
    run.progress("create_repo")
    try:
        return client.create_private_repo(name, f"Second Brain: {brain_name}")
    except GitHubValidationError as exc:
        # GitHub answers 422 when the name is taken. A repository with this name
        # that the account can see is the one that an earlier attempt created.
        try:
            repo = run.repo_info(repo_full_name)
        except GitHubNotFoundError:
            raise exc from None
        _log.info("Repository %s exists already; using it.", repo.full_name)
        return repo
