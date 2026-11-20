"""Durable parallel map extraction and checkpointed, isolated reduce writers."""

from __future__ import annotations

import logging
import os
import tempfile
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from functools import partial
from pathlib import Path, PurePosixPath
from threading import RLock

from second_brain_builder.domain import SourceStatus
from second_brain_builder.ingest import (
    MapReduceIngest,
    MapReduceResult,
    MapReduceState,
    PartNotes,
    extract_part_notes,
    group_notes,
    split_into_chunks,
)
from second_brain_builder.ingest.loop import _load_source
from second_brain_builder.ingest.map_reduce_models import MapPartFailure
from second_brain_builder.ingest.models import IngestStop
from second_brain_builder.ingest.progress import IngestProgress, read_progress, write_progress
from second_brain_builder.okf import LogKind, log_entry
from second_brain_builder.providers import Usage
from second_brain_builder.services.payloads import SourceIngestPayload
from second_brain_builder.store import BrainRow
from second_brain_builder.worker.clone import BrainClone
from second_brain_builder.worker.context import JobRun
from second_brain_builder.worker.errors import JobError
from second_brain_builder.worker.rows import derived_status, set_source_status

_log = logging.getLogger(__name__)


def map_directory(run: JobRun, brain_id: str, slug: str) -> Path:
    return run.ctx.settings.data_dir / "ingest" / brain_id / slug / "map"


def _save_notes(directory: Path, notes: PartNotes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, delete=False
        ) as stream:
            temporary = stream.name
            stream.write(notes.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / f"part-{notes.part_number:04d}.json")
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _tokens(usage: Usage) -> int:
    return usage.input_tokens + usage.output_tokens


def rekey_map_notes(directory: Path, old_digest: str, digest: str) -> None:
    """Preserve extraction across a move that only rewrites canonical links."""
    for path in directory.glob("part-*.json"):
        try:
            notes = PartNotes.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if "schema_version" in notes.model_fields_set and notes.canonical_sha256 == old_digest:
            notes.canonical_sha256 = digest
            _save_notes(directory, notes)


def ingest_map_reduce(
    run: JobRun,
    payload: SourceIngestPayload,
    brain: BrainRow,
    clone: BrainClone,
    canonical: PurePosixPath,
    language: str,
    initial: IngestProgress,
) -> None:
    root, slug = clone.root, payload.source_slug
    if payload.resume and initial.mode == "map_reduce" and initial.status == "complete":
        set_source_status(run.ctx, brain.id, slug, SourceStatus.INGESTED)
        return
    model = run.ingest_model()
    if model is None:
        raise JobError("No ingest Provider and model are set. Set them before ingesting.")
    source, body = _load_source(root, canonical)
    parts = split_into_chunks(body, max_chars=initial.chunk_chars)
    directory = map_directory(run, brain.id, slug)
    lock = RLock()
    with lock:
        cached: dict[int, PartNotes] = {}
        for number in range(1, len(parts) + 1):
            try:
                notes = PartNotes.model_validate_json(
                    (directory / f"part-{number:04d}.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if "schema_version" not in notes.model_fields_set:
                continue
            if (notes.canonical_sha256, notes.chunk_chars, notes.part_number) == (
                initial.canonical_sha256,
                initial.chunk_chars,
                number,
            ):
                cached[number] = notes
        saved_state = initial.map_reduce_state if payload.resume else None
        state: MapReduceState
        if (
            saved_state is None
            or saved_state.canonical_sha256 != initial.canonical_sha256
            or not set(saved_state.map_parts_done) <= cached.keys()
        ):
            state = MapReduceState(
                canonical_sha256=initial.canonical_sha256,
                chunk_chars=initial.chunk_chars,
                parts_total=len(parts),
                usage=initial.usage,
            )
        else:
            state = saved_state
        previous_usage = initial.usage.model_copy()
        accounted = set(saved_state.map_parts_done) if saved_state is not None else set()
        for number in cached.keys() - accounted:
            previous_usage += cached[number].usage
        progress = initial
        map_usage = Usage()
        current_usage = Usage()
    part_usage: dict[int, Usage] = {}
    budget = payload.max_tokens or run.ctx.settings.ingest_max_tokens
    set_source_status(run.ctx, brain.id, slug, SourceStatus.INGESTING)

    def failure_status() -> None:
        saved = read_progress(root, slug)
        status = (
            derived_status(root, slug)
            if saved is not None and saved.map_parts_done
            else SourceStatus.CONVERTED
        )
        set_source_status(run.ctx, brain.id, slug, status)

    run.on_failure(failure_status)

    def usage(used: Usage) -> None:
        with lock:
            update_usage(used)

    def update_usage(used: Usage) -> None:
        nonlocal current_usage
        # Both map callbacks and the main thread hold this same lock.
        current_usage = used.model_copy()
        state.usage = previous_usage + current_usage
        run.usage = used
        run.ctx.jobs.update_usage(
            run.job.id,
            used,
            {
                "source": slug,
                "mode": "map_reduce",
                "phase": state.phase,
                "parts_done": len(cached),
                "parts_total": len(parts),
                "map_parts_done": len(cached),
                "map_failed_parts": [part.model_dump() for part in state.map_failed_parts],
                "reduce_groups_done": state.reduce_groups_done,
                "reduce_groups_total": state.reduce_groups_total,
                "reduce_groups_skipped": state.reduce_groups_skipped,
                "reduce_groups_major": state.reduce_groups_major,
                "name_index_entries": state.name_index_entries,
                "name_index_pages": state.name_index_pages,
                "reduce_failed_groups": [
                    group.model_dump() for group in state.reduce_failed_groups
                ],
            },
            worker_id=run.worker_id,
        )

    def save() -> None:
        with lock:
            save_progress()

    def save_progress() -> None:
        nonlocal progress
        if not cached and parts:
            return
        MapReduceState.model_validate(state.model_dump())
        progress = initial.model_copy(
            update={
                "mode": "map_reduce",
                "phase": state.phase,
                "parts_done": len(cached),
                "map_parts_done": len(cached),
                "notes_dropped_total": sum(
                    notes.dropped_items + notes.dropped_summary_points for notes in cached.values()
                ),
                "parts_with_drops": sum(
                    bool(notes.dropped_items or notes.dropped_summary_points)
                    for notes in cached.values()
                ),
                "map_failed_parts": list(state.map_failed_parts),
                "reduce_groups_done": state.reduce_groups_done,
                "reduce_groups_total": state.reduce_groups_total,
                "reduce_groups_skipped": state.reduce_groups_skipped,
                "reduce_groups_major": state.reduce_groups_major,
                "name_index_entries": state.name_index_entries,
                "name_index_pages": state.name_index_pages,
                "reduce_failed_groups": list(state.reduce_failed_groups),
                "status": "complete" if state.phase == "complete" else "partial",
                "usage": previous_usage + current_usage,
                "completed_usage": state.map_usage + state.reduce_usage,
                "map_reduce_state": state.model_copy(deep=True),
                "updated_at": run.ctx.clock().replace(microsecond=0),
            }
        )
        write_progress(root, slug, progress)

    def map_update(number: int, used: Usage) -> None:
        nonlocal map_usage
        with lock:
            part_usage[number] = used
            map_usage = Usage()
            for value in part_usage.values():
                map_usage += value
            usage(map_usage)

    stopped = IngestStop.TOKEN_BUDGET
    error: Exception | None = None
    try:
        with lock:
            state.map_parts_done = sorted(cached)
            failed_parts = {
                part.part_number: part
                for part in state.map_failed_parts
                if part.part_number not in cached
            }
            state.map_failed_parts = [failed_parts[number] for number in sorted(failed_parts)]
            state.map_usage = Usage()
            for notes in cached.values():
                state.map_usage += notes.usage
            usage(Usage())
            save()
        with lock:
            missing = iter([number for number in range(1, len(parts) + 1) if number not in cached])
        next_number = next(missing, None)
        pending: dict[Future[PartNotes], tuple[int, float]] = {}
        with ThreadPoolExecutor(max_workers=run.ctx.settings.ingest_map_concurrency) as pool:
            while pending or next_number is not None:
                while (
                    next_number is not None
                    and len(pending) < run.ctx.settings.ingest_map_concurrency
                ):
                    if run.stop_requested():
                        stopped = IngestStop.USER_REQUESTED
                        next_number = None
                        break
                    # Reserve estimates for requests already admitted but still in flight.
                    with lock:
                        historical = sum(_tokens(notes.usage) for notes in cached.values())
                        estimate = (
                            historical / len(cached)
                            if cached and historical
                            else max(1, len(parts[next_number - 1].text) / 4)
                        )
                        admitted = (
                            _tokens(map_usage)
                            + sum(value[1] for value in pending.values())
                            + estimate
                            <= budget
                        )
                    if not admitted:
                        # Reconsider after in-flight requests finish and their actual cost is known.
                        break
                    number = next_number
                    future = pool.submit(
                        extract_part_notes,
                        model=model.chat_model,
                        source=source,
                        part=parts[number - 1],
                        part_number=number,
                        canonical_sha256=initial.canonical_sha256,
                        language=language,
                        chunk_chars=initial.chunk_chars,
                        safety_cap=max(4 * estimate, 200_000),
                        on_usage=partial(map_update, number),
                    )
                    pending[future] = (number, estimate)
                    next_number = next(missing, None)
                if not pending:
                    break
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                with lock:
                    for future in done:
                        number, _ = pending.pop(future)
                        try:
                            notes = future.result()
                        except Exception as exc:
                            kind = type(exc).__name__
                            failed_parts[number] = MapPartFailure(
                                part_number=number, error_kind=kind
                            )
                            _log.warning("Map Part %d failed extraction: %s.", number, kind)
                        else:
                            _save_notes(directory, notes)
                            cached[notes.part_number] = notes
                            failed_parts.pop(number, None)
                    state.map_parts_done = sorted(cached)
                    state.map_failed_parts = [
                        failed_parts[number] for number in sorted(failed_parts)
                    ]
                    state.map_usage = Usage()
                    for notes in cached.values():
                        state.map_usage += notes.usage
                    usage(map_usage)
                    save()
        if stopped is not IngestStop.USER_REQUESTED and run.stop_requested():
            stopped = IngestStop.USER_REQUESTED
        with lock:
            map_cost = map_usage.model_copy()
            cached_notes = list(cached.values())
            cached_count = len(cached)
            map_failed = bool(state.map_failed_parts)
        if map_failed and stopped is not IngestStop.USER_REQUESTED:
            stopped = IngestStop.EXTRACTION_FAILED
        if (
            stopped is not IngestStop.USER_REQUESTED
            and not map_failed
            and cached_count == len(parts)
        ):
            with lock:
                if state.phase == "map":
                    state.groups = group_notes(map_results=cached_notes)
                    state.phase = "reduce"
                    usage(map_cost)
                    save()
            last_checkpoint_pages = len(state.written)

            def checkpoint(result: MapReduceResult) -> None:
                nonlocal state, last_checkpoint_pages
                state = result.state
                usage(map_cost + result.usage)
                save()
                if len(state.written) - last_checkpoint_pages >= 25:
                    clone.keep_ingest_checkpoints = True
                    clone.finish(
                        f"Ingest: {source.title} (Part {state.reduce_groups_done} "
                        f"of {state.reduce_groups_total} pages)",
                        [],
                    )
                    last_checkpoint_pages = len(state.written)

            remaining = budget - _tokens(map_cost)
            if remaining > 0:
                result = MapReduceIngest(
                    root=root,
                    canonical_path=canonical,
                    model=model.chat_model,
                    model_name=model.model_name,
                    language=language,
                    index=run.ctx.search,
                    brain_id=brain.id,
                    chunk_chars=initial.chunk_chars,
                    min_facts_per_page=run.ctx.settings.ingest_min_facts_per_page,
                    min_parts_per_page=run.ctx.settings.ingest_min_parts_per_page,
                    reduce_concurrency=run.ctx.settings.ingest_reduce_concurrency,
                ).run(
                    max_tokens=remaining,
                    map_results=cached_notes,
                    state=state,
                    on_event=run.emit_ingest_event,
                    on_usage=lambda used: usage(map_cost + used),
                    on_checkpoint=checkpoint,
                    should_stop=run.stop_requested,
                )
                state, stopped = result.state, result.stopped
    except Exception as exc:
        error = exc
        stopped = IngestStop.ERROR
    with lock:
        cached_count = len(cached)
        failed_count = len(state.map_failed_parts)
    if not cached_count and parts:
        set_source_status(run.ctx, brain.id, slug, SourceStatus.CONVERTED)
        if error is not None:
            raise JobError(f"Map-reduce Ingest failed: {error}") from None
        if stopped is IngestStop.USER_REQUESTED:
            run.stop_message = f"Stopped by the user after Part 0 of {len(parts)}"
            return
        if failed_count:
            raise JobError(f"{failed_count} Parts failed extraction; Continue Ingest retries them")
        return
    with lock:
        save()
        progress.last_stop = stopped
        write_progress(root, slug, progress)
    clone.keep_ingest_checkpoints = True
    detail = (
        f"Map Parts {cached_count} of {len(parts)}, pages {state.reduce_groups_done} "
        f"of {state.reduce_groups_total}; "
        + ("run failed" if error is not None else f"stopped ({stopped})")
    )
    failure_message = (
        f"{failed_count} Parts failed extraction; Continue Ingest retries them"
        if failed_count
        else None
    )
    if state.reduce_failed_groups:
        failure_message = (
            f"{len(state.reduce_failed_groups)} pages failed; Continue Ingest retries them"
        )
    if failure_message is not None:
        detail += "; " + failure_message
    try:
        clone.finish(
            f"Ingest: {source.title} (run {'failed' if error else 'complete'})",
            [log_entry(LogKind.INGEST, source.title, canonical, detail)],
        )
    except Exception:
        if error is None:
            raise
        _log.warning("Could not publish the failed map-reduce Ingest log for %s.", slug)
    if error is not None:
        raise JobError(f"Map-reduce Ingest failed: {error}") from None
    if failure_message is not None and stopped is not IngestStop.USER_REQUESTED:
        raise JobError(failure_message)
    set_source_status(run.ctx, brain.id, slug, derived_status(root, slug))
    if stopped is IngestStop.USER_REQUESTED:
        set_source_status(run.ctx, brain.id, slug, SourceStatus.PARTLY_INGESTED)
        run.stop_message = (
            f"Stopped by the user after Part {cached_count} of {len(parts)}"
            if state.phase == "map"
            else f"Stopped by the user after reduce group {state.reduce_groups_done} "
            f"of {state.reduce_groups_total}"
        )
