"""Git LFS behavior of a scaffolded Brain. Skipped when git-lfs is not installed."""

import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.brains import (
    BrainLayout,
    BrainSettings,
    GitError,
    GitIdentity,
    GitRepo,
    scaffold_brain,
)


def _git_lfs_runs() -> bool:
    try:
        return subprocess.run(["git", "lfs", "version"], capture_output=True).returncode == 0
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _git_lfs_runs(), reason="git lfs version fails")

RunGit = Callable[..., str]
POINTER_START = b"version https://git-lfs.github.com/spec/v1\n"
FOLDER = PurePosixPath("reports")
layout = BrainLayout()


def synthetic_binary(size: int, seed: int) -> bytes:
    return bytes((index * 31 + seed) % 256 for index in range(size))


@pytest.fixture
def writer(
    bare_remote: Path, tmp_path: Path, identity: GitIdentity, brain_settings: BrainSettings
) -> GitRepo:
    """A clone of an empty remote with a scaffolded Brain, pushed."""
    repo = GitRepo(tmp_path / "writer", identity, None)
    repo.clone(str(bare_remote))
    scaffold_brain(repo.root, brain_settings)
    repo.commit_all("Create brain")
    repo.push()
    return repo


def add_source(repo: GitRepo, slug: str, content: bytes) -> PurePosixPath:
    source = layout.source_file_path(FOLDER, slug, "pdf")
    (repo.root / source).parent.mkdir(parents=True, exist_ok=True)
    (repo.root / source).write_bytes(content)
    return source


def test_sources_are_stored_in_lfs_and_markdown_and_assets_are_not(
    writer: GitRepo, bare_remote: Path, run_git: RunGit
) -> None:
    content = synthetic_binary(5000, seed=1)
    source = add_source(writer, "tide-tables", content)
    canonical = layout.canonical_path(FOLDER, "tide-tables")
    (writer.root / canonical).write_text("---\ntype: source\ntitle: Tide Tables\n---\n\nText.\n")
    asset = layout.assets_dir(FOLDER, "tide-tables") / "chart.png"
    (writer.root / asset).parent.mkdir()
    (writer.root / asset).write_bytes(synthetic_binary(2000, seed=2))
    writer.commit_all("Add source: Tide Tables")
    writer.push()

    pointer = run_git(bare_remote, "cat-file", "-p", f"main:{source}")
    assert pointer.encode().startswith(POINTER_START)
    assert f"size {len(content)}" in pointer
    assert run_git(bare_remote, "cat-file", "-s", f"main:{canonical}") == str(
        (writer.root / canonical).stat().st_size
    )
    assert run_git(bare_remote, "cat-file", "-s", f"main:{asset}") == "2000"


def test_skip_smudge_clone_keeps_pointers_until_lfs_pull(
    writer: GitRepo, bare_remote: Path, tmp_path: Path, identity: GitIdentity, run_git: RunGit
) -> None:
    content = synthetic_binary(5000, seed=3)
    source = add_source(writer, "tide-tables", content)
    writer.commit_all("Add source: Tide Tables")
    writer.push()

    reader = GitRepo(tmp_path / "reader", identity, None)
    reader.clone(str(bare_remote), skip_lfs_smudge=True)
    assert (reader.root / source).read_bytes().startswith(POINTER_START)

    reader.lfs_pull([source])
    assert (reader.root / source).read_bytes() == content
    assert run_git(reader.root, "status", "--porcelain") == ""

    # Later pulls keep skipping downloads.
    later = add_source(writer, "harbor-map", synthetic_binary(3000, seed=4))
    writer.commit_all("Add source: Harbor Map")
    writer.push()
    reader.pull_rebase()
    assert (reader.root / later).read_bytes().startswith(POINTER_START)


def test_default_clone_downloads_lfs_content(
    writer: GitRepo, bare_remote: Path, tmp_path: Path, identity: GitIdentity
) -> None:
    content = synthetic_binary(4000, seed=5)
    source = add_source(writer, "tide-tables", content)
    writer.commit_all("Add source: Tide Tables")
    writer.push()

    full = GitRepo(tmp_path / "full", identity, None)
    full.clone(str(bare_remote))

    assert (full.root / source).read_bytes() == content


def test_lfs_usage_counts_every_version_and_each_object_once(
    writer: GitRepo, bare_remote: Path, tmp_path: Path, identity: GitIdentity
) -> None:
    first = synthetic_binary(1000, seed=6)
    add_source(writer, "tide-tables", first)
    add_source(writer, "tide-tables-copy", first)  # same object as tide-tables
    writer.commit_all("Add sources")
    add_source(writer, "tide-tables", synthetic_binary(3000, seed=7))  # a new version
    writer.commit_all("Replace source")
    writer.push()

    reader = GitRepo(tmp_path / "reader", identity, None)
    reader.clone(str(bare_remote), skip_lfs_smudge=True)

    assert writer.lfs_usage_bytes() == 4000
    assert reader.lfs_usage_bytes() == 4000


@pytest.mark.parametrize("root_exists", [False, True], ids=["new-root", "empty-root"])
def test_clone_is_removed_when_the_lfs_download_fails(
    writer: GitRepo,
    bare_remote: Path,
    tmp_path: Path,
    identity: GitIdentity,
    run_git: RunGit,
    root_exists: bool,
) -> None:
    # The remote has a pointer file whose LFS object was never uploaded.
    source = layout.source_file_path(FOLDER, "tide-tables", "pdf")
    (writer.root / source).parent.mkdir(parents=True)
    (writer.root / source).write_text(
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{'0' * 64}\nsize 12\n"
    )
    writer.commit_all("Add source: Tide Tables")
    # The git-lfs pre-push hook refuses to push a pointer without its object.
    run_git(writer.root, "push", "--quiet", "--no-verify", "origin", "HEAD:refs/heads/main")
    root = tmp_path / "full"
    if root_exists:
        root.mkdir()
    full = GitRepo(root, identity, None)

    with pytest.raises(GitError, match="lfs pull"):
        full.clone(str(bare_remote))

    if root_exists:
        assert list(root.iterdir()) == []
    else:
        assert not root.exists()
    # A retried job can clone into the same root.
    full.clone(str(bare_remote), skip_lfs_smudge=True)
    assert (root / source).read_bytes().startswith(POINTER_START)
