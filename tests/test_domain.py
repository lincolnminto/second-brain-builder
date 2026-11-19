from enum import Enum

import pytest

from second_brain_builder.domain import (
    BrainStatus,
    JobKind,
    JobStatus,
    SourceStatus,
    TaskMode,
    TaskName,
)


@pytest.mark.parametrize(
    "enum_cls", [JobKind, JobStatus, SourceStatus, BrainStatus, TaskName, TaskMode]
)
def test_members_format_as_their_value(enum_cls: type[Enum]) -> None:
    # Commit messages, job payloads, and log lines put members in f-strings.
    # Python 3.11 changed format() of (str, Enum) members to "Class.NAME".
    for member in enum_cls:
        assert f"{member}" == str(member) == member.value
