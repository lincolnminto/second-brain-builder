"""Helpers that several use cases share: row lookups, views, settings, and validation."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from second_brain_builder.brains import SOURCES_DIR, is_slug, slugify
from second_brain_builder.ingest.progress import read_progress
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import InvalidInputError, NotFoundError
from second_brain_builder.services.models import BrainView, JobEventView, JobView, SourceView
from second_brain_builder.store import BrainRow, Job, JobEvent, SettingRow, SourceRow

GITHUB_URL = "https://github.com"
MAX_FOLDER_DEPTH = 8
MAX_FOLDER_LENGTH = 512
MAX_NAME_LENGTH = 200


# --- rows and views ------------------------------------------------------------------


def brain_row(session: Session, brain_id: str) -> BrainRow:
    """The Brain row, or ``NotFoundError``."""
    row = session.get(BrainRow, brain_id)
    if row is None:
        raise NotFoundError(f"Brain {brain_id!r} does not exist.")
    return row


def load_brain(ctx: AppContext, brain_id: str) -> BrainRow:
    """Read the Brain row in its own read transaction, or raise ``NotFoundError``."""
    with ctx.read_sessions() as session:
        return brain_row(session, brain_id)


def source_counts(session: Session) -> dict[str, int]:
    """Number of Sources per Brain id."""
    rows = session.execute(
        select(SourceRow.brain_id, func.count(SourceRow.id)).group_by(SourceRow.brain_id)
    ).all()
    return {brain_id: count for brain_id, count in rows}


def brain_slug(repo_full_name: str) -> str:
    """The slug of a Brain: from its repository name, for example ``shore-notes``."""
    return slugify(repo_full_name.rpartition("/")[2])


def brain_view(row: BrainRow, source_count: int) -> BrainView:
    return BrainView(
        id=row.id,
        name=row.name,
        slug=brain_slug(row.repo_full_name),
        repo_full_name=row.repo_full_name,
        repo_url=f"{GITHUB_URL}/{row.repo_full_name}",
        language=row.language,
        status=row.status,
        created_at=row.created_at,
        last_synced_at=row.last_synced_at,
        source_count=source_count,
    )


def canonical_path(folder: str, slug: str) -> PurePosixPath:
    """``sources/<folder>/<slug>/<slug>.md``, without the slug checks of ``BrainLayout``.

    A row that ``brain.sync`` read from a repository is shown even if its names
    are not slugs; every path from it is checked again before a file is read.
    """
    parts = PurePosixPath(folder).parts if folder else ()
    return SOURCES_DIR.joinpath(*parts, slug, f"{slug}.md")


def source_view(row: SourceRow, root: Path | None = None) -> SourceView:
    try:
        progress = read_progress(root, row.slug) if root is not None else None
    except (OSError, ValueError):
        progress = None
    return SourceView(
        slug=row.slug,
        brain_id=row.brain_id,
        folder=row.folder,
        path=row.path,
        canonical_path=canonical_path(row.folder, row.slug).as_posix(),
        title=row.title,
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        status=row.status,
        parts_done=progress.parts_done if progress is not None else None,
        parts_total=progress.parts_total if progress is not None else None,
        mode=progress.mode if progress is not None else None,
        phase=progress.phase if progress is not None else None,
        map_parts_done=progress.map_parts_done if progress is not None else None,
        notes_dropped_total=progress.notes_dropped_total if progress is not None else None,
        parts_with_drops=progress.parts_with_drops if progress is not None else None,
        reduce_groups_done=progress.reduce_groups_done if progress is not None else None,
        reduce_groups_total=progress.reduce_groups_total if progress is not None else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def job_view(job: Job) -> JobView:
    return JobView(
        id=job.id,
        brain_id=job.brain_id,
        kind=job.kind,
        payload=job.payload,
        status=job.status,
        attempts=job.attempts,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        input_tokens=job.input_tokens,
        output_tokens=job.output_tokens,
    )


def job_event_view(event: JobEvent) -> JobEventView:
    return JobEventView(
        id=event.id,
        job_id=event.job_id,
        brain_id=event.brain_id,
        created_at=event.created_at,
        kind=event.kind,
        data=event.data,
    )


# --- app settings (the ``settings`` table) ---------------------------------------------


def read_setting(session: Session, key: str) -> Any:
    """The JSON value of an app setting, or ``None``."""
    row = session.get(SettingRow, key)
    return None if row is None else row.value


def write_setting(session: Session, key: str, value: Any) -> None:
    """Set an app setting. ``value`` must be JSON-serializable."""
    session.merge(SettingRow(key=key, value=value))


def delete_setting(session: Session, key: str) -> None:
    row = session.get(SettingRow, key)
    if row is not None:
        session.delete(row)


# --- validation -----------------------------------------------------------------------------


def one_line_name(value: str, what: str, max_length: int = MAX_NAME_LENGTH) -> str:
    """Collapse whitespace; reject an empty or too long name."""
    name = " ".join(value.split())
    if not name:
        raise InvalidInputError(f"The {what} is empty.")
    if len(name) > max_length:
        raise InvalidInputError(f"The {what} is longer than {max_length} characters.")
    return name


def normalize_folder(value: str) -> PurePosixPath:
    """Turn a Folder from a request into a slug path. ``""`` and ``"/"`` are the root.

    Each part becomes a slug (``"Work/Client Notes"`` gives ``work/client-notes``),
    so a Folder name that a person or an agent types works. A part that is ``.``
    or ``..``, a backslash, or a NUL character is rejected: a Folder never leaves
    ``sources/``.

    Raises:
        InvalidInputError: The Folder is not valid.
    """
    if "\x00" in value or "\\" in value:
        raise InvalidInputError("A Folder cannot contain a backslash or a NUL character.")
    parts = [part.strip() for part in value.split("/")]
    parts = [part for part in parts if part]
    if any(part in {".", ".."} or part.startswith(".") for part in parts):
        raise InvalidInputError(f"Not a valid Folder: {value!r}. Folders cannot have dot parts.")
    slugs = [part if is_slug(part) else slugify(part) for part in parts]
    if len(slugs) > MAX_FOLDER_DEPTH:
        raise InvalidInputError(f"A Folder can have at most {MAX_FOLDER_DEPTH} levels.")
    folder = PurePosixPath(*slugs) if slugs else PurePosixPath()
    if len(folder.as_posix()) > MAX_FOLDER_LENGTH:
        raise InvalidInputError(f"A Folder path can have at most {MAX_FOLDER_LENGTH} characters.")
    return folder


def folder_text(folder: PurePosixPath) -> str:
    """The stored form of a Folder: ``""`` for the root, else ``a/b``."""
    return "" if folder == PurePosixPath() else folder.as_posix()


def path_text(path: PurePosixPath | Path) -> str:
    return PurePosixPath(path).as_posix()
