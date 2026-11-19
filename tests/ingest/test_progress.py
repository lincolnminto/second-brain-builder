"""Saved progress applies only to the same document bytes and Part boundaries."""

from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.ingest.progress import document_progress, read_progress, write_progress
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


def test_progress_does_not_follow_symlinks(brain: Path, tmp_path: Path) -> None:
    (brain / ".brain/ingest").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        read_progress(brain, "tide-pools")
