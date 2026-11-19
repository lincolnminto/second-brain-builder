"""Local git commands on a Brain clone that ``brains.GitRepo`` does not offer.

These commands never contact a remote, so they need no credentials: reset the
clone to its remote-tracking branch, clean it, list the paths that changed between
two commits, and apply one commit again with ``cherry-pick``. Clone, fetch, and
push stay in ``GitRepo``, which holds the token.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import GitIdentity

REMOTE = "origin"

# Inherited variables that would point git at another repository or inject
# config. GitRepo drops the same ones.
_DROPPED_VARIABLES = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_DATE",
        "GIT_LFS_SKIP_SMUDGE",
    }
)
_DROPPED_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
_MAX_ERROR_LINES = 20
# git-lfs never writes a pointer file larger than this.
_POINTER_MAX_BYTES = 1024
_POINTER_START = b"version https://git-lfs.github.com/spec/v1\n"
# Index modes of regular files; symbolic links and submodules have no LFS content.
_FILE_MODES = frozenset({b"100644", b"100755"})


class LocalGitError(RuntimeError):
    """A local git command failed. Local commands carry no token."""


class LocalGit:
    """Local git commands in the clone at ``root``, as ``identity``."""

    def __init__(self, root: Path, identity: GitIdentity) -> None:
        self.root = root
        self.identity = identity

    def head(self) -> str | None:
        """The commit id of ``HEAD``; ``None`` on a branch without commits."""
        return self.commit("HEAD")

    def commit(self, ref: str) -> str | None:
        """The commit id that ``ref`` names, or ``None`` when it names no commit."""
        result = self.run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        return _text(result.stdout).strip() if result.returncode == 0 else None

    def branch(self) -> str | None:
        """The current branch, or ``None`` when ``HEAD`` is detached."""
        result = self.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return _text(result.stdout).strip() if result.returncode == 0 else None

    def remote_ref(self) -> str | None:
        """The remote-tracking ref of the current branch, if the branch and the ref exist."""
        branch = self.branch()
        if branch is None:
            return None
        ref = f"refs/remotes/{REMOTE}/{branch}"
        return ref if self.commit(ref) is not None else None

    def message(self, commit: str) -> str:
        """The full commit message of ``commit``."""
        result = self.run("log", "-1", "--format=%B", commit, "--")
        return _text(result.stdout).strip()

    def count_commits(self, revision_range: str) -> int:
        """The number of commits in ``revision_range``, for example ``origin/main..HEAD``."""
        result = self.run("rev-list", "--count", revision_range, "--")
        return int(_text(result.stdout).strip() or "0")

    def is_dirty(self) -> bool:
        """True when the working tree or the index differs from ``HEAD``, untracked files too."""
        result = self.run("status", "--porcelain=v1", "-z", "--untracked-files=all")
        return bool(result.stdout)

    def changed_paths(self, old: str, new: str) -> list[PurePosixPath]:
        """The paths that differ between two commits, renames as a delete and an add."""
        result = self.run("diff", "--name-only", "--no-renames", "-z", old, new, "--")
        return _paths(result.stdout)

    def conflicted_paths(self) -> list[PurePosixPath]:
        """The paths with unmerged entries in the index."""
        result = self.run("diff", "--name-only", "--diff-filter=U", "-z")
        return _paths(result.stdout)

    def has_path(self, ref: str, path: PurePosixPath) -> bool:
        """True when the tree of ``ref`` has ``path``."""
        result = self.run("cat-file", "-e", f"{ref}:{path.as_posix()}", check=False)
        return result.returncode == 0

    def abort_operations(self) -> None:
        """Abort a rebase or cherry-pick that a crash or a conflict left in progress."""
        for operation in ("rebase", "cherry-pick", "merge"):
            self.run(operation, "--abort", check=False)

    def reset_hard(self, ref: str) -> None:
        """Point the branch at ``ref`` and make the index and the working tree match it."""
        self.run("reset", "--quiet", "--hard", ref)

    def clean(self) -> None:
        """Remove every untracked file and directory, ignored ones too."""
        self.run("clean", "-ffdxq")

    def cherry_pick_no_commit(self, commit: str) -> bool:
        """Apply the changes of ``commit`` to the working tree and the index.

        Returns False when the changes conflict; the conflicted paths then have
        unmerged entries (see ``conflicted_paths``).
        """
        result = self.run("cherry-pick", "--no-commit", commit, check=False)
        return result.returncode == 0

    def quit_cherry_pick(self) -> None:
        """Forget a cherry-pick in progress, and keep its changes in the working tree."""
        self.run("cherry-pick", "--quit", check=False)

    def checkout_paths(self, ref: str, paths: list[PurePosixPath]) -> None:
        """Take the version of ``paths`` in ``ref``, in the index and the working tree."""
        if paths:
            self.run("checkout", ref, "--", *(path.as_posix() for path in paths))

    def install_lfs(self) -> None:
        """Set up the Git LFS filters and hooks in this clone only (``git lfs install --local``)."""
        self.run("lfs", "install", "--local")

    def files_outside_lfs(self) -> list[PurePosixPath]:
        """Tracked files that ``.gitattributes`` puts in Git LFS, but whose index blob is
        not a Git LFS pointer: someone pushed them without Git LFS.

        It reads only the attributes and the small blobs, never the file contents.
        """
        listing = self.run("ls-files", "--stage", "-z").stdout
        blobs: dict[bytes, bytes] = {}
        for record in listing.split(b"\0"):
            meta, _, path = record.partition(b"\t")
            fields = meta.split()
            if len(fields) == 3 and fields[0] in _FILE_MODES:
                blobs[path] = fields[1]
        if not blobs:
            return []
        attributes = self.run(
            "check-attr", "-z", "--stdin", "filter", stdin=b"\0".join(blobs) + b"\0"
        ).stdout.split(b"\0")
        in_lfs = [
            attributes[index]
            for index in range(0, len(attributes) - 2, 3)
            if attributes[index + 2] == b"lfs"
        ]
        if not in_lfs:
            return []
        object_ids = b"\n".join(blobs[path] for path in in_lfs) + b"\n"
        sizes = self.run("cat-file", "--batch-check=%(objectsize)", stdin=object_ids).stdout
        small = [
            path
            for path, size in zip(in_lfs, sizes.split(), strict=True)
            if int(size) <= _POINTER_MAX_BYTES
        ]
        pointers: set[bytes] = set()
        if small:
            batch = self.run(
                "cat-file", "--batch", stdin=b"\n".join(blobs[path] for path in small) + b"\n"
            ).stdout
            pointers = {
                object_id
                for object_id, content in _batch_contents(batch)
                if content.startswith(_POINTER_START)
            }
        return [PurePosixPath(os.fsdecode(path)) for path in in_lfs if blobs[path] not in pointers]

    def renormalize(self, paths: list[PurePosixPath]) -> None:
        """Stage ``paths`` again through their filters (``git add --renormalize``)."""
        if paths:
            self.run("add", "--renormalize", "--", *(path.as_posix() for path in paths))

    def run(
        self, *args: str, check: bool = True, stdin: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        """Run ``git <args>`` in the clone.

        Raises:
            LocalGitError: git is not installed, or ``check`` is True and git failed.
        """
        executable = shutil.which("git")
        if executable is None:
            raise LocalGitError("git is not installed")
        try:
            # An argument list, never a shell; the arguments are git options,
            # commit ids, and paths that this package builds.
            result = subprocess.run(  # noqa: S603
                [executable, *args],
                cwd=self.root,
                env=self._environment(),
                input=stdin,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise LocalGitError(f"cannot run git {args[0]}: {exc}") from None
        if check and result.returncode != 0:
            output = _text(result.stderr).strip() or _text(result.stdout).strip()
            lines = output.splitlines()[-_MAX_ERROR_LINES:]
            message = f"git {' '.join(args)} failed with exit code {result.returncode}"
            if lines:
                message += ": " + "\n".join(lines)
            raise LocalGitError(message)
        return result

    def _environment(self) -> dict[str, str]:
        env = {
            name: value
            for name, value in os.environ.items()
            if name not in _DROPPED_VARIABLES and not name.startswith(_DROPPED_PREFIXES)
        }
        config = [("credential.helper", ""), ("commit.gpgSign", "false")]
        env["GIT_CONFIG_COUNT"] = str(len(config))
        for index, (key, value) in enumerate(config):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_EDITOR"] = ":"
        env["GIT_AUTHOR_NAME"] = self.identity.name
        env["GIT_AUTHOR_EMAIL"] = self.identity.email
        env["GIT_COMMITTER_NAME"] = self.identity.name
        env["GIT_COMMITTER_EMAIL"] = self.identity.email
        return env


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _batch_contents(output: bytes) -> list[tuple[bytes, bytes]]:
    """``(object id, content)`` pairs from the output of ``git cat-file --batch``."""
    found: list[tuple[bytes, bytes]] = []
    position = 0
    while position < len(output):
        header_end = output.index(b"\n", position)
        header = output[position:header_end].split()
        position = header_end + 1
        if len(header) != 3:  # "<object> missing"
            continue
        size = int(header[2])
        found.append((header[0], output[position : position + size]))
        position += size + 1
    return found


def _paths(output: bytes) -> list[PurePosixPath]:
    return [PurePosixPath(os.fsdecode(name)) for name in output.split(b"\0") if name]
