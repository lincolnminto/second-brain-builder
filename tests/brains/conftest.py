"""Fixtures for the brains tests. Git runs for real, against bare repos in tmp_path."""

import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from second_brain_builder.brains import BrainSettings, GitIdentity

FIXTURES = Path(__file__).parent.parent / "fixtures" / "brains"

RunGit = Callable[..., str]


@pytest.fixture
def identity() -> GitIdentity:
    """A synthetic GitHub noreply identity."""
    return GitIdentity(name="Brain Bot", email="1001+brain-bot@users.noreply.github.com")


@pytest.fixture
def brain_settings() -> BrainSettings:
    return BrainSettings(
        name="Example Notes",
        language="en",
        created_at=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def run_git() -> RunGit:
    """Run git in a directory and return its stripped stdout. Use it to inspect repos."""

    def run(cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
        return result.stdout.strip()

    return run


@pytest.fixture
def sample_brain(tmp_path: Path) -> Path:
    """A copy of tests/fixtures/brains/sample: a small Brain with edge cases."""
    root = tmp_path / "sample"
    shutil.copytree(FIXTURES / "sample", root)
    return root
