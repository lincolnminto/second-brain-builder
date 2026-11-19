"""Fixtures shared by every test directory.

Two autouse fixtures keep each test away from the developer's machine: git sees
an empty global config and a synthetic identity, and no ``SBB_*`` variable from
the shell reaches the settings.
"""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from second_brain_builder.config import Settings, get_settings

GIT_TEST_NAME = "Test Author"
GIT_TEST_EMAIL = "test-author@example.com"


@pytest.fixture(scope="session")
def _git_global_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("git-global") / "gitconfig"


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch, _git_global_config: Path) -> None:
    """Run git with no user or system config, a synthetic identity, and no prompts.

    Every ``GIT_*`` variable from the shell is removed first (a git hook, for
    example, sets ``GIT_DIR`` and ``GIT_INDEX_FILE``). The global config is an
    empty file, so the Git LFS filters from ``git lfs install`` are not set:
    code that needs them must run ``git lfs install --local`` in its repo.
    """
    for name in list(os.environ):
        if name.startswith("GIT_") or name == "SSH_ASKPASS":
            monkeypatch.delenv(name)
    _git_global_config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(_git_global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", GIT_TEST_NAME)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", GIT_TEST_EMAIL)
    monkeypatch.setenv("GIT_COMMITTER_NAME", GIT_TEST_NAME)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", GIT_TEST_EMAIL)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


@pytest.fixture(autouse=True)
def isolated_sbb_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove ``SBB_*`` variables and reset the cached ``get_settings()``."""
    for name in list(os.environ):
        if name.startswith("SBB_"):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings whose data, brains, and models directories are empty dirs in tmp_path.

    The fixture also sets ``SBB_DATA_DIR``, ``SBB_BRAINS_DIR``, and
    ``SBB_MODELS_DIR``, so code that calls ``get_settings()`` sees the same values.
    Use ``settings.model_copy(update={...})`` for other values.
    """
    directories = {
        "SBB_DATA_DIR": tmp_path / "data",
        "SBB_BRAINS_DIR": tmp_path / "brains",
        "SBB_MODELS_DIR": tmp_path / "models",
    }
    for variable, path in directories.items():
        path.mkdir()
        monkeypatch.setenv(variable, str(path))
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def bare_remote(tmp_path: Path) -> Path:
    """An empty bare repo with initial branch ``main``.

    Use ``str(bare_remote)`` or ``bare_remote.as_uri()`` as the clone URL.
    """
    path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "--initial-branch=main", str(path)],
        check=True,
        capture_output=True,
    )
    return path
