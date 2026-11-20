"""Map-reduce orchestration with isolated, disjoint parallel page writers."""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from pathlib import Path, PurePosixPath
from threading import RLock

from second_brain_builder.ingest._map_reduce_notes import (
    MAX_REQUEST_CHARS,
    extract_part_notes,
    group_notes,
)
from second_brain_builder.ingest._map_reduce_plan import related_pages, resolve_targets
from second_brain_builder.ingest._map_reduce_triage import TRIAGE_BATCH_SIZE, triage_batch
from second_brain_builder.ingest._map_reduce_writer import (
    estimate_group_tokens,
    write_group_page,
    write_summary_from_points,
)
from second_brain_builder.ingest._name_indexes import write_name_indexes
from second_brain_builder.ingest._wiki import bundle_paths, source_catalog
from second_brain_builder.ingest.chunks import CHUNK_CHARS, split_into_chunks
from second_brain_builder.ingest.loop import _load_source, _page_search, _tokens
from second_brain_builder.ingest.map_reduce_models import (
    MapPartFailure,
    MapReduceResult,
    MapReduceState,
    PageWriteResult,
    PartNotes,
    ReduceGroupFailure,
    SummaryPoint,
)
from second_brain_builder.ingest.models import IngestEvent, IngestStop
from second_brain_builder.providers import ChatModel, Usage
from second_brain_builder.search import SearchIndex

_log = logging.getLogger(__name__)

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
    after every Part, reduce wave and the summary; persist map documents before
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
        min_facts_per_page: int = 4,
        min_parts_per_page: int = 2,
        reduce_concurrency: int = 8,
    ) -> None:
        if not model_name.strip() or not language.strip():
            raise ValueError("model_name and language are required")
        if (index is None) != (brain_id is None):
            raise ValueError("give both index and brain_id, or neither")
        if (
            min(
                chunk_chars,
                request_chars,
                min_facts_per_page,
                min_parts_per_page,
                reduce_concurrency,
            )
            < 1
        ):
            raise ValueError(
                "Part, request, fact threshold and concurrency limits must be positive"
            )
        self.root = root
        self.canonical_path = canonical_path
        self.model = model
        self.model_name = model_name
        self.language = language
        self.index = index
        self.brain_id = brain_id
        self.chunk_chars = chunk_chars
        self.request_chars = request_chars
        self.min_facts_per_page = min_facts_per_page
        self.min_parts_per_page = min_parts_per_page
        self.reduce_concurrency = reduce_concurrency

    def run(
        self,
        *,
        max_tokens: int,
        map_results: Sequence[PartNotes] = (),
        state: MapReduceState | None = None,
        on_event: Callable[[IngestEvent], None] | None = None,
        on_usage: Callable[[Usage], None] | None = None,
        on_checkpoint: Callable[[MapReduceResult], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
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
            if "schema_version" not in notes.model_fields_set:
                continue
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
        if state.phase != "map":
            original_groups = group_notes(map_results=list(cached.values()))
            if state.reduce_targets:
                matches = Counter(
                    fact.model_dump_json() for group in state.groups for fact in group.facts
                ) == Counter(
                    fact.model_dump_json() for group in original_groups for fact in group.facts
                )
            else:
                matches = state.groups == original_groups
            if not matches:
                raise ValueError("saved groups do not match the map documents")
        for number in cached.keys() - set(state.map_parts_done):
            state.usage += cached[number].usage
        state.map_parts_done = sorted(cached)
        state.map_failed_parts = [
            part for part in state.map_failed_parts if part.part_number not in cached
        ]
        if state.phase == "map":
            state.map_usage = Usage()
            for notes in cached.values():
                state.map_usage += notes.usage
        # Saved map costs inform estimates, but are never charged to this run.
        cumulative_before = state.usage
        usage = Usage()
        written: list[PurePosixPath] = []
        stopped = IngestStop.COMPLETED
        lock = RLock()
        paths: set[PurePosixPath] | None = None

        def result() -> MapReduceResult:
            return MapReduceResult(
                state=state.model_copy(deep=True),
                map_results=[cached[number].model_copy(deep=True) for number in sorted(cached)],
                written=list(written),
                usage=usage.model_copy(),
                stopped=stopped,
            )

        def checkpoint() -> None:
            MapReduceState.model_validate(state.model_dump())
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
                            "reduce_groups_skipped": state.reduce_groups_skipped,
                            "reduce_groups_major": state.reduce_groups_major,
                            "name_index_entries": state.name_index_entries,
                            "name_index_pages": state.name_index_pages,
                            "reduce_failed_groups": [
                                group.model_dump() for group in state.reduce_failed_groups
                            ],
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

        def finish() -> MapReduceResult:
            nonlocal paths
            # Called only after writers drain, including budget/stop/failure exits.
            if state.phase != "map":
                if paths is None:
                    paths = bundle_paths(self.root)
                remaining = [
                    group
                    for group, target in zip(state.groups, state.reduce_targets, strict=True)
                    if not (self.root / target).is_file()
                ]
                pages, stale = write_name_indexes(
                    root=self.root,
                    source=source,
                    groups=remaining,
                    language=self.language,
                    existing_paths=paths,
                )
                state.name_index_entries = len(remaining)
                state.name_index_pages = len(pages)
                paths.difference_update(stale)
                paths.update(pages)
                state.written = [path for path in state.written if path not in stale]
                for path in pages:
                    if path not in written:
                        written.append(path)
                    if path not in state.written:
                        state.written.append(path)
                if self.index is not None and self.brain_id is not None:
                    self.index.update(self.brain_id, self.root, [*pages, *stale])
                event()
                checkpoint()
            return result()

        event()
        for number, part in enumerate(parts, 1):
            if number in cached:
                continue
            if should_stop is not None and should_stop():
                stopped = IngestStop.USER_REQUESTED
                return result()
            estimate = (
                _tokens(state.map_usage) / len(cached)
                if cached and _tokens(state.map_usage)
                else max(1, len(part.text) / 4)
            )
            if not admitted(estimate):
                stopped = IngestStop.TOKEN_BUDGET
                return result()
            try:
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
            except Exception as exc:
                failures_by_part = {part.part_number: part for part in state.map_failed_parts}
                failures_by_part[number] = MapPartFailure(
                    part_number=number, error_kind=type(exc).__name__
                )
                state.map_failed_parts = [failures_by_part[n] for n in sorted(failures_by_part)]
                event()
                checkpoint()
                continue
            cached[number] = notes
            state.map_parts_done = sorted(cached)
            state.map_failed_parts = [
                part for part in state.map_failed_parts if part.part_number != number
            ]
            state.map_usage += notes.usage
            event()
            checkpoint()
        if state.map_failed_parts:
            stopped = IngestStop.EXTRACTION_FAILED
            return result()
        if state.phase == "map":
            state.groups = group_notes(map_results=list(cached.values()))
            state.phase = "reduce"
            checkpoint()
        resolve_targets(self.root, state)
        if state.phase == "complete":
            return finish()
        eligible = {
            number
            for number, group in enumerate(state.groups)
            if len(group.facts) >= self.min_facts_per_page
            or len({fact.part_number for fact in group.facts}) >= self.min_parts_per_page
        }
        rare = [number for number in range(len(state.groups)) if number not in eligible]
        for start in range(0, len(rare), TRIAGE_BATCH_SIZE):
            batch = rare[start : start + TRIAGE_BATCH_SIZE]
            pending_triage = [number for number in batch if number not in state.triage_done]
            if not pending_triage:
                continue
            if should_stop is not None and should_stop():
                stopped = IngestStop.USER_REQUESTED
                return finish()
            estimate = max(
                1,
                sum(
                    len(state.groups[n].name)
                    + sum(len(f.text[:160]) for f in state.groups[n].facts[:2])
                    for n in pending_triage
                )
                / 4,
            )
            if not admitted(estimate):
                stopped = IngestStop.TOKEN_BUDGET
                return finish()
            selected = triage_batch(
                model=self.model,
                source=source,
                groups=[state.groups[n] for n in pending_triage],
                batch_number=start // TRIAGE_BATCH_SIZE + 1,
                request_chars=self.request_chars,
                on_usage=operation_usage(usage),
            )
            state.triage_done = sorted(set(state.triage_done) | set(pending_triage))
            state.triage_major = sorted(
                set(state.triage_major) | {pending_triage[n] for n in selected}
            )
            state.reduce_groups_major = len(state.triage_major)
            event()
            checkpoint()
        eligible.update(state.triage_major)
        related = related_pages(state, eligible)
        catalog = source_catalog(self.root)
        paths = bundle_paths(self.root)
        checkpoint()
        # One index for the entire reduce phase, including the temporary fallback.
        with _page_search(self.root, self.index, self.brain_id) as search:
            index, brain_id = search._index, search._brain_id
            failures = {failure.group_index: failure for failure in state.reduce_failed_groups}
            queue = [number - 1 for number in sorted(failures)] + list(
                range(state.reduce_groups_done, len(state.groups))
            )
            retry_count = len(failures)
            position = 0
            successful_pages = 0
            base_usage = usage.model_copy()
            group_usage: dict[int, Usage] = {}

            def report(number: int, current: Usage) -> None:
                nonlocal usage
                with lock:
                    group_usage[number] = current
                    usage = base_usage.model_copy()
                    for value in group_usage.values():
                        usage += value
                    state.usage = cumulative_before + usage
                    if on_usage is not None:
                        on_usage(usage.model_copy())

            def emit(value: IngestEvent) -> None:
                with lock:
                    if on_event is not None:
                        on_event(value)

            with ThreadPoolExecutor(max_workers=self.reduce_concurrency) as pool:
                while position < len(queue):
                    pending: list[tuple[int, Future[PageWriteResult]]] = []
                    reserved = 0.0
                    skipped = False
                    # Drain writers before checkpoints can commit. A wave never
                    # crosses the next 25-page boundary, including after failures.
                    wave_limit = min(self.reduce_concurrency, 25 - successful_pages % 25)
                    if position < retry_count:
                        wave_limit = min(wave_limit, retry_count - position)
                    while position < len(queue) and len(pending) < wave_limit:
                        if should_stop is not None and should_stop():
                            stopped = IngestStop.USER_REQUESTED
                            break
                        number = queue[position]
                        group = state.groups[number]
                        if number not in eligible:
                            # Keep checkpoint positions ordered with pending writers.
                            if pending:
                                break
                            state.reduce_groups_skipped += 1
                            state.reduce_groups_done = max(state.reduce_groups_done, number + 1)
                            failures.pop(number + 1, None)
                            state.reduce_failed_groups = [failures[n] for n in sorted(failures)]
                            position += 1
                            skipped = True
                            continue
                        estimate = (
                            _tokens(state.reduce_written_usage) / state.reduce_groups_written
                            if state.reduce_groups_written and _tokens(state.reduce_written_usage)
                            else estimate_group_tokens(
                                root=self.root,
                                source=source,
                                group=group,
                                target=state.reduce_targets[number],
                                related_pages=related[number],
                                language=self.language,
                                request_chars=self.request_chars,
                            )
                        )
                        with lock:
                            allowed = admitted(reserved + estimate)
                        if not allowed:
                            break
                        future = pool.submit(
                            write_group_page,
                            root=self.root,
                            canonical_path=self.canonical_path,
                            group=group,
                            target=state.reduce_targets[number],
                            related_pages=related[number],
                            model=self.model,
                            model_name=self.model_name,
                            language=self.language,
                            index=index,
                            brain_id=brain_id,
                            request_chars=self.request_chars,
                            safety_cap=max(4 * estimate, 200_000),
                            on_event=emit,
                            on_usage=partial(report, number),
                            lock=lock,
                            catalog=catalog,
                            paths=paths,
                        )
                        pending.append((number, future))
                        reserved += estimate
                        position += 1
                    if not pending:
                        if skipped:
                            with lock:
                                event()
                                checkpoint()
                        if stopped is IngestStop.USER_REQUESTED:
                            return finish()
                        if position < len(queue):
                            stopped = IngestStop.TOKEN_BUDGET
                            return finish()
                        break
                    # Collect all outcomes before any git/index/progress callback.
                    outcomes: list[tuple[int, PageWriteResult | Exception]] = []
                    for number, future in pending:
                        try:
                            outcomes.append((number, future.result()))
                        except Exception as exc:
                            outcomes.append((number, exc))
                    with lock:
                        for number, outcome in outcomes:
                            state.reduce_groups_done = max(state.reduce_groups_done, number + 1)
                            state.reduce_usage += group_usage.get(number, Usage())
                            if isinstance(outcome, Exception):
                                kind = type(outcome).__name__
                                failures[number + 1] = ReduceGroupFailure(
                                    group_index=number + 1,
                                    fact_count=len(state.groups[number].facts),
                                    error_kind=kind,
                                )
                                _log.warning(
                                    "Reduce group %d failed: facts=%d, error_kind=%s.",
                                    number + 1,
                                    len(state.groups[number].facts),
                                    kind,
                                )
                            else:
                                failures.pop(number + 1, None)
                                state.reduce_written_usage += group_usage.get(number, Usage())
                                state.reduce_groups_written += 1
                                successful_pages += 1
                                if outcome.path not in written:
                                    written.append(outcome.path)
                                if outcome.path not in state.written:
                                    state.written.append(outcome.path)
                            state.reduce_failed_groups = [failures[n] for n in sorted(failures)]
                        event()
                        checkpoint()
                    if stopped is IngestStop.USER_REQUESTED:
                        return finish()
            if should_stop is not None and should_stop():
                stopped = IngestStop.USER_REQUESTED
                return finish()
            if failures:
                stopped = IngestStop.REDUCE_FAILED
                return finish()
            if parts and not state.summary_done:
                points = [
                    SummaryPoint(text=text, part_numbers=[number])
                    for number, notes in sorted(cached.items())
                    for text in notes.summary_points
                ]
                estimate = max(1, sum(len(point.text) for point in points) / 4)
                if not admitted(estimate):
                    stopped = IngestStop.TOKEN_BUDGET
                    return finish()
                try:
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
                        catalog=catalog,
                        paths=paths,
                    )
                except Exception:
                    finish()
                    raise
                written.append(page.path)
                if page.path not in state.written:
                    state.written.append(page.path)
            state.summary_done = True
            state.phase = "complete"
            event()
            checkpoint()
        return finish()
