"""One job's work on a Brain clone: prepare, publish, restore, and refresh the caches.

Every job starts from the remote state: the clone is reset to its remote-tracking
branch, which drops what a crashed job left behind, and then pulled with rebase.
After the work, ``finish`` writes ``index.md`` and ``log.md``, commits, pushes, and
updates the search index and the ``sources`` rows. When a job fails, ``restore``
resets the clone to the remote state again, so the next job starts clean.
"""

from __future__ import annotations

import logging
import secrets
import shutil
from collections.abc import Callable
from datetime import date
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import (
    INDEX_PATH,
    LOG_PATH,
    SOURCES_DIR,
    WIKI_DIR,
    WIKI_DIRS,
    GitConflictError,
    GitRepo,
    lfs_available,
    write_index,
)
from second_brain_builder.okf import PageType, prepend_log
from second_brain_builder.services import AppContext, GitHubAuth
from second_brain_builder.worker.errors import JobError
from second_brain_builder.worker.localgit import LocalGit, LocalGitError
from second_brain_builder.worker.rows import reconcile_sources

_log = logging.getLogger(__name__)

GENERATED_FILES = frozenset({INDEX_PATH, LOG_PATH})
"""Files that code writes from the rest of the Brain. A conflict in them is resolved
by writing them again on top of the remote version."""

MAX_REPLAYS = 3
"""How many times ``publish`` applies its commit again on a new remote state after a conflict."""

NORMALIZE_MESSAGE = "Store Sources in Git LFS"
"""The commit for files that ``.gitattributes`` puts in Git LFS, but that were pushed
without it (for example from the GitHub web page)."""


class BrainClone:
    """The clone of one Brain at ``<brains_dir>/<brain-id>``, as one job uses it."""

    def __init__(self, ctx: AppContext, brain_id: str, auth: GitHubAuth) -> None:
        self.ctx = ctx
        self.brain_id = brain_id
        self.root = ctx.clone_path(brain_id)
        self.repo = GitRepo(self.root, auth.identity, auth.auth)
        self.local = LocalGit(self.root, auth.identity)
        self.start_head: str | None = None
        """``HEAD`` before this job pulled, which the caches show; ``None`` for a new clone."""

    # --- start --------------------------------------------------------------------

    def prepare(self, clone_url: Callable[[], str]) -> None:
        """Make the clone match the remote: clone it, or reset it and pull with rebase.

        Leftovers of a crashed job (changes, untracked files, commits that were not
        pushed, a rebase in progress) are dropped. A clone that is damaged, or that
        has never seen a remote branch, is removed and cloned again. Files that are
        not stored as ``.gitattributes`` says get a commit of their own (see
        ``NORMALIZE_MESSAGE``), which the next ``publish`` pushes: without it the
        clone would never be clean, and every pull would fail.
        """
        try:
            usable = self._reset_to_remote()
        except LocalGitError:
            _log.warning("The clone of Brain %s is damaged; cloning again.", self.brain_id)
            usable = False
        if usable:
            if lfs_available():
                self.local.install_lfs()
            self.start_head = self.local.head()
            self._commit_normalization()
            self.repo.pull_rebase()
            return
        self._remove()
        self.repo.clone(clone_url())
        self._commit_normalization()

    def restore(self) -> None:
        """After a failed job: reset the clone to the remote state, or remove it.

        The next job then starts from the remote state, or clones again.
        """
        try:
            if self._reset_to_remote():
                return
        except LocalGitError:
            _log.warning("Cannot reset the clone of Brain %s.", self.brain_id)
        self._remove()

    # --- finish -------------------------------------------------------------------

    def finish(self, message: str, log_entries: list[str]) -> str | None:
        """Publish the changes of the job (see ``publish``), then refresh the caches.

        Returns the commit id, or ``None`` when the job changed nothing.
        """
        commit = self.publish(message, log_entries)
        self.refresh()
        return commit

    def publish(self, message: str, log_entries: list[str]) -> str | None:
        """Write ``index.md`` and ``log.md``, commit with ``message``, and push.

        Nothing is committed when the working tree has no changes; commits that the
        remote does not have yet are pushed all the same. When the push meets a
        rebase conflict, the last commit is applied again on the new remote state,
        with ``index.md`` and ``log.md`` written again from it; a conflict in any
        other file fails the job.

        Returns the commit of the job, or ``None`` when the job changed nothing.

        Raises:
            JobError: Another file conflicts with a change on the remote.
            GitError: git failed, or the conflicts went on after ``MAX_REPLAYS``.
        """
        job_commit: str | None = None
        if self.local.is_dirty():
            self._write_index_and_log(log_entries)
            job_commit = self.repo.commit_all(message)
        commit = self.local.head()
        attempt = 0
        while commit is not None and self._ahead_of_remote():
            try:
                self.repo.push()
            except GitConflictError:
                if attempt == MAX_REPLAYS:
                    raise
                attempt += 1
                _log.info("Push of %s met a conflict: applying the commit again.", self.root)
                replaying_job = commit == job_commit
                commit = self._replay(commit, log_entries if replaying_job else [])
                if replaying_job:
                    job_commit = commit
                continue
            break
        return job_commit

    def refresh(self) -> None:
        """Update the search index and the ``sources`` rows for what changed in the clone.

        After a new clone, both are rebuilt. Otherwise only the paths that changed
        since ``start_head`` (this job's commit and the commits that it pulled) are
        indexed again.
        """
        paths = self.changed_paths()
        if paths is None:
            self.ctx.search.rebuild(self.brain_id, self.root)
            reconcile_sources(self.ctx, self.brain_id, self.root)
            return
        pages = [path for path in paths if path.suffix == ".md"]
        if pages:
            self.ctx.search.update(self.brain_id, self.root, pages)
        slugs = changed_source_ids(paths)
        if slugs:
            reconcile_sources(self.ctx, self.brain_id, self.root, slugs)

    def changed_paths(self) -> list[PurePosixPath] | None:
        """The paths that changed since ``start_head``; ``None`` for a new clone."""
        if self.start_head is None:
            return None
        head = self.local.head()
        if head is None or head == self.start_head:
            return []
        return self.local.changed_paths(self.start_head, head)

    # --- log.md -------------------------------------------------------------------

    def read_log(self) -> str:
        """The text of ``log.md``; empty when it is missing, not a regular file, or not UTF-8."""
        path = self.root / LOG_PATH
        if path.is_symlink() or not path.is_file():
            return ""
        try:
            return path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            return ""

    def write_log(self, text: str) -> None:
        """Replace ``log.md``. A symbolic link there is replaced, never followed."""
        write_text(self.root, LOG_PATH, text)

    # --- helpers ------------------------------------------------------------------

    def _write_index_and_log(self, log_entries: list[str]) -> None:
        write_index(self.root)
        if log_entries:
            self.write_log(prepend_log(self.read_log(), self._today(), log_entries))

    def _today(self) -> date:
        return self.ctx.clock().date()

    def _replay(self, commit: str, log_entries: list[str]) -> str | None:
        """Apply ``commit`` again on top of the new remote state, and commit it again.

        Commits before it that the remote does not have are rebased by the pull.
        """
        message = self.local.message(commit)
        parent = self.local.commit(f"{commit}^")
        if parent is None:
            raise JobError(
                "The repository got its first commit from elsewhere while this job ran. "
                "Run the job again."
            )
        self.local.reset_hard(parent)
        self.repo.pull_rebase()
        if not self.local.cherry_pick_no_commit(commit):
            conflicted = set(self.local.conflicted_paths())
            others = sorted(conflicted - GENERATED_FILES)
            if not conflicted or others:
                self.local.abort_operations()
                self.local.reset_hard("HEAD")
                names = ", ".join(path.as_posix() for path in others) or "unknown files"
                raise JobError(
                    "The Brain changed on GitHub while this job ran, and these files "
                    f"conflict with the changes of the job: {names}. Run the job again."
                )
        self.local.quit_cherry_pick()
        remote_versions = [path for path in GENERATED_FILES if self.local.has_path("HEAD", path)]
        self.local.checkout_paths("HEAD", sorted(remote_versions))
        self._write_index_and_log(log_entries)
        return self.repo.commit_all(message)

    def _ahead_of_remote(self) -> bool:
        """True when the branch has commits that its remote-tracking branch does not have."""
        if self.local.head() is None:
            return False
        remote = self.local.remote_ref()
        return remote is None or self.local.count_commits(f"{remote}..HEAD") > 0

    def _commit_normalization(self) -> None:
        """Commit files that git stores again in another form than the remote has.

        Files that ``.gitattributes`` puts in Git LFS but that were pushed without it
        are staged again through the LFS filter. Other files that git sees as changed
        right after a reset (other attribute rules) go into the same commit.
        """
        if self.local.head() is None:
            return
        if lfs_available():
            self.local.renormalize(self.local.files_outside_lfs())
        if not self.local.is_dirty():
            return
        _log.warning(
            "Brain %s has files that are not stored as .gitattributes says; committing them.",
            self.brain_id,
        )
        self.repo.commit_all(NORMALIZE_MESSAGE)

    def _reset_to_remote(self) -> bool:
        """Reset to the remote-tracking branch and clean; False when the clone cannot be reset."""
        if not (self.root / ".git").is_dir():
            return False
        self.local.abort_operations()
        remote = self.local.remote_ref()
        if remote is None:
            return False
        self.local.reset_hard(remote)
        self.local.clean()
        return True

    def _remove(self) -> None:
        """Remove the clone directory, which must be inside the brains directory."""
        root = self.root
        if root.is_symlink():
            root.unlink()
            return
        if not root.exists():
            return
        brains_dir = self.ctx.settings.brains_dir.resolve()
        if root.resolve().parent != brains_dir:
            raise JobError(f"The clone {root} is not in {brains_dir}; it was not removed.")
        shutil.rmtree(root)


def changed_source_ids(paths: list[PurePosixPath]) -> set[str]:
    """The Source ids whose Canonical Document or summary Wiki Page is among ``paths``."""
    summaries = WIKI_DIR / WIKI_DIRS[PageType.SUMMARY]
    found: set[str] = set()
    for path in paths:
        if path.suffix != ".md":
            continue
        in_sources = path.parts[:1] == SOURCES_DIR.parts and path.parent.name == path.stem
        if path.parent == summaries or in_sources:
            found.add(path.stem)
    return found


def remove_empty_dirs(root: Path, directory: PurePosixPath, stop: PurePosixPath) -> None:
    """Remove ``directory`` and its parents while they are empty, up to ``stop`` (kept)."""
    current = directory
    while current != stop and stop in current.parents:
        path = root.joinpath(*current.parts)
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            return
        path.rmdir()
        current = current.parent


def write_text(root: Path, path: PurePosixPath, text: str) -> None:
    """Write a file of the Brain atomically: a ``.sbb-<hex>.tmp`` file, then a rename.

    A symbolic link at the target is replaced, never followed.
    """
    target = root.joinpath(*path.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".sbb-{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_bytes(text.encode("utf-8"))
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
