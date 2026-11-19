"""Fixtures for the ingest tests: a real Brain in tmp_path, made with ``scaffold_brain``."""

import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.brains import BrainSettings, scaffold_brain

FIXTURES = Path(__file__).parent.parent / "fixtures" / "ingest"


@pytest.fixture
def brain(tmp_path: Path) -> Path:
    """A Brain made from tests/fixtures/ingest/brain, then scaffolded.

    Sources: ``tide-pools`` and ``coastal-birds`` (folder ``nature``). Wiki Pages:
    the summary of coastal-birds and ``grey-heron`` (both cite both Sources;
    grey-heron has ``verified`` and an ``x-sbb-note`` key); ``wading`` and
    ``heron-vs-egret`` (cite coastal-birds only); ``sea-star`` (cites tide-pools
    only, links to wading inline and by reference, and to grey-heron by reference).
    """
    root = tmp_path / "brain"
    shutil.copytree(FIXTURES / "brain", root)
    scaffold_brain(
        root,
        BrainSettings(
            name="Example Shore",
            language="en",
            created_at=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
        ),
    )
    return root


@pytest.fixture
def tide_pools() -> PurePosixPath:
    """The Canonical Document of the ``tide-pools`` Source."""
    return PurePosixPath("sources/nature/tide-pools/tide-pools.md")
