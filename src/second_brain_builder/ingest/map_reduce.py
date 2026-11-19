"""Map-reduce orchestration without worker storage, commits, or concurrency policy.

Workers can call extract_part_notes concurrently, persist the returned JSON models,
and feed them to run. The runner fills missing Parts sequentially and keeps exactly
one reduce writer. Checkpoints include groups and the next reduce position.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

from second_brain_builder.ingest._map_reduce_notes import (
    MAX_REQUEST_CHARS,
    extract_part_notes,
    group_notes,
)
from second_brain_builder.ingest._map_reduce_writer import (
    write_group_page,
    write_summary_from_points,
)
from second_brain_builder.ingest.chunks import CHUNK_CHARS, split_into_chunks
from second_brain_builder.ingest.loop import _load_source, _page_search, _tokens
from second_brain_builder.ingest.map_reduce_models import (
    MapReduceResult,
    MapReduceState,
    PartNotes,
    SummaryPoint,
)
from second_brain_builder.ingest.models import IngestEvent, IngestStop
from second_brain_builder.providers import ChatModel, Usage
from second_brain_builder.search import SearchIndex

__all__ = [
    "MAX_REQUEST_CHARS",
    "MapReduceIngest",
    "extract_part_notes",
    "group_notes",
    "write_group_page",
    "write_summary_from_points",
]


class MapReduceIngest:
    """Reusable runner bound to a Brain, Source, and Provider-neutral ChatModel.

    ``run`` receives separate map documents and a checkpoint. on_checkpoint runs
    after every Part/group and after the summary; persist its map documents before
    its state. on_usage reports cumulative tokens for this run, including failed
    requests. No checkpoints or map documents are saved by this package.

    Budgets are checked *before every* missing Part and reduce group, including
    the first and the final summary. Estimates use saved completed-phase usage,
    falling back to input characters / 4. An admitted operation can exceed its
    estimate, but has a hard token safety cap of max(4 * estimate, 200000).
    """

    def __init__(
        self,
        *,
        root: Path,
        canonical_path: PurePosixPath,
        model: ChatModel,
        model_name: str,
        language: str,
        index: SearchIndex | None = None,
        brain_id: str | None = None,
        chunk_chars: int = CHUNK_CHARS,
        request_chars: int = MAX_REQUEST_CHARS,
    ) -> None:
        if not model_name.strip() or not language.strip():
            raise ValueError("model_name and language are required")
        if (index is None) != (brain_id is None):
            raise ValueError("give both index and brain_id, or neither")
        if chunk_chars < 1 or request_chars < 1:
            raise ValueError("Part and request sizes must be positive")
        self.root = root
        self.canonical_path = canonical_path
        self.model = model
        self.model_name = model_name
        self.language = language
        self.index = index
        self.brain_id = brain_id
        self.chunk_chars = chunk_chars
        self.request_chars = request_chars

    def run(
        self,
        *,
        max_tokens: int,
        map_results: Sequence[PartNotes] = (),
        state: MapReduceState | None = None,
        on_event: Callable[[IngestEvent], None] | None = None,
        on_usage: Callable[[Usage], None] | None = None,
        on_checkpoint: Callable[[MapReduceResult], None] | None = None,
    ) -> MapReduceResult:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        source, body = _load_source(self.root, self.canonical_path)
        digest = hashlib.sha256((self.root / self.canonical_path).read_bytes()).hexdigest()
        parts = split_into_chunks(body, max_chars=self.chunk_chars)
        if state is None:
            state = MapReduceState(
                canonical_sha256=digest, chunk_chars=self.chunk_chars, parts_total=len(parts)
            )
        else:
            state = MapReduceState.model_validate(state.model_dump())
            if (state.canonical_sha256, state.chunk_chars, state.parts_total) != (
                digest,
                self.chunk_chars,
                len(parts),
            ):
                raise ValueError("checkpoint belongs to a different document or Part size")
        cached: dict[int, PartNotes] = {}
        for notes in map_results:
            notes = PartNotes.model_validate(notes.model_dump())
            if (notes.canonical_sha256, notes.chunk_chars) != (digest, self.chunk_chars):
                continue
            if not 1 <= notes.part_number <= len(parts):
                continue
            if notes.part_number in cached:
                raise ValueError("duplicate map Part number")
            cached[notes.part_number] = notes
        if not set(state.map_parts_done) <= cached.keys():
            raise ValueError("checkpoint is missing its completed map documents")
        if state.phase != "map" and state.groups != group_notes(map_results=list(cached.values())):
            raise ValueError("saved groups do not match the map documents")
        state.map_parts_done = sorted(cached)
        if state.phase == "map":
            state.map_usage = Usage()
            for notes in cached.values():
                state.map_usage += notes.usage
        # Saved map costs inform estimates, but are never charged to this run.
        cumulative_before = state.usage
        usage = Usage()
        written: list[PurePosixPath] = []
        stopped = IngestStop.COMPLETED

        def result() -> MapReduceResult:
            return MapReduceResult(
                state=state.model_copy(deep=True),
                map_results=[cached[number].model_copy(deep=True) for number in sorted(cached)],
                written=list(written),
                usage=usage.model_copy(),
                stopped=stopped,
            )

        def checkpoint() -> None:
            if on_checkpoint is not None:
                on_checkpoint(result())

        def event() -> None:
            if on_event is not None:
                on_event(
                    IngestEvent(
                        kind="progress",
                        data={
                            "source": source.id,
                            "mode": "map_reduce",
                            "phase": state.phase,
                            "map_parts_done": len(cached),
                            "map_parts_total": len(parts),
                            "reduce_groups_done": state.reduce_groups_done,
                            "reduce_groups_total": len(state.groups),
                        },
                    )
                )

        def operation_usage(before: Usage) -> Callable[[Usage], None]:
            def update(current: Usage) -> None:
                nonlocal usage
                usage = before + current
                state.usage = cumulative_before + usage
                if on_usage is not None:
                    on_usage(usage.model_copy())

            return update

        def admitted(estimate: float) -> bool:
            return _tokens(usage) + max(1, estimate) <= max_tokens

        event()
        for number, part in enumerate(parts, 1):
            if number in cached:
                continue
            estimate = (
                _tokens(state.map_usage) / len(cached)
                if cached and _tokens(state.map_usage)
                else max(1, len(part.text) / 4)
            )
            if not admitted(estimate):
                stopped = IngestStop.TOKEN_BUDGET
                return result()
            notes = extract_part_notes(
                model=self.model,
                source=source,
                part=part,
                part_number=number,
                canonical_sha256=digest,
                language=self.language,
                chunk_chars=self.chunk_chars,
                request_chars=self.request_chars,
                safety_cap=max(4 * estimate, 200_000),
                on_usage=operation_usage(usage),
            )
            cached[number] = notes
            state.map_parts_done = sorted(cached)
            state.map_usage += notes.usage
            event()
            checkpoint()
        if state.phase == "map":
            state.groups = group_notes(map_results=list(cached.values()))
            state.phase = "reduce"
            checkpoint()
        if state.phase == "complete":
            return result()
        # One index for the entire reduce phase, including the temporary fallback.
        with _page_search(self.root, self.index, self.brain_id) as search:
            index, brain_id = search._index, search._brain_id
            for group in state.groups[state.reduce_groups_done :]:
                estimate = (
                    _tokens(state.reduce_usage) / state.reduce_groups_done
                    if state.reduce_groups_done and _tokens(state.reduce_usage)
                    else max(1, len(group.model_dump_json()) / 4)
                )
                if not admitted(estimate):
                    stopped = IngestStop.TOKEN_BUDGET
                    return result()
                event()
                page = write_group_page(
                    root=self.root,
                    canonical_path=self.canonical_path,
                    group=group,
                    model=self.model,
                    model_name=self.model_name,
                    language=self.language,
                    index=index,
                    brain_id=brain_id,
                    request_chars=self.request_chars,
                    safety_cap=max(4 * estimate, 200_000),
                    on_event=on_event,
                    on_usage=operation_usage(usage),
                )
                if page.path not in written:
                    written.append(page.path)
                if page.path not in state.written:
                    state.written.append(page.path)
                state.reduce_usage += page.usage
                state.reduce_groups_done += 1
                event()
                checkpoint()
            if parts:
                points = [
                    SummaryPoint(text=text, part_numbers=[number])
                    for number, notes in sorted(cached.items())
                    for text in notes.summary_points
                ]
                estimate = max(1, sum(len(point.text) for point in points) / 4)
                if not admitted(estimate):
                    stopped = IngestStop.TOKEN_BUDGET
                    return result()
                page = write_summary_from_points(
                    root=self.root,
                    canonical_path=self.canonical_path,
                    points=points,
                    model=self.model,
                    model_name=self.model_name,
                    language=self.language,
                    index=index,
                    brain_id=brain_id,
                    request_chars=self.request_chars,
                    safety_cap=max(4 * estimate, 200_000),
                    on_event=on_event,
                    on_usage=operation_usage(usage),
                )
                written.append(page.path)
                if page.path not in state.written:
                    state.written.append(page.path)
            state.summary_done = True
            state.phase = "complete"
            event()
            checkpoint()
        return result()
