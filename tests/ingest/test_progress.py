"""Saved progress applies only to the same document bytes and Part boundaries."""

from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.ingest.progress import (
    document_progress,
    progress_path,
    read_progress,
    write_progress,
)
from second_brain_builder.providers import Usage
from second_brain_builder.store import utc_now


@pytest.mark.parametrize("change", ["document", "part_size", "restart"])
def test_changed_document_or_size_and_explicit_restart_reset_progress(
    brain: Path, tide_pools: PurePosixPath, change: str
) -> None:
    now = utc_now()
    previous = document_progress(brain, tide_pools, 100, now)
    previous = previous.model_copy(update={"parts_done": 1, "usage": Usage(input_tokens=300)})
    assert previous.parts_total > 1
    write_progress(brain, "tide-pools", previous)
    assert read_progress(brain, "tide-pools") == previous
    assert document_progress(brain, tide_pools, 100, now) == previous

    if change == "document":
        with (brain / tide_pools).open("a") as stream:
            stream.write("\nAnother observation.\n")
    current = document_progress(
        brain,
        tide_pools,
        200 if change == "part_size" else 100,
        now,
        resume=change != "restart",
    )
    assert current.parts_done == 0
    assert current.usage == Usage()
    assert current.recent_part_tokens == []


def test_checkpoints_retain_last_two_completed_part_costs(
    brain: Path, tide_pools: PurePosixPath
) -> None:
    initial = document_progress(brain, tide_pools, 100, utc_now())
    assert initial.parts_total > 3
    total = 0
    for number, cost in enumerate([100, 300, 500], start=1):
        total += cost
        # The worker builds each checkpoint from the initial progress model.
        current = initial.model_copy(
            update={"parts_done": number, "completed_usage": Usage(input_tokens=total)}
        )
        write_progress(brain, "tide-pools", current)
        assert read_progress(brain, "tide-pools") == current
    assert current.recent_part_tokens == [300, 500]

    # Usage from an interrupted Part and repeated writes do not advance history.
    current = current.model_copy(update={"usage": Usage(input_tokens=9999)})
    write_progress(brain, "tide-pools", current)
    assert read_progress(brain, "tide-pools").recent_part_tokens == [300, 500]

    restarted = initial.model_copy(
        update={"parts_done": 1, "completed_usage": Usage(input_tokens=700)}
    )
    write_progress(brain, "tide-pools", restarted)
    assert read_progress(brain, "tide-pools").recent_part_tokens == [700]


def test_legacy_progress_without_recent_costs_can_resume(
    brain: Path, tide_pools: PurePosixPath
) -> None:
    from ruamel.yaml import YAML

    progress = document_progress(brain, tide_pools, 100, utc_now())
    progress = progress.model_copy(
        update={"parts_done": 1, "completed_usage": Usage(input_tokens=100)}
    )
    write_progress(brain, "tide-pools", progress)
    data = progress.model_dump(mode="json", exclude={"recent_part_tokens"})
    with progress_path(brain, "tide-pools").open("w") as stream:
        YAML(typ="safe").dump(data, stream)
    legacy = read_progress(brain, "tide-pools")
    assert legacy is not None and legacy.recent_part_tokens == []
    resumed = legacy.model_copy(
        update={"parts_done": 2, "completed_usage": Usage(input_tokens=300, output_tokens=50)}
    )
    write_progress(brain, "tide-pools", resumed)
    assert read_progress(brain, "tide-pools").recent_part_tokens == [250]


def test_progress_does_not_follow_symlinks(brain: Path, tmp_path: Path) -> None:
    (brain / ".brain/ingest").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        read_progress(brain, "tide-pools")
