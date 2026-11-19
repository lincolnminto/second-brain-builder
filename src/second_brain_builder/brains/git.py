"""Git and Git LFS operations on one Brain clone.

Every command runs as an argument list, never through a shell. The token travels
only in an ``http.extraheader`` that environment variables set for that one
process (``GIT_CONFIG_COUNT``), scoped to ``https://github.com/``. It never goes
into argv, ``.git/config``, or the remote URL, and every error message is scrubbed
of it.
"""

from __future__ import annotations

import base64
import functools
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

from second_brain_builder.brains.models import GitAuth, GitIdentity

_log = logging.getLogger(__name__)

DEFAULT_BRANCH = "main"
"""The branch that the first push to an empty remote creates."""

REMOTE = "origin"

_GITHUB_HEADER_KEY = "http.https://github.com/.extraheader"
_REDACTED = "***"
_MAX_ERROR_LINES = 20

# Inherited variables that would point git at another repo, inject config, or
# change commits. A git hook, for example, sets GIT_DIR and GIT_INDEX_FILE.
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
_DROPPED_PREFIXES = (
    "GIT_CONFIG_KEY_",
    "GIT_CONFIG_VALUE_",
    # Trace and debug output can record the auth header from GIT_CONFIG_* (for
    # example GIT_TRACE2_EVENT with GIT_TRACE2_CONFIG_PARAMS=http.*, or GIT_TRACE_CURL).
    # git-lfs reads GIT_TRACE too.
    "GIT_TRACE",
    "GIT_CURL_VERBOSE",
)

# git-lfs never writes a pointer file larger than this (its "blobSizeCutoff").
_POINTER_MAX_BYTES = 1024
_POINTER_VERSIONS = frozenset(
    {"https://git-lfs.github.com/spec/v1", "https://hawser.github.com/spec/v1"}
)
_SHA256_RE = re.compile(r"sha256:([0-9a-f]{64})")
_LFS_PATH_PART_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
# git reads "<transport>::<address>" as a remote helper (transport.c, is_urlschemechar).
_REMOTE_HELPER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+.-]*::")
# okf.write_page and the writes in brains.scaffold put the text in a temporary file
# next to the target (".okf-<hex>.tmp", ".sbb-<hex>.tmp"), then rename it. A worker
# crash between the two steps leaves the file behind; commit_all never commits it.
_TEMPORARY_FILE_PATHSPECS = (
    ":(exclude,glob)**/.okf-*.tmp",
    ":(exclude,glob)**/.sbb-*.tmp",
)


class GitError(RuntimeError):
    """A git or git-lfs command failed. The message never contains a token."""


class GitConflictError(GitError):
    """A rebase stopped on a conflict.

    The rebase was aborted: the branch and the working tree are as they were
    before ``pull_rebase``, with the local commits on top of the old remote state.
    """


@functools.lru_cache(maxsize=1)
def lfs_available() -> bool:
    """True when ``git lfs version`` runs. The result is cached for the process."""
    try:
        result = subprocess.run(["git", "lfs", "version"], capture_output=True, check=False)
    except OSError:
        return False
    return result.returncode == 0


class GitRepo:
    """One Brain clone at ``root``.

    ``identity`` is the author and committer of every commit. ``auth`` is the
    GitHub token, or ``None`` for a remote that needs none (for example a local
    path in tests). A relative ``root`` is made absolute against the current
    working directory once, here.
    """

    def __init__(self, root: Path, identity: GitIdentity, auth: GitAuth | None) -> None:
        self.root = root.absolute()
        self.identity = identity
        self._auth = auth
        self._secrets = _secret_forms(auth)

    # --- public operations -------------------------------------------------------

    def clone(self, url: str, skip_lfs_smudge: bool = False) -> None:
        """Clone ``url`` into ``root``, which must not exist or must be empty.

        An empty remote gives a clone on the unborn branch ``main``; the first
        ``push`` creates it. When git-lfs is installed, its filters and hooks are
        set up in the clone. With ``skip_lfs_smudge``, the clone keeps LFS pointer
        files instead of downloading Sources, now and on every later pull; use
        ``lfs_pull`` to download the ones you need.

        When a step after the checkout fails (for example the Git LFS download),
        the clone is removed again, so that a retry can clone into ``root``. A
        ``root`` that existed before (empty) stays, empty.

        Raises:
            ValueError: ``url`` has credentials in it (pass the token in
                ``GitAuth``), or it has git's ``<transport>::<address>`` form.
            GitError: git failed.
        """
        _check_clone_url(url)
        self.root.parent.mkdir(parents=True, exist_ok=True)
        root_existed = self.root.exists()
        # Always clone without LFS downloads; _set_up_lfs downloads afterwards if
        # asked. Then a git-lfs filter in the system config cannot download them.
        # git removes what it wrote when the clone itself fails.
        self._git(
            ["clone", "--quiet", "--origin", REMOTE, "--", url, str(self.root)],
            cwd=self.root.parent,
            extra_env={"GIT_LFS_SKIP_SMUDGE": "1"},
        )
        try:
            if self._head_commit() is None:
                self._git(["symbolic-ref", "HEAD", f"refs/heads/{DEFAULT_BRANCH}"])
            self._set_up_lfs(skip_lfs_smudge)
        except BaseException:
            self._remove_clone(keep_root=root_existed)
            raise

    def pull_rebase(self) -> None:
        """Fetch ``origin`` and rebase the local commits onto its copy of the branch.

        Nothing happens to the branch when the remote has no such branch yet. A
        clone with no commits takes the remote branch as it is.

        Raises:
            GitConflictError: The rebase stopped on a conflict and was aborted.
            GitError: git failed, for example because the working tree has
                uncommitted changes to tracked files.
        """
        self._git(["fetch", "--quiet", "--prune", REMOTE])
        branch = self._branch()
        remote_ref = f"refs/remotes/{REMOTE}/{branch}"
        if not self._ref_exists(remote_ref):
            return
        if self._head_commit() is None:
            self._git(["checkout", "--quiet", "-B", branch, remote_ref])
            return
        args = ["rebase", "--quiet", remote_ref]
        result = self._git(args, check=False)
        if result.returncode == 0:
            return
        error = self._error(args, result)
        if self._rebase_in_progress():
            self._git(["rebase", "--abort"], check=False)
            raise GitConflictError(f"{error} (the rebase was aborted)")
        raise error

    def commit_all(self, message: str) -> str | None:
        """Stage every change in the working tree and commit it.

        Temporary files that an interrupted page write left behind (``.okf-*.tmp``,
        ``.sbb-*.tmp``) are not staged.

        Returns:
            The new commit id, or ``None`` when nothing changed.

        Raises:
            ValueError: ``message`` is empty.
            GitError: git failed.
        """
        if not message.strip():
            raise ValueError("the commit message is empty")
        self._git(["add", "--all", "--", ".", *_TEMPORARY_FILE_PATHSPECS])
        args = ["diff", "--cached", "--quiet"]
        staged = self._git(args, check=False)
        if staged.returncode == 0:
            return None
        if staged.returncode != 1:
            raise self._error(args, staged)
        self._git(["commit", "--quiet", "--message", message])
        return self._head_commit()

    def push(self, retries: int = 3) -> None:
        """Push the current branch to ``origin``.

        When the remote rejects the push because it has new commits, run
        ``pull_rebase`` and push again, up to ``retries`` more times. A branch
        with no commits has nothing to push.

        Raises:
            ValueError: ``retries`` is negative.
            GitConflictError: A rebase between attempts stopped on a conflict.
            GitError: The push failed for another reason, or every retry was
                rejected.
        """
        if retries < 0:
            raise ValueError("retries must be 0 or more")
        if self._head_commit() is None:
            return
        branch = self._branch()
        args = ["push", "--porcelain", "--set-upstream", REMOTE, f"HEAD:refs/heads/{branch}"]
        for attempt in range(retries + 1):
            result = self._git(args, check=False)
            if result.returncode == 0:
                return
            if attempt == retries or not _rejected_as_behind(_text(result.stdout)):
                raise self._error(args, result)
            _log.info("Push to %s was rejected: rebasing and retrying.", self.root)
            self.pull_rebase()

    def lfs_pull(self, paths: list[PurePosixPath]) -> None:
        """Download the Git LFS content of ``paths`` and write it to the working tree.

        ``paths`` are file paths from the Brain root. An empty list does nothing.

        Raises:
            ValueError: A path is not a plain relative path (it is absolute, has a
                dot segment, or has characters that Git LFS reads as a pattern).
            GitError: git-lfs is not installed, or the download failed.
        """
        if not paths:
            return
        for path in paths:
            _check_lfs_path(path)
        include = ",".join(path.as_posix() for path in paths)
        # An empty --exclude overrides an lfs.fetchexclude from any config.
        self._git(["lfs", "pull", f"--include={include}", "--exclude=", REMOTE])

    def lfs_usage_bytes(self) -> int:
        """The total size of the Git LFS objects that any ref of the clone uses.

        It reads the LFS pointer files in the history of every branch and
        remote-tracking branch, and counts each object once. Old versions count,
        as they do in the GitHub LFS storage quota. git-lfs does not need to be
        installed.

        Raises:
            GitError: git failed.
        """
        listing = _text(self._git(["rev-list", "--objects", "--all"]).stdout)
        object_ids = [line.split(" ", 1)[0] for line in listing.splitlines() if line]
        if not object_ids:
            return 0
        checks = self._git(
            ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
            stdin="\n".join(object_ids).encode() + b"\n",
        )
        candidates: dict[str, None] = {}
        for line in _text(checks.stdout).splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[1] == "blob" and int(fields[2]) <= _POINTER_MAX_BYTES:
                candidates.setdefault(fields[0], None)
        if not candidates:
            return 0
        contents = self._git(["cat-file", "--batch"], stdin="\n".join(candidates).encode() + b"\n")
        sizes: dict[str, int] = {}
        for blob in _batch_contents(contents.stdout):
            pointer = _parse_pointer(blob)
            if pointer is not None:
                sizes[pointer[0]] = pointer[1]
        return sum(sizes.values())

    # --- helpers -------------------------------------------------------------------

    def _set_up_lfs(self, skip_smudge: bool) -> None:
        if not lfs_available():
            _log.warning(
                "git-lfs is not installed: %s has no Git LFS filters, so Sources "
                "committed from it are stored in plain git.",
                self.root,
            )
            return
        install = ["lfs", "install", "--local"]
        if skip_smudge:
            install.append("--skip-smudge")
        self._git(install)
        if not skip_smudge and self._head_commit() is not None:
            self._git(["lfs", "pull", REMOTE])

    def _remove_clone(self, keep_root: bool) -> None:
        """Remove what ``clone`` wrote into ``root``; with ``keep_root``, keep ``root``."""
        try:
            targets = list(self.root.iterdir()) if keep_root else [self.root]
            for target in targets:
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
        except OSError as exc:
            _log.warning("Could not remove the failed clone at %s: %s", self.root, exc)

    def _branch(self) -> str:
        args = ["symbolic-ref", "--quiet", "--short", "HEAD"]
        result = self._git(args, check=False)
        if result.returncode != 0:
            raise GitError(f"HEAD of {self.root} is not on a branch")
        return _text(result.stdout).strip()

    def head_commit(self) -> str | None:
        """The current HEAD commit, or ``None`` for a repository without a commit."""
        return self._head_commit()

    def _head_commit(self) -> str | None:
        result = self._git(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], check=False)
        return _text(result.stdout).strip() if result.returncode == 0 else None

    def _ref_exists(self, ref: str) -> bool:
        result = self._git(["rev-parse", "--verify", "--quiet", ref], check=False)
        return result.returncode == 0

    def _rebase_in_progress(self) -> bool:
        for name in ("rebase-merge", "rebase-apply"):
            result = self._git(["rev-parse", "--git-path", name])
            if (self.root / _text(result.stdout).strip()).exists():
                return True
        return False

    def _environment(self, extra: Mapping[str, str] | None) -> dict[str, str]:
        env = {
            name: value
            for name, value in os.environ.items()
            if name not in _DROPPED_VARIABLES and not name.startswith(_DROPPED_PREFIXES)
        }
        config: list[tuple[str, str]] = []
        if self._auth is not None:
            header = f"AUTHORIZATION: basic {_basic_credentials(self._auth)}"
            config.append((_GITHUB_HEADER_KEY, header))
        # No credential helper may answer instead of the header, or store anything.
        config.append(("credential.helper", ""))
        # The commits use a noreply identity that has no signing key.
        config.append(("commit.gpgSign", "false"))
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
        if extra:
            env.update(extra)
        return env

    def _git(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        extra_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        argv = ["git", *args]
        try:
            result = subprocess.run(
                argv,
                cwd=cwd if cwd is not None else self.root,
                env=self._environment(extra_env),
                input=stdin,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise GitError(self._scrub(f"cannot run git {args[0]}: {exc}")) from None
        if check and result.returncode != 0:
            raise self._error(args, result)
        return result

    def _error(self, args: Sequence[str], result: subprocess.CompletedProcess[bytes]) -> GitError:
        output = _text(result.stderr).strip() or _text(result.stdout).strip()
        lines = output.splitlines()[-_MAX_ERROR_LINES:]
        message = f"git {' '.join(args)} failed with exit code {result.returncode}"
        if lines:
            message += ": " + "\n".join(lines)
        return GitError(self._scrub(message))

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, _REDACTED)
        return text


def _basic_credentials(auth: GitAuth) -> str:
    raw = f"x-access-token:{auth.token.get_secret_value()}"
    return base64.b64encode(raw.encode()).decode("ascii")


def _secret_forms(auth: GitAuth | None) -> tuple[str, ...]:
    """Every form in which the token could appear in output, longest first."""
    if auth is None:
        return ()
    token = auth.token.get_secret_value()
    forms = {token, quote(token, safe=""), _basic_credentials(auth)}
    return tuple(sorted((form for form in forms if form), key=len, reverse=True))


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _check_clone_url(url: str) -> None:
    # git hands "https::https://<token>@github.com/..." to a remote helper with
    # the whole address, so the check below would not see the user name. The
    # app never needs a remote helper.
    if _REMOTE_HELPER_RE.match(url):
        raise ValueError(
            "The clone URL must not use the <transport>::<address> form of git remote helpers."
        )
    # A user name alone is fine for SSH (ssh://git@github.com/...), but over HTTP
    # it can be the token itself (https://<token>@github.com/...).
    parts = urlsplit(url)
    is_http = parts.scheme.lower() in ("http", "https")
    if parts.password is not None or (is_http and parts.username is not None):
        raise ValueError(
            "The clone URL must not contain credentials. Pass the token in GitAuth instead."
        )


def _rejected_as_behind(porcelain: str) -> bool:
    """True when ``git push --porcelain`` says that the remote has commits we lack."""
    return any(
        line.startswith("!") and ("fetch first" in line or "non-fast-forward" in line)
        for line in porcelain.splitlines()
    )


def _check_lfs_path(path: PurePosixPath) -> None:
    if path.is_absolute() or not path.parts:
        raise ValueError(f"not a file path in the Brain: {path}")
    for part in path.parts:
        if part.startswith(".") or not _LFS_PATH_PART_RE.fullmatch(part):
            raise ValueError(f"not a plain file path in the Brain: {path}")


def _batch_contents(output: bytes) -> list[bytes]:
    """The object contents in the output of ``git cat-file --batch``."""
    contents: list[bytes] = []
    position = 0
    while position < len(output):
        header_end = output.index(b"\n", position)
        header = output[position:header_end].split()
        position = header_end + 1
        if len(header) != 3:  # "<oid> missing"
            continue
        size = int(header[2])
        contents.append(output[position : position + size])
        position += size + 1
    return contents


def _parse_pointer(blob: bytes) -> tuple[str, int] | None:
    """The object id and size in a Git LFS pointer file, or ``None`` for another blob."""
    try:
        lines = blob.decode("ascii").split("\n")
    except UnicodeDecodeError:
        return None
    if not lines or not lines[0].startswith("version "):
        return None
    if lines[0].removeprefix("version ") not in _POINTER_VERSIONS:
        return None
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line:
            key, _, value = line.partition(" ")
            values[key] = value
    oid = _SHA256_RE.fullmatch(values.get("oid", ""))
    size = values.get("size", "")
    if oid is None or not size.isdigit():
        return None
    return oid.group(1), int(size)
