from pathlib import Path

import pytest

from second_brain_builder.brains import BrainPathError, resolve_in_brain


@pytest.fixture
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "brain"
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "wiki" / "entities" / "grey-heron.md").write_text("# Grey Heron\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.md").write_text("not in the Brain\n")
    return root


def test_resolves_a_path_from_the_brain_root(brain: Path) -> None:
    resolved = resolve_in_brain(brain, "wiki/entities/grey-heron.md")
    assert resolved == brain.resolve() / "wiki" / "entities" / "grey-heron.md"


def test_resolves_a_file_that_does_not_exist_yet(brain: Path) -> None:
    resolved = resolve_in_brain(brain, "wiki/concepts/new-page.md")
    assert resolved == brain.resolve() / "wiki" / "concepts" / "new-page.md"


def test_a_leading_slash_is_the_brain_root_as_in_okf_links(brain: Path) -> None:
    # index.md and OKF links name pages as /wiki/entities/grey-heron.md.
    resolved = resolve_in_brain(brain, "/wiki/entities/grey-heron.md")
    assert resolved == brain.resolve() / "wiki" / "entities" / "grey-heron.md"


@pytest.mark.parametrize(
    "path",
    [
        "../outside/secret.md",
        "wiki/../../outside/secret.md",
        "wiki/entities/../../../outside/secret.md",
    ],
)
def test_rejects_traversal(brain: Path, path: str) -> None:
    with pytest.raises(BrainPathError, match="traversal"):
        resolve_in_brain(brain, path)


def test_a_host_file_system_path_never_leaves_the_brain(brain: Path, tmp_path: Path) -> None:
    host_path = tmp_path / "outside" / "secret.md"
    resolved = resolve_in_brain(brain, str(host_path))
    assert resolved == brain.resolve().joinpath(*host_path.parts[1:])
    assert resolve_in_brain(brain, "/etc/passwd") == brain.resolve() / "etc" / "passwd"


def test_rejects_a_rooted_path_that_climbs_out_of_the_brain(brain: Path) -> None:
    with pytest.raises(BrainPathError, match="traversal"):
        resolve_in_brain(brain, "/../outside/secret.md")


@pytest.mark.parametrize(
    "path", [".git/config", ".brain/brain.yaml", "wiki/.hidden.md", "wiki/.drafts/page.md"]
)
def test_rejects_dot_paths(brain: Path, path: str) -> None:
    with pytest.raises(BrainPathError, match="dot-path"):
        resolve_in_brain(brain, path)


def test_rejects_a_dot_path_given_as_a_rooted_path(brain: Path) -> None:
    with pytest.raises(BrainPathError, match="dot-path"):
        resolve_in_brain(brain, "/.git/config")


@pytest.mark.parametrize(
    "path", ["", ".", "/", "//wiki/entities/grey-heron.md", "wiki\\..\\..\\outside", "wiki/\x00.md"]
)
def test_rejects_paths_that_name_no_file_in_the_brain(brain: Path, path: str) -> None:
    with pytest.raises(BrainPathError):
        resolve_in_brain(brain, path)


def test_rejects_a_symlink_that_escapes_the_brain(brain: Path, tmp_path: Path) -> None:
    (brain / "wiki" / "escape").symlink_to(tmp_path / "outside")
    (brain / "wiki" / "escape.md").symlink_to(tmp_path / "outside" / "secret.md")
    with pytest.raises(BrainPathError, match="outside"):
        resolve_in_brain(brain, "wiki/escape/secret.md")
    with pytest.raises(BrainPathError, match="outside"):
        resolve_in_brain(brain, "wiki/escape.md")


def test_rejects_a_symlink_that_leads_to_a_dot_path(brain: Path) -> None:
    (brain / "wiki" / "repo").symlink_to(brain / ".git")
    with pytest.raises(BrainPathError, match="dot-path"):
        resolve_in_brain(brain, "wiki/repo/config")


def test_follows_a_symlink_that_stays_inside_the_brain(brain: Path) -> None:
    (brain / "wiki" / "heron.md").symlink_to(brain / "wiki" / "entities" / "grey-heron.md")
    resolved = resolve_in_brain(brain, "wiki/heron.md")
    assert resolved == (brain / "wiki" / "entities" / "grey-heron.md").resolve()


def test_rejects_a_loop_of_symlinks(brain: Path) -> None:
    # git checks out symbolic links from a pushed commit as they are.
    (brain / "wiki" / "loop-a").symlink_to("loop-b")
    (brain / "wiki" / "loop-b").symlink_to("loop-a")
    (brain / "wiki" / "a.md").symlink_to("b.md")
    (brain / "wiki" / "b.md").symlink_to("a.md")
    with pytest.raises(BrainPathError):
        resolve_in_brain(brain, "wiki/loop-a/page.md")
    with pytest.raises(BrainPathError):
        resolve_in_brain(brain, "/wiki/a.md")
