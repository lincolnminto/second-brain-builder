"""The jobs of one Source: add, ingest, reconvert, move, and delete."""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import (
    ASSETS_DIR_NAME,
    LOG_PATH,
    SOURCES_DIR,
    BrainLayout,
    is_slug,
    lfs_available,
    read_pages,
    read_settings,
    slugify,
    unique_slug,
)
from second_brain_builder.conversion import (
    ConversionResult,
    UnsupportedFormatError,
    convert_source,
    detect_media_type,
    sha256_file,
)
from second_brain_builder.conversion.formats import SUPPORTED_FORMATS
from second_brain_builder.domain import SourceStatus
from second_brain_builder.ingest import (
    IngestEvent,
    IngestResult,
    IngestStop,
    ingest_source,
    remove_source,
)
from second_brain_builder.ingest.chunks import CHARS_PER_TOKEN
from second_brain_builder.ingest.progress import (
    document_progress,
    progress_path,
    read_progress,
    write_progress,
)
from second_brain_builder.okf import (
    LogKind,
    Page,
    apply_moves,
    bundle_href,
    is_external,
    log_entry,
    read_page,
    remove_links,
    resolve_link,
    rewrite_links,
    write_page,
)
from second_brain_builder.providers import Usage
from second_brain_builder.services import (
    ConflictError,
    NotConfiguredError,
    SourceAddPayload,
    SourceJobPayload,
    SourceMovePayload,
)
from second_brain_builder.services import ingest_source as enqueue_ingest
from second_brain_builder.services.payloads import SourceIngestPayload
from second_brain_builder.services.providers import ingest_configured
from second_brain_builder.services.uploads import remove_staged_upload, staged_upload_path
from second_brain_builder.store import BrainRow
from second_brain_builder.worker.canonical import (
    SourceFacts,
    canonical_documents,
    canonical_frontmatter,
    describe_source,
    folder_of,
    folder_path,
    folder_text,
    page_event,
    page_ref,
    read_source_record,
    source_facts,
    source_file,
    summary_path,
)
from second_brain_builder.worker.clone import remove_empty_dirs
from second_brain_builder.worker.context import JobRun
from second_brain_builder.worker.errors import JobError, scrub
from second_brain_builder.worker.handlers.source_map_reduce import ingest_map_reduce, map_directory
from second_brain_builder.worker.rows import (
    delete_source_row,
    derived_status,
    set_source_status,
    source_row_slugs,
)

_log = logging.getLogger(__name__)

_LFS_POINTER_START = b"version https://git-lfs.github.com/spec/v1\n"
_LFS_POINTER_MAX_BYTES = 1024


# --- source.add -------------------------------------------------------------------------


def add_source(run: JobRun) -> None:
    """Convert a staged upload into a Source, its Canonical Document, and its Assets.

    The slug comes from the original filename, because Conversion names the
    transcript Asset of audio and video after the Source file. The title comes from
    the metadata, or the filename. After the push, the staged upload is deleted,
    and ``source.ingest`` is enqueued when an ingest Provider is set.
    """
    ctx = run.ctx
    payload = run.payload(SourceAddPayload)
    upload = staged_upload_path(ctx, payload.to_json())
    brain = run.brain(payload.brain_id)
    clone = run.open_clone(brain)
    root = clone.root

    existing = _source_with_sha256(root, payload.sha256)
    if existing is not None:
        # An earlier attempt pushed this Source and then stopped.
        _log.info("Source %s is in Brain %s already.", existing, brain.id)
        clone.finish("Add source", [])
        remove_staged_upload(ctx, payload.to_json())
        _enqueue_ingest(run, brain.id, existing)
        return

    if upload is None or not upload.is_file():
        raise JobError("The uploaded file is gone. Add the file again.")

    media_type = detect_media_type(upload)
    fmt = SUPPORTED_FORMATS.get(media_type)
    if fmt is None:
        raise UnsupportedFormatError(media_type)
    folder = _folder(payload.folder, root)
    layout = BrainLayout()
    slug = _new_slug(run, brain, root, folder, PurePosixPath(payload.original_filename).stem)
    source_rel = layout.source_file_path(folder, slug, fmt.extension)
    canonical = layout.canonical_path(folder, slug)
    source_abs = _absolute(root, source_rel)
    source_abs.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(upload, source_abs)
    digest = sha256_file(source_abs)
    if digest != payload.sha256:
        raise JobError("The uploaded file changed after it was added. Add the file again.")

    run.progress("convert", source=slug)
    result = convert_source(
        source_abs,
        _absolute(root, layout.assets_dir(folder, slug)),
        run.conversion_options(media_type),
    )
    _report_warnings(run, slug, result)
    now = _now(run)
    title = _title(result.metadata.title, payload.original_filename)
    facts = SourceFacts(
        original_filename=payload.original_filename,
        sha256=digest,
        media_type=media_type,
        size_bytes=source_abs.stat().st_size,
        converter=result.converter,
        added_at=now,
    )
    page = Page(
        path=canonical,
        frontmatter=canonical_frontmatter(
            title=title,
            description=describe_source(fmt.extension, result.metadata),
            resource=f"./{source_rel.name}",
            facts=facts,
            generated_at=now,
        ),
        body=result.markdown,
    )
    write_page(root, page)
    clone.finish(f"Add source: {title}", [log_entry(LogKind.ADD, title, canonical)])
    run.emit("page_written", page_event(page))
    remove_staged_upload(ctx, payload.to_json())
    _enqueue_ingest(run, brain.id, slug)


# --- source.ingest ----------------------------------------------------------------------


def ingest(run: JobRun) -> None:
    """Let the ingest model write the Wiki Pages of a Source, then commit and push them."""
    ctx = run.ctx
    payload = run.payload(SourceIngestPayload)
    brain = run.brain(payload.brain_id)
    clone = run.open_clone(brain)
    root = clone.root
    slug = payload.source_slug
    canonical = _canonical(root, slug)
    language = read_settings(root).language
    chunk_chars = ctx.settings.ingest_part_tokens * CHARS_PER_TOKEN
    if payload.resume and progress_path(root, slug).exists() and read_progress(root, slug) is None:
        raise JobError(
            "The Ingest progress file is invalid. Restore it before resuming this Source."
        )
    progress = document_progress(root, canonical, chunk_chars, _now(run), resume=payload.resume)
    mode = payload.mode
    if mode == "auto":
        mode = (
            "map_reduce"
            if progress.parts_total >= ctx.settings.ingest_map_reduce_min_parts
            else "sequential"
        )
    if progress.mode != mode:
        progress = document_progress(root, canonical, chunk_chars, _now(run), resume=False)
    if mode == "map_reduce":
        ingest_map_reduce(run, payload, brain, clone, canonical, language, progress)
        return
    start_part = progress.parts_done
    if start_part == progress.parts_total:
        set_source_status(ctx, brain.id, slug, SourceStatus.INGESTED)
        return
    model = run.ingest_model()
    if model is None:
        raise JobError(
            "No ingest Provider and model are set. Set them in the settings, then ingest "
            "the Source again."
        )
    set_source_status(ctx, brain.id, slug, SourceStatus.INGESTING)

    def failure_status() -> None:
        saved = read_progress(root, slug)
        status = derived_status(root, slug) if saved and saved.parts_done else SourceStatus.FAILED
        set_source_status(ctx, brain.id, slug, status)

    run.on_failure(failure_status)
    title = read_source_record(root, canonical).title
    initial = progress

    def checkpoint(result: IngestResult) -> None:
        nonlocal progress
        progress = initial.model_copy(
            update={
                "parts_done": result.parts_done,
                "status": "complete" if result.parts_done == result.parts_total else "partial",
                "last_stop": result.stopped,
                "usage": initial.usage + result.usage,
                "completed_usage": initial.completed_usage + result.completed_usage,
                "notes": result.notes,
                "written": result.context_written,
                "updated_at": _now(run),
            }
        )
        write_progress(root, slug, progress)
        run.usage = result.usage
        run.ctx.jobs.update_usage(
            run.job.id,
            result.usage,
            {"source": slug, "parts_done": result.parts_done, "parts_total": result.parts_total},
            worker_id=run.worker_id,
        )
        clone.keep_ingest_checkpoints = True
        clone.finish(f"Ingest: {title} (Part {result.parts_done} of {result.parts_total})", [])

    def usage(used: Usage) -> None:
        run.usage = used

    current_part = start_part + 1

    def event(value: IngestEvent) -> None:
        nonlocal current_part
        if value.kind == "progress":
            current_part = int(value.data["chunk"])
        run.emit_ingest_event(value)

    observed = initial.completed_usage.input_tokens + initial.completed_usage.output_tokens
    try:
        result = ingest_source(
            root,
            canonical,
            model.chat_model,
            model.model_name,
            language,
            payload.max_tokens
            if payload.max_tokens is not None
            else ctx.settings.ingest_max_tokens,
            event,
            index=ctx.search,
            brain_id=brain.id,
            start_part=start_part,
            chunk_chars=chunk_chars,
            on_part_done=checkpoint,
            should_stop=run.stop_requested,
            on_usage=usage,
            previous_notes=initial.notes,
            previous_written=initial.written,
            estimated_part_tokens=observed / start_part if start_part and observed else None,
        )
        if result.stopped in (IngestStop.REFUSED, IngestStop.NO_SUMMARY):
            raise JobError(
                f"Ingest Part {result.parts_done + 1} failed ({result.stopped}); "
                "its writes were discarded."
            )
    except Exception as exc:
        if progress.parts_done:
            try:
                progress = progress.model_copy(
                    update={
                        "usage": initial.usage + (run.usage or Usage()),
                        "updated_at": _now(run),
                    }
                )
                write_progress(root, slug, progress)
                detail = (
                    f"Parts {progress.parts_done} of {progress.parts_total} completed; run failed"
                )
                clone.finish(
                    f"Ingest: {title} (run failed)",
                    [log_entry(LogKind.INGEST, title, canonical, detail)],
                )
            except Exception:
                _log.warning("Could not publish the failed Ingest run log for %s.", slug)
        raise JobError(
            f"Ingest Part {current_part} failed: {exc}. "
            "Its uncommitted writes were discarded; completed checkpoints were kept."
        ) from None
    # One log entry per run, after all completed Part checkpoints were pushed.
    progress = progress.model_copy(update={"last_stop": result.stopped})
    write_progress(root, slug, progress)
    detail = (
        f"Parts {start_part + 1}\u2013{result.parts_done} of {result.parts_total}, "
        f"{_pages(len(result.written))} updated"
    )
    if result.stopped is not IngestStop.COMPLETED:
        detail += f", stopped early ({result.stopped})"
    clone.finish(
        f"Ingest: {title} (run complete)", [log_entry(LogKind.INGEST, title, canonical, detail)]
    )
    set_source_status(ctx, brain.id, slug, derived_status(root, slug))
    if result.stopped is IngestStop.USER_REQUESTED:
        if result.parts_done < result.parts_total:
            set_source_status(ctx, brain.id, slug, SourceStatus.PARTLY_INGESTED)
        run.stop_message = (
            f"Stopped by the user after Part {result.parts_done} of {result.parts_total}"
        )


# --- source.reconvert -------------------------------------------------------------------


def reconvert(run: JobRun) -> None:
    """Convert a Source again and overwrite its Canonical Document and Assets.

    The ``x-sbb-source`` facts that do not come from Conversion (the original
    filename, when it was added) and the other frontmatter keys stay.
    """
    ctx = run.ctx
    payload = run.payload(SourceJobPayload)
    brain = run.brain(payload.brain_id)
    clone = run.open_clone(brain)
    root = clone.root
    slug = payload.source_slug
    canonical = _canonical(root, slug)
    old = read_page(root, canonical)
    frontmatter = old.frontmatter
    source_rel = source_file(canonical, frontmatter)
    if frontmatter is None or source_rel is None or not _absolute(root, source_rel).is_file():
        raise JobError(f"The Canonical Document of {slug} does not name its Source file.")
    set_source_status(ctx, brain.id, slug, SourceStatus.CONVERTING)
    run.on_failure(lambda: set_source_status(ctx, brain.id, slug, SourceStatus.FAILED))
    if lfs_available():
        clone.repo.lfs_pull([source_rel])
    source_abs = _absolute(root, source_rel)
    if _is_lfs_pointer(source_abs):
        raise JobError("The Source file is not in Git LFS on GitHub, so it cannot be converted.")
    assets_abs = _absolute(root, canonical.parent / ASSETS_DIR_NAME)
    if assets_abs.is_symlink():
        assets_abs.unlink()
    elif assets_abs.exists():
        shutil.rmtree(assets_abs)

    media_type = detect_media_type(source_abs)
    fmt = SUPPORTED_FORMATS.get(media_type)
    if fmt is None:
        raise UnsupportedFormatError(media_type)
    run.progress("convert", source=slug)
    result = convert_source(source_abs, assets_abs, run.conversion_options(media_type))
    _report_warnings(run, slug, result)
    facts = source_facts(frontmatter)
    original = facts.original_filename or source_rel.name
    now = _now(run)
    updated = facts.model_copy(
        update={
            "original_filename": original,
            "sha256": sha256_file(source_abs),
            "media_type": media_type,
            "size_bytes": source_abs.stat().st_size,
            "converter": result.converter,
            "added_at": facts.added_at if facts.added_at is not None else now,
        }
    )
    title = _title(result.metadata.title, original)
    page = Page(
        path=canonical,
        frontmatter=canonical_frontmatter(
            title=title,
            description=describe_source(fmt.extension, result.metadata),
            resource=frontmatter.resource or f"./{source_rel.name}",
            facts=updated,
            generated_at=now,
            tags=frontmatter.tags,
            extensions=frontmatter.extensions,
        ),
        body=result.markdown,
    )
    old_digest = hashlib.sha256((root / canonical).read_bytes()).hexdigest()
    write_page(root, page)
    unchanged = hashlib.sha256((root / canonical).read_bytes()).hexdigest() == old_digest
    if not unchanged:
        progress_path(root, slug).unlink(missing_ok=True)
    shutil.rmtree(map_directory(run, brain.id, slug), ignore_errors=True)
    clone.finish(f"Reconvert source: {title}", [log_entry(LogKind.RECONVERT, title, canonical)])
    run.emit("page_written", page_event(page))
    set_source_status(
        ctx, brain.id, slug, derived_status(root, slug) if unchanged else SourceStatus.CONVERTED
    )


# --- source.move ------------------------------------------------------------------------


def move(run: JobRun) -> None:
    """Move a Source directory to another Folder and rewrite every link to it.

    Links in page bodies, ``sources[].resource``, ``resource``, and ``log.md`` point
    at the new paths afterwards. The Source id (its slug) does not change.
    """
    payload = run.payload(SourceMovePayload)
    brain = run.brain(payload.brain_id)
    clone = run.open_clone(brain)
    root = clone.root
    slug = payload.source_slug
    canonical = _canonical(root, slug)
    old_dir = canonical.parent
    old_folder = folder_of(canonical)
    new_folder = _folder(payload.folder, root)
    new_dir = BrainLayout().source_dir(new_folder, slug)
    if new_dir == old_dir:
        clone.finish("Move source", [])
        return
    if old_dir in new_dir.parents:
        raise JobError("A Source cannot move into a Folder inside its own directory.")
    if _absolute(root, new_dir).exists() or _absolute(root, new_dir).is_symlink():
        raise JobError(f"{new_dir.as_posix()} exists already. Choose another Folder.")

    pages = read_pages(root)
    moves = {old_dir: new_dir}
    target = _absolute(root, new_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    _absolute(root, old_dir).rename(target)
    remove_empty_dirs(root, old_dir.parent, SOURCES_DIR)

    written: list[Page] = []
    for page in pages:
        new_path = apply_moves(page.path, moves)
        moved = _moved_page(page, new_path, moves)
        if moved != page.model_copy(update={"path": new_path}):
            write_page(root, moved)
        if (
            new_path != page.path
            or moved.body != page.body
            or moved.frontmatter != page.frontmatter
        ):
            written.append(moved)
    log_text = clone.read_log()
    if log_text:
        clone.write_log(rewrite_links(log_text, moves, from_path=LOG_PATH))

    saved = read_progress(root, slug)
    if saved is not None:
        saved = saved.model_copy(
            update={
                "canonical_sha256": hashlib.sha256(
                    (root / apply_moves(canonical, moves)).read_bytes()
                ).hexdigest()
            }
        )
        write_progress(root, slug, saved)
    title = read_source_record(root, apply_moves(canonical, moves)).title
    detail = f"from {_folder_name(old_folder)} to {_folder_name(new_folder)}"
    clone.finish(
        f"Move source: {title}",
        [log_entry(LogKind.MOVE, title, apply_moves(canonical, moves), detail)],
    )
    for page in pages:
        if page.path == canonical:
            run.emit("page_deleted", page_ref(page))
    for page in written:
        run.emit("page_written", page_event(page))


# --- source.delete ----------------------------------------------------------------------


def delete(run: JobRun) -> None:
    """Remove a Source, its Canonical Document and Assets, and its citations.

    Wiki Pages that cite only this Source are deleted with its summary, and links
    to deleted files become plain text (see ``ingest.remove_source``). A Source that
    is gone already only loses its row.
    """
    ctx = run.ctx
    payload = run.payload(SourceJobPayload)
    brain = run.brain(payload.brain_id)
    clone = run.open_clone(brain)
    root = clone.root
    slug = payload.source_slug
    canonical = canonical_documents(root).get(slug)
    title = read_source_record(root, canonical).title if canonical is not None else slug
    canonical_page = _read_optional(root, canonical)
    progress_path(root, slug).unlink(missing_ok=True)
    shutil.rmtree(map_directory(run, brain.id, slug), ignore_errors=True)

    removed_files: list[PurePosixPath] = []
    result = remove_source(root, slug, run.emit_ingest_event)
    if canonical is not None:
        source_dir = _absolute(root, canonical.parent)
        removed_files = [
            PurePosixPath(path.relative_to(root).as_posix())
            for path in source_dir.rglob("*")
            if path.is_file()
        ]
        shutil.rmtree(source_dir)
        remove_empty_dirs(root, canonical.parent.parent, SOURCES_DIR)
    log_text = clone.read_log()
    gone = [*removed_files, *result.deleted]
    if log_text and gone:
        clone.write_log(remove_links(log_text, gone, from_path=LOG_PATH))

    detail = f"{_pages(len(result.deleted))} deleted, {_pages(len(result.written))} updated"
    clone.finish(f"Delete source: {title}", [log_entry(LogKind.DELETE, title, None, detail)])
    if canonical_page is not None:
        run.emit("page_deleted", page_ref(canonical_page))
    delete_source_row(ctx, brain.id, slug)


# --- helpers ----------------------------------------------------------------------------


def _enqueue_ingest(run: JobRun, brain_id: str, slug: str) -> None:
    """Enqueue ``source.ingest`` when an ingest Provider and model are set."""
    if run.stop_requested():
        run.stop_message = "Stopped by the user after saving the converted Source"
        return
    with run.ctx.read_sessions() as session:
        if not ingest_configured(session):
            return
    if (run.ctx.clone_path(brain_id) / summary_path(slug)).is_file():
        return
    try:
        job = enqueue_ingest(run.ctx, brain_id, slug)
    except (ConflictError, NotConfiguredError) as exc:
        _log.info("Did not enqueue the Ingest of %s: %s", slug, exc.message)
        return
    run.progress("ingest_queued", source=slug, job_id=job.id)


def _source_with_sha256(root: Path, sha256: str) -> str | None:
    """The id of a Source in the clone whose Canonical Document has this SHA-256."""
    for slug, path in canonical_documents(root).items():
        if read_source_record(root, path).sha256 == sha256:
            return slug
    return None


def _new_slug(run: JobRun, brain: BrainRow, root: Path, folder: PurePosixPath, stem: str) -> str:
    """A slug from the filename that no Source of the Brain has, in any Folder."""
    taken = set(canonical_documents(root)) | source_row_slugs(run.ctx, brain.id)
    layout = BrainLayout()
    while True:
        slug = unique_slug(slugify(stem), taken)
        directory = _absolute(root, layout.source_dir(folder, slug))
        if not directory.exists() and not directory.is_symlink():
            return slug
        taken.add(slug)


def _canonical(root: Path, slug: str) -> PurePosixPath:
    canonical = canonical_documents(root).get(slug)
    if canonical is None:
        raise JobError(f"Source {slug!r} is not in the Brain. Sync the Brain, then try again.")
    return canonical


def _folder(text: str, root: Path) -> PurePosixPath:
    """The Folder of a payload. It must be a slug path, and no part of it a Source directory."""
    folder = folder_path(text)
    if not all(is_slug(part) for part in folder.parts):
        raise JobError(f"Not a valid Folder: {text!r}.")
    directory = SOURCES_DIR
    for part in folder.parts:
        directory = directory / part
        if _absolute(root, directory / f"{part}.md").is_file():
            raise JobError(f"{folder_text(folder)} is in the directory of a Source, not a Folder.")
    return folder


def _folder_name(folder: PurePosixPath) -> str:
    return folder_text(folder) or "sources"


def _moved_page(
    page: Page, new_path: PurePosixPath, moves: dict[PurePosixPath, PurePosixPath]
) -> Page:
    """The page at its new path, with its links and resources pointing at the moved files."""
    body = rewrite_links(page.body, moves, from_path=page.path)
    frontmatter = page.frontmatter
    if frontmatter is not None:
        sources = [
            ref.model_copy(
                update={"resource": _moved_reference(ref.resource, page.path, new_path, moves)}
            )
            for ref in frontmatter.sources
        ]
        resource = frontmatter.resource
        if resource:
            resource = _moved_reference(resource, page.path, new_path, moves)
        frontmatter = frontmatter.model_copy(update={"sources": sources, "resource": resource})
    return Page(path=new_path, frontmatter=frontmatter, body=body)


def _moved_reference(
    value: str,
    old_path: PurePosixPath,
    new_path: PurePosixPath,
    moves: dict[PurePosixPath, PurePosixPath],
) -> str:
    """A path in frontmatter, changed to point at the moved file; else unchanged."""
    if is_external(value) or any(char.isspace() for char in value) or "#" in value or "?" in value:
        return value
    target = resolve_link(old_path, value)
    if target is None:
        return value
    new_target = apply_moves(target, moves)
    if value.startswith("/"):
        return value if new_target == target else bundle_href(new_target)
    if resolve_link(new_path, value) == new_target:
        return value
    return bundle_href(new_target)


def _read_optional(root: Path, path: PurePosixPath | None) -> Page | None:
    if path is None:
        return None
    try:
        return read_page(root, path)
    except (OSError, ValueError):
        return None


def _title(metadata_title: str | None, original_filename: str) -> str:
    title = " ".join((metadata_title or "").split())
    return title or " ".join(PurePosixPath(original_filename).stem.split()) or original_filename


def _now(run: JobRun) -> datetime:
    return run.ctx.clock().replace(microsecond=0)


def _pages(count: int) -> str:
    return f"{count} page" if count == 1 else f"{count} pages"


def _report_warnings(run: JobRun, slug: str, result: ConversionResult) -> None:
    warnings = [scrub(warning, run.secrets) for warning in result.warnings]
    for warning in warnings:
        _log.info("Conversion of %s: %s", slug, warning)
    if warnings:
        run.progress("converted", source=slug, warnings=warnings)


def _absolute(root: Path, path: PurePosixPath) -> Path:
    return root.joinpath(*path.parts)


def _is_lfs_pointer(path: Path) -> bool:
    try:
        if path.stat().st_size > _LFS_POINTER_MAX_BYTES:
            return False
        with path.open("rb") as file:
            return file.read(len(_LFS_POINTER_START)) == _LFS_POINTER_START
    except OSError:
        return False
