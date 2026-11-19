"""GitRepo against real git and local bare remotes. Nothing here needs git-lfs."""

import base64
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from second_brain_builder.brains import (
    BrainSettings,
    GitAuth,
    GitConflictError,
    GitError,
    GitIdentity,
    GitRepo,
    lfs_available,
    read_settings,
    scaffold_brain,
)
from second_brain_builder.okf import validate_bundle

# Synthetic value, built from parts so secret scanners do not flag a test literal.
TOKEN = "ghp_" + "SyntheticTestToken0123456789abcdefXYZ"
ENCODED_CREDENTIALS = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()

RunGit = Callable[..., str]


def clone(remote: Path, root: Path, identity: GitIdentity, auth: GitAuth | None = None) -> GitRepo:
    repo = GitRepo(root, identity, auth)
    repo.clone(str(remote))
    return repo


def commit_file(repo: GitRepo, name: str, text: str, message: str) -> str:
    (repo.root / name).write_text(text)
    sha = repo.commit_all(message)
    assert sha is not None
    return sha


@pytest.fixture
def seeded_remote(bare_remote: Path, tmp_path: Path, identity: GitIdentity) -> Path:
    """The bare remote with one commit on main: shared.md."""
    seeder = clone(bare_remote, tmp_path / "seeder", identity)
    commit_file(seeder, "shared.md", "first line\n", "Seed")
    seeder.push()
    return bare_remote


def test_clone_of_an_empty_remote_then_first_push_creates_main(
    bare_remote: Path, tmp_path: Path, identity: GitIdentity, run_git: RunGit
) -> None:
    repo = clone(bare_remote, tmp_path / "clone", identity)

    assert run_git(repo.root, "symbolic-ref", "HEAD") == "refs/heads/main"
    repo.pull_rebase()  # the remote has no branch yet: nothing to do
    repo.push()  # no commits yet: nothing to push
    assert repo.commit_all("Nothing") is None
    assert repo.lfs_usage_bytes() == 0

    sha = commit_file(repo, "note.md", "one\n", "Add note")
    repo.push()

    assert run_git(bare_remote, "rev-parse", "refs/heads/main") == sha
    assert run_git(repo.root, "rev-parse", "HEAD") == sha


def test_first_push_creates_main_when_the_empty_remote_names_another_branch(
    tmp_path: Path, identity: GitIdentity, run_git: RunGit
) -> None:
    remote = tmp_path / "trunk.git"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "--initial-branch=trunk", str(remote)], check=True
    )
    repo = clone(remote, tmp_path / "clone", identity)
    sha = commit_file(repo, "note.md", "one\n", "Add note")
    repo.push()

    assert run_git(remote, "for-each-ref", "--format=%(refname)") == "refs/heads/main"
    assert run_git(remote, "rev-parse", "refs/heads/main") == sha


def test_commits_use_the_git_identity(seeded_remote: Path, tmp_path: Path, run_git: RunGit) -> None:
    # The test environment sets GIT_AUTHOR_* and GIT_COMMITTER_* to another identity.
    bot = GitIdentity(name="Other Bot", email="2002+other-bot@users.noreply.github.com")
    repo = clone(seeded_remote, tmp_path / "clone", bot)
    commit_file(repo, "note.md", "one\n", "Add note")
    repo.push()

    log = run_git(seeded_remote, "log", "-1", "--format=%an|%ae|%cn|%ce|%s", "main")
    assert log == f"{bot.name}|{bot.email}|{bot.name}|{bot.email}|Add note"


def test_commit_all_stages_new_changed_and_deleted_files(
    seeded_remote: Path, tmp_path: Path, identity: GitIdentity, run_git: RunGit
) -> None:
    repo = clone(seeded_remote, tmp_path / "clone", identity)
    (repo.root / "shared.md").unlink()
    (repo.root / "wiki" / "entities").mkdir(parents=True)
    (repo.root / "wiki" / "entities" / "new.md").write_text("new\n")

    sha = repo.commit_all("Replace shared")

    assert sha == run_git(repo.root, "rev-parse", "HEAD")
    assert run_git(repo.root, "ls-tree", "-r", "--name-only", "HEAD") == "wiki/entities/new.md"
    assert run_git(repo.root, "status", "--porcelain") == ""
    assert repo.commit_all("Again") is None


def test_commit_all_leaves_out_files_of_interrupted_page_writes(
    seeded_remote: Path, tmp_path: Path, identity: GitIdentity, run_git: RunGit
) -> None:
    # okf.write_page and brains.scaffold write a temporary file, then rename it.
    # A worker crash between the two steps leaves the temporary file behind.
    repo = clone(seeded_remote, tmp_path / "clone", identity)
    entities = repo.root / "wiki" / "entities"
    entities.mkdir(parents=True)
    (entities / ".okf-0123456789abcdef.tmp").write_text("---\ntype: entity\n")
    (repo.root / ".sbb-0123456789abcdef.tmp").write_text("# Lo")
    assert repo.commit_all("Nothing to commit") is None

    (entities / "new.md").write_text("new\n")
    repo.commit_all("Add page")

    tracked = run_git(repo.root, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert tracked == ["shared.md", "wiki/entities/new.md"]


def test_a_relative_root_is_taken_from_the_working_directory_once(
    seeded_remote: Path,
    tmp_path: Path,
    identity: GitIdentity,
    run_git: RunGit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SBB_BRAINS_DIR=./brains in a local run gives a relative root.
    monkeypatch.chdir(tmp_path)
    repo = GitRepo(Path("brains") / "b1", identity, None)
    repo.clone(str(seeded_remote))

    assert (tmp_path / "brains" / "b1" / "shared.md").read_text() == "first line\n"
    sha = commit_file(repo, "note.md", "one\n", "Add note")
    repo.push()
    assert run_git(seeded_remote, "rev-parse", "main") == sha


def test_inherited_git_variables_do_not_redirect_commits_or_inject_config(
    seeded_remote: Path,
    tmp_path: Path,
    identity: GitIdentity,
    run_git: RunGit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = clone(seeded_remote, tmp_path / "clone", identity)
    other = tmp_path / "other"
    run_git(tmp_path, "init", "--quiet", str(other))
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook_ran = tmp_path / "hook-ran"
    (hooks / "pre-commit").write_text(f"#!/bin/sh\ntouch '{hook_ran}'\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    stray_index = tmp_path / "stray-index"

    with monkeypatch.context() as patch:
        # A worker started from a git hook inherits GIT_DIR and GIT_INDEX_FILE, and
        # a parent process can pass config in GIT_CONFIG_PARAMETERS.
        patch.setenv("GIT_DIR", str(other / ".git"))
        patch.setenv("GIT_WORK_TREE", str(other))
        patch.setenv("GIT_INDEX_FILE", str(stray_index))
        patch.setenv("GIT_CONFIG_PARAMETERS", f"'core.hooksPath'='{hooks}'")
        sha = commit_file(repo, "note.md", "one\n", "Add note")
        repo.push()

    assert run_git(seeded_remote, "rev-parse", "main") == sha
    assert run_git(other, "rev-list", "--all") == ""
    assert not stray_index.exists()
    assert not hook_ran.exists()


def test_canonical_documents_and_text_assets_diff_and_merge_as_text(
    bare_remote: Path,
    tmp_path: Path,
    identity: GitIdentity,
    brain_settings: BrainSettings,
    run_git: RunGit,
) -> None:
    canonical = PurePosixPath("sources/books/dune/dune.md")
    captions = PurePosixPath("sources/books/dune/assets/dune.vtt")
    lines = [f"Line {number}.\n" for number in range(1, 8)]
    first = clone(bare_remote, tmp_path / "first", identity)
    scaffold_brain(first.root, brain_settings)
    (first.root / captions).parent.mkdir(parents=True)
    (first.root / canonical).write_text("".join(lines))
    (first.root / captions).write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nHello.\n")
    first.commit_all("Add source: Dune")
    first.push()
    second = clone(bare_remote, tmp_path / "second", identity)

    # Two jobs change different lines of the same Canonical Document.
    (first.root / canonical).write_text("".join(["Line one.\n", *lines[1:]]))
    first.commit_all("Edit the first line")
    first.push()
    (second.root / canonical).write_text("".join([*lines[:-1], "Line seven.\n"]))
    (second.root / captions).write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nHello again.\n")
    second.commit_all("Edit the last line")
    second.push()  # rebases onto the first edit without a conflict

    assert (second.root / canonical).read_text() == "".join(
        ["Line one.\n", *lines[1:-1], "Line seven.\n"]
    )
    diff = run_git(second.root, "diff", "HEAD~1", "HEAD")
    assert "Binary files" not in diff
    assert "-Line 7.\n+Line seven." in diff
    assert "-Hello.\n+Hello again." in diff


def test_create_brain_flow_round_trips_through_the_remote(
    bare_remote: Path, tmp_path: Path, identity: GitIdentity, brain_settings: BrainSettings
) -> None:
    writer = clone(bare_remote, tmp_path / "writer", identity)
    scaffold_brain(writer.root, brain_settings)
    assert writer.commit_all("Create brain") is not None
    writer.push()

    reader = GitRepo(tmp_path / "reader", identity, None)
    reader.clone(str(bare_remote), skip_lfs_smudge=True)
    assert read_settings(reader.root) == brain_settings
    assert validate_bundle(reader.root) == []


def test_rejected_push_recovers_by_rebasing_onto_the_remote(
    seeded_remote: Path, tmp_path: Path, identity: GitIdentity, run_git: RunGit
) -> None:
    first = clone(seeded_remote, tmp_path / "first", identity)
    second = clone(seeded_remote, tmp_path / "second", identity)
    commit_file(first, "a.md", "from first\n", "Add a")
    first.push()
    commit_file(second, "b.md", "from second\n", "Add b")

    second.push()

    assert run_git(seeded_remote, "log", "--format=%s", "main").splitlines() == [
        "Add b",
        "Add a",
        "Seed",
    ]
    assert run_git(seeded_remote, "rev-list", "--merges", "--count", "main") == "0"
    assert run_git(seeded_remote, "rev-parse", "main") == run_git(second.root, "rev-parse", "HEAD")
    assert (second.root / "a.md").read_text() == "from first\n"


def test_push_with_a_conflicting_change_aborts_the_rebase(
    seeded_remote: Path, tmp_path: Path, identity: GitIdentity, run_git: RunGit
) -> None:
    first = clone(seeded_remote, tmp_path / "first", identity)
    second = clone(seeded_remote, tmp_path / "second", identity)
    first_sha = commit_file(first, "shared.md", "edited by first\n", "Edit in first")
    first.push()
    second_sha = commit_file(second, "shared.md", "edited by second\n", "Edit in second")

    with pytest.raises(GitConflictError, match="aborted"):
        second.push()

    assert run_git(second.root, "rev-parse", "HEAD") == second_sha
    assert run_git(second.root, "status", "--porcelain") == ""
    assert not (second.root / ".git" / "rebase-merge").exists()
    assert (second.root / "shared.md").read_text() == "edited by second\n"
    assert run_git(seeded_remote, "rev-parse", "main") == first_sha


def test_push_without_retries_reports_the_rejection(
    seeded_remote: Path, tmp_path: Path, identity: GitIdentity
) -> None:
    first = clone(seeded_remote, tmp_path / "first", identity)
    second = clone(seeded_remote, tmp_path / "second", identity)
    commit_file(first, "a.md", "a\n", "Add a")
    first.push()
    commit_file(second, "b.md", "b\n", "Add b")

    with pytest.raises(GitError, match="failed to push") as info:
        second.push(retries=0)
    assert not isinstance(info.value, GitConflictError)


def test_pull_rebase_on_a_clone_without_commits_takes_the_remote_branch(
    bare_remote: Path, tmp_path: Path, identity: GitIdentity, run_git: RunGit
) -> None:
    early = clone(bare_remote, tmp_path / "early", identity)
    other = clone(bare_remote, tmp_path / "other", identity)
    sha = commit_file(other, "a.md", "a\n", "Add a")
    other.push()
    (early.root / "draft.md").write_text("not committed\n")

    early.pull_rebase()

    assert run_git(early.root, "rev-parse", "HEAD") == sha
    assert (early.root / "a.md").read_text() == "a\n"
    assert (early.root / "draft.md").read_text() == "not committed\n"


def test_pull_rebase_keeps_local_commits_on_top(
    seeded_remote: Path, tmp_path: Path, identity: GitIdentity, run_git: RunGit
) -> None:
    first = clone(seeded_remote, tmp_path / "first", identity)
    second = clone(seeded_remote, tmp_path / "second", identity)
    remote_sha = commit_file(first, "a.md", "a\n", "Add a")
    first.push()
    commit_file(second, "b.md", "b\n", "Add b")

    second.pull_rebase()

    assert run_git(second.root, "rev-parse", "HEAD~1") == remote_sha
    assert run_git(second.root, "log", "-1", "--format=%s") == "Add b"


def test_token_reaches_git_only_through_the_environment(
    bare_remote: Path,
    tmp_path: Path,
    identity: GitIdentity,
    run_git: RunGit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lfs_available()  # cached; a probe without a token, outside the recorded calls
    calls: list[tuple[list[str], dict[str, str]]] = []
    real_run = subprocess.run

    def recording_run(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        calls.append((list(argv), dict(kwargs.get("env") or {})))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    # With these variables, git writes config values (the auth header included)
    # and HTTP headers to the trace files. The repo must not pass them on.
    traces = {
        name: tmp_path / f"{name.lower()}.txt"
        for name in ("GIT_TRACE", "GIT_TRACE2_EVENT", "GIT_TRACE_CURL", "GIT_CURL_VERBOSE")
    }
    for name, path in traces.items():
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("GIT_TRACE2_CONFIG_PARAMS", "http.*")

    repo = clone(bare_remote, tmp_path / "clone", identity, GitAuth(token=SecretStr(TOKEN)))
    commit_file(repo, "a.md", "a\n", "Add a")
    repo.push()
    repo.pull_rebase()
    recorded = list(calls)

    assert {argv[1] for argv, _ in recorded} >= {"clone", "commit", "push", "fetch"}
    for argv, env in recorded:
        assert not any(TOKEN in arg or ENCODED_CREDENTIALS in arg for arg in argv)
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        config = {
            env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
            for index in range(int(env["GIT_CONFIG_COUNT"]))
        }
        assert config["http.https://github.com/.extraheader"] == (
            f"AUTHORIZATION: basic {ENCODED_CREDENTIALS}"
        )
        assert not [name for name in env if name.startswith(("GIT_TRACE", "GIT_CURL_VERBOSE"))]
    assert not [path for path in traces.values() if path.exists()]
    assert TOKEN not in (repo.root / ".git" / "config").read_text()
    assert ENCODED_CREDENTIALS not in (repo.root / ".git" / "config").read_text()
    assert run_git(repo.root, "remote", "get-url", "origin") == str(bare_remote)


def test_git_errors_never_contain_the_token(tmp_path: Path, identity: GitIdentity) -> None:
    repo = GitRepo(tmp_path / "clone", identity, GitAuth(token=SecretStr(TOKEN)))
    missing = tmp_path / f"missing-{TOKEN}.git"

    with pytest.raises(GitError) as info:
        repo.clone(str(missing))

    message = str(info.value)
    assert TOKEN not in message
    assert "missing-***.git" in message


@pytest.mark.parametrize(
    "url",
    [
        f"https://x-access-token:{TOKEN}@github.com/example/brain.git",
        f"https://{TOKEN}@github.com/example/brain.git",
    ],
)
def test_clone_refuses_credentials_in_the_url(
    url: str, tmp_path: Path, identity: GitIdentity
) -> None:
    repo = GitRepo(tmp_path / "clone", identity, None)
    with pytest.raises(ValueError, match="must not contain credentials") as info:
        repo.clone(url)
    assert TOKEN not in str(info.value)
    assert not (tmp_path / "clone").exists()


@pytest.mark.parametrize(
    "url",
    [
        # Local addresses: nothing reaches the network if the check is ever lost.
        f"https::https://{TOKEN}@127.0.0.1:9/example/brain.git",
        "https::https://127.0.0.1:9/example/brain.git",
        "ext::git-upload-pack /srv/brain.git",
    ],
)
def test_clone_refuses_git_remote_helper_urls(
    url: str, tmp_path: Path, identity: GitIdentity
) -> None:
    # git runs a remote helper with the whole address, past the credentials check.
    repo = GitRepo(tmp_path / "clone", identity, None)
    with pytest.raises(ValueError, match="transport") as info:
        repo.clone(url)
    assert TOKEN not in str(info.value)
    assert not (tmp_path / "clone").exists()


@pytest.mark.parametrize(
    "token",
    [
        "",
        "ghp_Synthetic\r\nX-Injected: yes",
        "ghp_Synthetic abc",
        "ghp_Synthetic_é",
        "ghp_Synthetic0123\n",  # a pasted token with a line break
        "ghp_Synthetic0123 ",
    ],
)
@pytest.mark.parametrize("as_secret", [True, False], ids=["SecretStr", "str"])
def test_git_auth_refuses_tokens_that_could_change_the_http_header(
    token: str, as_secret: bool
) -> None:
    value = SecretStr(token) if as_secret else token
    with pytest.raises(ValidationError) as info:
        GitAuth.model_validate({"token": value})
    # A job stores str(error); an error report can carry repr() or the JSON form.
    for text in (str(info.value), repr(info.value), info.value.json()):
        assert "Synthetic" not in text


@pytest.mark.parametrize(
    "path", ["../outside.pdf", "/etc/passwd", ".git/config", "sources/*.pdf", "a,b.pdf"]
)
def test_lfs_pull_refuses_paths_that_are_not_plain_files(
    path: str, tmp_path: Path, identity: GitIdentity
) -> None:
    repo = GitRepo(tmp_path, identity, None)
    with pytest.raises(ValueError, match="path"):
        repo.lfs_pull([PurePosixPath(path)])
