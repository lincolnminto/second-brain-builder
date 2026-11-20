"""Sources and Folders: list, get, add (upload), original file, move, delete, reconvert, ingest.

Every change is a job. ``add_source`` stages the upload and checks it first: size,
supported format, duplicates in the Brain, and the Git LFS quota.
"""

from __future__ import annotations

import math
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from second_brain_builder.brains import BrainPathError, read_pages, resolve_in_brain
from second_brain_builder.conversion import detect_media_type
from second_brain_builder.conversion.formats import SUPPORTED_FORMATS
from second_brain_builder.domain import JobKind, JobStatus
from second_brain_builder.ingest._map_reduce_plan import related_pages, resolve_targets
from second_brain_builder.ingest._map_reduce_writer import estimate_group_tokens
from second_brain_builder.ingest.chunks import CHARS_PER_TOKEN
from second_brain_builder.ingest.loop import MAX_OUTPUT_TOKENS, _load_source, _tokens
from second_brain_builder.ingest.progress import document_progress
from second_brain_builder.okf import OKFError, read_page
from second_brain_builder.services._common import (
    brain_row,
    folder_text,
    job_view,
    load_brain,
    normalize_folder,
    source_view,
)
from second_brain_builder.services._files import local_file
from second_brain_builder.services.brains import owner_lfs_bytes, repo_owner
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import (
    ConflictError,
    DuplicateSourceError,
    InvalidInputError,
    NotConfiguredError,
    NotFoundError,
    QuotaExceededError,
    UploadTooLargeError,
)
from second_brain_builder.services.models import (
    AddSourceResult,
    FolderNode,
    IngestEstimate,
    JobView,
    LocalFile,
    PageRef,
    SourceDetail,
    SourceView,
)
from second_brain_builder.services.payloads import (
    SourceAddPayload,
    SourceIngestPayload,
    SourceJobPayload,
    SourceMovePayload,
)
from second_brain_builder.services.providers import ingest_configured
from second_brain_builder.services.uploads import (
    Base64Stream,
    ByteStream,
    base64_min_decoded_size,
    clean_filename,
    stage_upload,
)
from second_brain_builder.store import Job, JobStateError, SourceRow

ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)
SOURCE_METADATA_KEY = "x-sbb-source"


# --- Folders ------------------------------------------------------------------------------


@dataclass
class _Folder:
    name: str
    path: str
    count: int = 0
    children: dict[str, _Folder] = field(default_factory=dict)

    def node(self) -> FolderNode:
        children = [self.children[name].node() for name in sorted(self.children)]
        total = self.count + sum(child.total_source_count for child in children)
        return FolderNode(
            name=self.name,
            path=self.path,
            source_count=self.count,
            total_source_count=total,
            children=children,
        )


def get_folder_tree(ctx: AppContext, brain_id: str) -> FolderNode:
    """The Folder tree of a Brain, from its Sources. The root is the ``sources/`` directory.

    Raises:
        NotFoundError: No such Brain.
    """
    with ctx.read_sessions() as session:
        brain_row(session, brain_id)
        folders = Counter(
            session.scalars(select(SourceRow.folder).where(SourceRow.brain_id == brain_id))
        )
    root = _Folder(name="", path="")
    for folder, count in folders.items():
        node = root
        parts = PurePosixPath(folder).parts if folder else ()
        for index, part in enumerate(parts):
            path = "/".join(parts[: index + 1])
            node = node.children.setdefault(part, _Folder(name=part, path=path))
        node.count += count
    return root.node()


# --- read ------------------------------------------------------------------------------------


def list_sources(
    ctx: AppContext, brain_id: str, folder: str | None = None, *, recursive: bool = True
) -> list[SourceView]:
    """The Sources of a Brain, sorted by Folder and slug.

    With ``folder``, only the Sources in that Folder, and with ``recursive`` also
    those in its subfolders.

    Raises:
        NotFoundError: No such Brain.
        InvalidInputError: The Folder is not valid.
    """
    wanted = None if folder is None else folder_text(normalize_folder(folder))
    with ctx.read_sessions() as session:
        brain_row(session, brain_id)
        rows = session.scalars(
            select(SourceRow)
            .where(SourceRow.brain_id == brain_id)
            .order_by(SourceRow.folder, SourceRow.slug)
        ).all()
    return [
        source_view(row, ctx.clone_path(brain_id))
        for row in rows
        if wanted is None or _in_folder(row.folder, wanted, recursive=recursive)
    ]


def get_source(ctx: AppContext, brain_id: str, slug: str) -> SourceDetail:
    """A Source and the Wiki Pages that cite it.

    Raises:
        NotFoundError: No such Brain or Source.
    """
    with ctx.read_sessions() as session:
        row = _source_row(session, brain_id, slug)
    root = ctx.clone_path(brain_id)
    citing: list[PageRef] = []
    if root.is_dir():
        for page in read_pages(root):
            frontmatter = page.frontmatter
            if frontmatter is None or frontmatter.type == "source":
                continue
            if any(ref.id == row.slug for ref in frontmatter.sources):
                title = " ".join((frontmatter.title or "").split()) or page.path.stem
                citing.append(
                    PageRef(path=page.path.as_posix(), type=frontmatter.type, title=title)
                )
    return SourceDetail(source=source_view(row, root), citing_pages=citing)


def original_file(ctx: AppContext, brain_id: str, slug: str) -> LocalFile:
    """The Source file in the clone, with its original filename for the download.

    Raises:
        NotFoundError: No such Brain or Source, the file is not in the clone yet,
            or its Git LFS content is not downloaded.
    """
    with ctx.read_sessions() as session:
        row = _source_row(session, brain_id, slug)
    if not row.path:
        raise NotFoundError("The Source file is not in the Brain yet.")
    root = ctx.clone_path(brain_id)
    try:
        path = resolve_in_brain(root, row.path)
    except BrainPathError:
        raise NotFoundError("The Source file is not in the Brain.") from None
    view = source_view(row)
    filename = _original_filename(root, view.canonical_path) or path.name
    return local_file(path, filename, row.media_type or "application/octet-stream")


# --- add ---------------------------------------------------------------------------------------


def add_source(
    ctx: AppContext,
    brain_id: str,
    filename: str,
    stream: ByteStream,
    folder: str = "",
    *,
    confirm_over_quota: bool = False,
) -> AddSourceResult:
    """Stage an upload and enqueue ``source.add``.

    The stream goes to ``<data_dir>/uploads/<uuid>/`` while its SHA-256 is
    computed. The upload is removed again when a check fails.

    Raises:
        NotFoundError: No such Brain.
        InvalidInputError: The filename or Folder is not valid, the Folder is
            inside the directory of a Source, the file is empty, or Conversion
            does not support its format.
        UploadTooLargeError: The file is larger than ``SBB_MAX_UPLOAD_BYTES``.
        DuplicateSourceError: The Brain has a Source with the same content, or one
            is being added.
        QuotaExceededError: The upload would take the Git LFS usage of the
            repository owner over ``SBB_LFS_QUOTA_BYTES``, and
            ``confirm_over_quota`` is False.
    """
    load_brain(ctx, brain_id)
    original_filename = clean_filename(filename)
    target = folder_text(normalize_folder(folder))
    # Checked here too, so that a bad Folder does not wait for a large upload.
    _check_folder_placement(ctx, brain_id, target)
    staged = stage_upload(ctx, original_filename, stream)
    try:
        media_type = detect_media_type(staged.path)
        if media_type not in SUPPORTED_FORMATS:
            raise InvalidInputError(
                f"This file type cannot be converted ({media_type}). Supported types include "
                "PDF, Office documents, EPUB, HTML, Markdown, text, images, audio, and video."
            )
        payload = SourceAddPayload(
            brain_id=brain_id,
            upload_path=str(staged.path.resolve()),
            original_filename=original_filename,
            folder=target,
            sha256=staged.sha256,
        )
        with ctx.add_source_lock:
            job = admit_source_add(
                ctx, payload, staged.size_bytes, confirm_over_quota=confirm_over_quota
            )
    except BaseException:
        shutil.rmtree(staged.directory, ignore_errors=True)
        raise
    return AddSourceResult(
        job=job_view(job),
        sha256=staged.sha256,
        size_bytes=staged.size_bytes,
        media_type=media_type,
    )


def admit_source_add(
    ctx: AppContext, payload: SourceAddPayload, size_bytes: int, *, confirm_over_quota: bool
) -> Job:
    """Check a staged upload against the Brain, then enqueue ``source.add``.

    ``add_source`` and ``retry_job`` share it. The caller holds
    ``ctx.add_source_lock``, so that two uploads of the same file cannot both pass
    the duplicate check. The upload is not removed on an error.

    Raises:
        NotFoundError: No such Brain.
        InvalidInputError: The Folder is inside the directory of a Source.
        DuplicateSourceError: The Brain has a Source with the same content, or one
            is being added.
        QuotaExceededError: The upload would take the Git LFS usage of the
            repository owner over the quota, and ``confirm_over_quota`` is False.
    """
    brain_id = payload.brain_id
    brain = load_brain(ctx, brain_id)
    _check_folder_placement(ctx, brain_id, payload.folder)
    _check_duplicate(ctx, brain_id, payload.sha256)
    if not confirm_over_quota:
        used = owner_lfs_bytes(ctx, repo_owner(brain.repo_full_name))
        quota = ctx.settings.lfs_quota_bytes
        if used + size_bytes > quota:
            raise QuotaExceededError(
                "This upload takes the Git LFS storage of the repository owner over "
                f"the quota ({used + size_bytes} of {quota} bytes). "
                "Confirm to add it anyway.",
                used_bytes=used,
                upload_bytes=size_bytes,
                quota_bytes=quota,
            )
    return ctx.jobs.enqueue(JobKind.SOURCE_ADD, brain_id, payload.to_json())


def add_source_base64(
    ctx: AppContext,
    brain_id: str,
    filename: str,
    content_base64: str,
    folder: str = "",
    *,
    confirm_over_quota: bool = False,
) -> AddSourceResult:
    """``add_source`` for content in base64, as the MCP tool ``add_source`` gets it.

    Spaces, tabs, and line breaks in the base64 text are ignored. Text whose
    decoded size is over the upload limit is refused before anything is copied
    or decoded. Other text is decoded piece by piece while it is staged, so the
    decoded file is never in memory as a whole.

    Raises:
        InvalidInputError: The content is not valid base64. Also every error of
            ``add_source``.
    """
    limit = ctx.settings.max_upload_bytes
    if base64_min_decoded_size(content_base64) > limit:
        raise UploadTooLargeError(
            f"The file is larger than the upload limit of {limit} bytes.", max_bytes=limit
        )
    return add_source(
        ctx,
        brain_id,
        filename,
        Base64Stream(content_base64),
        folder,
        confirm_over_quota=confirm_over_quota,
    )


# --- jobs on one Source ---------------------------------------------------------------------------


def move_source(ctx: AppContext, brain_id: str, slug: str, folder: str) -> JobView:
    """Enqueue ``source.move`` to another Folder. The slug (the Source id) stays.

    Raises:
        NotFoundError: No such Brain or Source.
        InvalidInputError: The Folder is not valid, the Source is in it already,
            the Folder is inside the directory of another Source, or the new
            directory of the Source would hold other Sources.
        ConflictError: The Source has a pending move or delete.
    """
    with ctx.add_source_lock:
        target = folder_text(normalize_folder(folder))
        with ctx.read_sessions() as session:
            row = _source_row(session, brain_id, slug)
        if row.folder == target:
            raise InvalidInputError("The Source is in this Folder already.")
        _check_no_pending(ctx, brain_id, row.slug, JobKind.SOURCE_MOVE)
        _check_folder_placement(ctx, brain_id, target, moving=row.slug)
        payload = SourceMovePayload(brain_id=brain_id, source_slug=row.slug, folder=target)
        return job_view(ctx.jobs.enqueue(JobKind.SOURCE_MOVE, brain_id, payload.to_json()))


def delete_source(ctx: AppContext, brain_id: str, slug: str) -> JobView:
    """Enqueue ``source.delete``: the Source, its Canonical Document, and its citations go.

    Raises:
        NotFoundError: No such Brain or Source.
        ConflictError: The Source has a pending delete.
    """
    return _source_job(ctx, brain_id, slug, JobKind.SOURCE_DELETE)


def reconvert_source(ctx: AppContext, brain_id: str, slug: str) -> JobView:
    """Enqueue ``source.reconvert``.

    Raises:
        NotFoundError: No such Brain or Source.
        ConflictError: The Source has a pending reconvert or delete.
    """
    return _source_job(ctx, brain_id, slug, JobKind.SOURCE_RECONVERT)


def ingest_source(
    ctx: AppContext,
    brain_id: str,
    slug: str,
    *,
    resume: bool = True,
    max_tokens: int | None = None,
    mode: Literal["auto", "sequential", "map_reduce"] = "auto",
) -> JobView:
    """Enqueue ``source.ingest``.

    Raises:
        NotFoundError: No such Brain or Source.
        NotConfiguredError: No ingest Provider and model are set.
        ConflictError: The Source has a pending Ingest or delete.
    """
    with ctx.add_source_lock:
        with ctx.read_sessions() as session:
            configured = ingest_configured(session)
        if not configured:
            raise NotConfiguredError("Set an ingest Provider and model before you ingest a Source.")
        if max_tokens is not None and max_tokens < 1:
            raise InvalidInputError("max_tokens must be positive.")
        if mode not in ("auto", "sequential", "map_reduce"):
            raise InvalidInputError("Invalid Ingest mode.")
        with ctx.read_sessions() as session:
            row = _source_row(session, brain_id, slug)
        _check_no_pending(ctx, brain_id, row.slug, JobKind.SOURCE_INGEST)
        payload = SourceIngestPayload(
            brain_id=brain_id,
            source_slug=row.slug,
            resume=resume,
            mode=mode,
            max_tokens=max_tokens if max_tokens is not None else ctx.settings.ingest_max_tokens,
        )
        return job_view(ctx.jobs.enqueue(JobKind.SOURCE_INGEST, brain_id, payload.to_json()))


def ingest_estimate(
    ctx: AppContext, brain_id: str, slug: str, *, resume: bool = True
) -> IngestEstimate:
    """Estimate remaining Parts using observed tokens per completed Part when available."""
    with ctx.read_sessions() as session:
        row = _source_row(session, brain_id, slug)
    root = ctx.clone_path(brain_id)
    canonical = source_view(row).canonical_path
    try:
        path = resolve_in_brain(root, canonical)
        if not path.is_file():
            raise NotFoundError("The Canonical Document is not in the Brain.")
        progress = document_progress(
            root,
            PurePosixPath(canonical),
            ctx.settings.ingest_part_tokens * CHARS_PER_TOKEN,
            ctx.clock(),
            resume=resume,
        )
    except BrainPathError:
        raise NotFoundError("The Canonical Document is not in the Brain.") from None
    if progress.mode == "map_reduce" or (
        progress.parts_done == 0
        and progress.parts_total >= ctx.settings.ingest_map_reduce_min_parts
    ):
        state = progress.map_reduce_state
        maps_done = progress.map_parts_done
        groups_done = progress.reduce_groups_done
        groups_total = progress.reduce_groups_total
        map_spent = state.map_usage.input_tokens + state.map_usage.output_tokens if state else 0
        map_per_part = (
            map_spent / maps_done if maps_done and map_spent else ctx.settings.ingest_part_tokens
        )
        map_tokens = math.ceil(map_per_part * (progress.parts_total - maps_done))
        if progress.phase == "complete":
            reduce_tokens = 0
        elif state is not None and state.phase == "reduce":
            resolve_targets(root, state)
            groups_done, groups_total = state.reduce_groups_done, state.reduce_groups_total
            eligible = {
                number
                for number, group in enumerate(state.groups)
                if len(group.facts) >= ctx.settings.ingest_min_facts_per_page
                or len({fact.part_number for fact in group.facts})
                >= ctx.settings.ingest_min_parts_per_page
            }
            remaining = eligible.intersection(range(groups_done, groups_total)) | {
                failure.group_index - 1 for failure in state.reduce_failed_groups
            }
            spent = _tokens(state.reduce_written_usage)
            if state.reduce_groups_written and spent:
                per_group = spent / state.reduce_groups_written
                reduce_tokens = math.ceil(per_group * (len(remaining) + (not state.summary_done)))
            else:
                source, _ = _load_source(root, PurePosixPath(canonical))
                related = related_pages(state, eligible)
                language = load_brain(ctx, brain_id).language
                reduce_tokens = math.ceil(
                    sum(
                        estimate_group_tokens(
                            root=root,
                            source=source,
                            group=state.groups[number],
                            target=state.reduce_targets[number],
                            related_pages=related[number],
                            language=language,
                        )
                        for number in remaining
                    )
                    + (MAX_OUTPUT_TOKENS if not state.summary_done else 0)
                )
        else:
            # Until grouping finishes, allow a writer pass over the extracted material.
            reduce_tokens = math.ceil(map_per_part * progress.parts_total * 2)
        return IngestEstimate(
            parts_total=progress.parts_total,
            parts_done=maps_done,
            mode="map_reduce",
            map_parts_done=maps_done,
            reduce_groups_done=groups_done,
            reduce_groups_total=groups_total,
            map_estimated_tokens=map_tokens,
            reduce_estimated_tokens=reduce_tokens,
            estimated_tokens=map_tokens + reduce_tokens,
        )
    tokens = progress.completed_usage.input_tokens + progress.completed_usage.output_tokens
    per_part = (
        tokens / progress.parts_done
        if progress.parts_done and tokens
        else ctx.settings.ingest_part_tokens * 6
    )
    if progress.recent_part_tokens:
        per_part = max(
            per_part, sum(progress.recent_part_tokens) / len(progress.recent_part_tokens)
        )
    return IngestEstimate(
        parts_total=progress.parts_total,
        parts_done=progress.parts_done,
        estimated_tokens=math.ceil(per_part * (progress.parts_total - progress.parts_done)),
    )


# --- helpers ----------------------------------------------------------------------------------


def _source_job(ctx: AppContext, brain_id: str, slug: str, kind: JobKind) -> JobView:
    with ctx.add_source_lock:
        with ctx.read_sessions() as session:
            row = _source_row(session, brain_id, slug)
        if kind is JobKind.SOURCE_DELETE:
            pending = [
                job
                for job in ctx.jobs.list_jobs(brain_id, statuses=ACTIVE_STATUSES, limit=10_000)
                if job.payload.get("source_slug") == row.slug
            ]
            if any(job.status is JobStatus.RUNNING for job in pending):
                raise ConflictError("The Source has a running job; wait before deleting it.")
            for job in pending:
                try:
                    ctx.jobs.cancel(job.id)
                except JobStateError:
                    raise ConflictError("A Source job started; wait before deleting it.") from None
        else:
            _check_no_pending(ctx, brain_id, row.slug, kind)
        payload = SourceJobPayload(brain_id=brain_id, source_slug=row.slug)
        return job_view(ctx.jobs.enqueue(kind, brain_id, payload.to_json()))


def _source_row(session: Session, brain_id: str, slug: str) -> SourceRow:
    brain_row(session, brain_id)
    row = session.scalars(
        select(SourceRow).where(SourceRow.brain_id == brain_id, SourceRow.slug == slug)
    ).first()
    if row is None:
        raise NotFoundError(f"Source {slug!r} does not exist in this Brain.")
    return row


def _check_duplicate(ctx: AppContext, brain_id: str, sha256: str) -> None:
    with ctx.read_sessions() as session:
        existing = session.scalars(
            select(SourceRow)
            .where(SourceRow.brain_id == brain_id, SourceRow.sha256 == sha256)
            .order_by(SourceRow.id)
        ).first()
    if existing is not None:
        view = source_view(existing)
        raise DuplicateSourceError(
            f"The Brain has this file already: {view.title or view.slug}.", existing=view
        )
    for job in ctx.jobs.list_jobs(brain_id, statuses=ACTIVE_STATUSES, limit=10_000):
        if job.kind is JobKind.SOURCE_ADD and job.payload.get("sha256") == sha256:
            raise DuplicateSourceError(
                f"This file is being added to the Brain already (job {job.id}).",
                pending_job=job_view(job),
            )


def _check_folder_placement(
    ctx: AppContext, brain_id: str, folder: str, *, moving: str | None = None
) -> None:
    """Refuse a Folder that would put one Source directory inside another.

    A Source lives in ``sources/<folder>/<slug>/``, and ``source.delete`` and
    ``source.move`` act on that whole directory. So no part of a Folder can be
    the directory of a Source, and the new directory of a moving Source cannot be
    a Folder that holds other Sources. Pending moves and uploads count too.

    Raises:
        InvalidInputError: The Folder breaks one of these rules.
    """
    with ctx.read_sessions() as session:
        rows = session.execute(
            select(SourceRow.slug, SourceRow.folder).where(SourceRow.brain_id == brain_id)
        ).all()
    # The directory (from sources/) of each other Source, now and after a pending
    # move, and the Folders that hold other Sources, now or after a pending job.
    source_dirs: dict[str, str] = {}
    used_folders: set[str] = set()
    for slug, row_folder in rows:
        if slug != moving:
            source_dirs[_join(row_folder, slug)] = slug
            used_folders.add(row_folder)
    for job in ctx.jobs.list_jobs(brain_id, statuses=ACTIVE_STATUSES, limit=10_000):
        job_folder = job.payload.get("folder")
        if not isinstance(job_folder, str):
            continue
        job_slug = job.payload.get("source_slug")
        if job.kind is JobKind.SOURCE_MOVE and isinstance(job_slug, str) and job_slug != moving:
            source_dirs[_join(job_folder, job_slug)] = job_slug
            used_folders.add(job_folder)
        elif job.kind is JobKind.SOURCE_ADD:
            used_folders.add(job_folder)

    parts = PurePosixPath(folder).parts if folder else ()
    for depth in range(1, len(parts) + 1):
        owner = source_dirs.get("/".join(parts[:depth]))
        if owner is not None:
            raise InvalidInputError(
                f"The Folder {folder!r} is inside the directory of Source {owner!r}. "
                "Choose another Folder."
            )
    if moving is not None:
        new_dir = _join(folder, moving)
        if any(used == new_dir or used.startswith(f"{new_dir}/") for used in used_folders):
            raise InvalidInputError(
                f"Source {moving!r} cannot move there: its directory would be the Folder "
                f"{new_dir!r}, which holds other Sources. Choose another Folder."
            )


def _join(folder: str, slug: str) -> str:
    return f"{folder}/{slug}" if folder else slug


def _check_no_pending(ctx: AppContext, brain_id: str, slug: str, kind: JobKind) -> None:
    for job in ctx.jobs.list_jobs(brain_id, statuses=ACTIVE_STATUSES, limit=10_000):
        if job.payload.get("source_slug") != slug:
            continue
        if job.kind is JobKind.SOURCE_DELETE:
            raise ConflictError(f"The Source is being deleted (job {job.id}).")
        if job.kind in {JobKind.SOURCE_INGEST, JobKind.SOURCE_RECONVERT, JobKind.SOURCE_MOVE}:
            raise ConflictError(f"A {kind} job for this Source is pending already (job {job.id}).")


def _in_folder(folder: str, wanted: str, *, recursive: bool) -> bool:
    if folder == wanted:
        return True
    if not recursive:
        return False
    return wanted == "" or folder.startswith(f"{wanted}/")


def _original_filename(root: Path, canonical_path: str) -> str | None:
    try:
        page = read_page(root, PurePosixPath(canonical_path))
    except (OSError, ValueError, OKFError):
        return None
    if page.frontmatter is None:
        return None
    metadata: Any = page.frontmatter.extensions.get(SOURCE_METADATA_KEY)
    if not isinstance(metadata, dict):
        return None
    name = metadata.get("original_filename")
    return name if isinstance(name, str) and name.strip() else None
