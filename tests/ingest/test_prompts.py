"""Every LLM step that writes or extracts content sends the faithfulness rules (ADR 0005).

Each case runs a real Ingest entry point on a real Brain until its first model
request, records that request, and stops. So the test checks the prompt that each
step really sends, not only the prompt builders.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Literal

import pytest

from second_brain_builder.ingest import (
    CanonicalSource,
    Chunk,
    GroupFact,
    NoteGroup,
    SummaryPoint,
    extract_part_notes,
    ingest_source,
    write_group_page,
    write_summary_from_points,
)
from second_brain_builder.ingest._prompts import FAITHFULNESS_RULES
from second_brain_builder.providers import ChatResponse, Message, ToolSpec


class _Recorded(Exception):
    """Stops an Ingest step at its first model request."""


class FirstRequest:
    """A ChatModel that records the first request and then stops the step."""

    def __init__(self) -> None:
        self.system: str | None = None
        self.tools: list[str] = []

    def chat(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        *,
        effort: Literal["low", "medium", "high"] | None = None,
    ) -> ChatResponse:
        self.system = system
        self.tools = [tool.name for tool in tools]
        raise _Recorded


def _group(facts: int) -> NoteGroup:
    return NoteGroup(
        key="example heron",
        kind="entity",
        subtype="person",
        name="Example Heron",
        aliases=[],
        facts=[
            GroupFact(text=f"Fact {number}.", part_number=number + 1, locator=None)
            for number in range(facts)
        ],
    )


def _sequential(root: Path, path: PurePosixPath, model: FirstRequest) -> None:
    ingest_source(root, path, model, "test", "en", 10**9, lambda event: None)


def _extraction(root: Path, path: PurePosixPath, model: FirstRequest) -> None:
    extract_part_notes(
        model=model,
        source=CanonicalSource(id=path.stem, path=path, title="Tide Pools"),
        part=Chunk(text="Rock pools hold sea stars.", headings=()),
        part_number=1,
        canonical_sha256="a" * 64,
        language="en",
    )


def _group_page(facts: int) -> Callable[[Path, PurePosixPath, FirstRequest], None]:
    def run(root: Path, path: PurePosixPath, model: FirstRequest) -> None:
        write_group_page(
            root=root,
            canonical_path=path,
            group=_group(facts),
            model=model,
            model_name="test",
            language="en",
        )

    return run


def _summary(points: int, words: int) -> Callable[[Path, PurePosixPath, FirstRequest], None]:
    def run(root: Path, path: PurePosixPath, model: FirstRequest) -> None:
        write_summary_from_points(
            root=root,
            canonical_path=path,
            points=[
                SummaryPoint(text=f"Point {number}: " + "detail " * words, part_numbers=[1])
                for number in range(points)
            ],
            model=model,
            model_name="test",
            language="en",
        )

    return run


@pytest.mark.parametrize(
    ("step", "tool"),
    [
        pytest.param(_sequential, "finish", id="sequential"),
        pytest.param(_extraction, "record_notes", id="map-extraction"),
        pytest.param(_group_page(121), "record_condensed_facts", id="condensation"),
        pytest.param(_group_page(4), "write_page", id="reduce-writer"),
        pytest.param(_summary(8, 1500), "record_summary_points", id="summary-points"),
        pytest.param(_summary(1, 3), "finish", id="summary-writer"),
    ],
)
def test_every_content_step_sends_the_faithfulness_rules_verbatim(
    brain: Path,
    tide_pools: PurePosixPath,
    step: Callable[[Path, PurePosixPath, FirstRequest], None],
    tool: str,
) -> None:
    model = FirstRequest()

    with pytest.raises(_Recorded):
        step(brain, tide_pools, model)

    assert tool in model.tools  # the request belongs to the step under test
    assert model.system is not None
    assert model.system.count(FAITHFULNESS_RULES) == 1
