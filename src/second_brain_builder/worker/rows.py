"""The rows that the worker keeps current: the ``sources`` cache and the Brain status.

The ``sources`` rows cache what the Canonical Documents of a clone say, so the
worker can rebuild them at any time. Each write runs in its own short transaction,
after the slow file and git work.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from sqlalchemy import delete, select

from second_brain_builder.domain import BrainStatus, SourceStatus
from second_brain_builder.ingest.progress import progress_path, read_progress
from second_brain_builder.services import AppContext, NotFoundError
from second_brain_builder.store import BrainRow, SourceRow
from second_brain_builder.worker.canonical import (
    SourceRecord,
    canonical_documents,
    read_source_record,
    summary_path,
)


def load_brain(ctx: AppContext, brain_id: str) -> BrainRow:
    """The Brain row, read in its own transaction.

    Raises:
        NotFoundError: The Brain was disconnected.
    """
    with ctx.read_sessions() as session:
        row = session.get(BrainRow, brain_id)
        if row is None:
            raise NotFoundError(f"Brain {brain_id} does not exist. It was disconnected.")
        return row


def set_brain_status(ctx: AppContext, brain_id: str, status: BrainStatus, *, synced: bool) -> None:
    """Set the Brain status, and with ``synced`` also ``last_synced_at``. Skip a missing Brain."""
    with ctx.sessions.begin() as session:
        row = session.get(BrainRow, brain_id)
        if row is None:
            return
        row.status = status
        if synced:
            row.last_synced_at = ctx.clock()


def derived_status(root: Path, slug: str) -> SourceStatus:
    """Progress determines status; legacy Sources fall back to their summary page."""
    progress = read_progress(root, slug)
    if progress is not None:
        return (
            SourceStatus.INGESTED if progress.status == "complete" else SourceStatus.PARTLY_INGESTED
        )
    if progress_path(root, slug).exists():
        return SourceStatus.CONVERTED
    return (
        SourceStatus.INGESTED if (root / summary_path(slug)).is_file() else SourceStatus.CONVERTED
    )


def reconcile_sources(
    ctx: AppContext, brain_id: str, root: Path, slugs: Collection[str] | None = None
) -> None:
    """Make the ``sources`` rows of a Brain match the Canonical Documents in its clone.

    With ``slugs``, only the rows of these Source ids change; ``None`` rebuilds every
    row and removes the rows of Sources that the clone does not have. A row keeps the
    status ``failed`` (the last job on the Source failed); any other status becomes
    ``ingested`` or ``converted`` (see ``derived_status``). Jobs of one Brain run one
    at a time, so ``converting`` and ``ingesting`` here are left over from a crash.
    """
    catalog = canonical_documents(root)
    wanted = set(catalog) if slugs is None else set(slugs)
    records: dict[str, SourceRecord] = {
        slug: read_source_record(root, catalog[slug]) for slug in wanted if slug in catalog
    }
    statuses = {slug: derived_status(root, slug) for slug in records}
    now = ctx.clock()
    with ctx.sessions.begin() as session:
        rows = {
            row.slug: row
            for row in session.scalars(select(SourceRow).where(SourceRow.brain_id == brain_id))
        }
        for slug in sorted(wanted):
            record = records.get(slug)
            row = rows.get(slug)
            if record is None:
                if row is not None:
                    session.delete(row)
                continue
            if row is None:
                row = SourceRow(
                    brain_id=brain_id,
                    slug=slug,
                    status=statuses[slug],
                    created_at=record.added_at or now,
                )
                session.add(row)
            elif row.status is not SourceStatus.FAILED:
                row.status = statuses[slug]
            row.folder = record.folder
            row.path = record.path
            row.title = record.title
            row.sha256 = record.sha256
            row.media_type = record.media_type
            row.size_bytes = record.size_bytes
            row.updated_at = now
        if slugs is None:
            for slug, row in rows.items():
                if slug not in catalog:
                    session.delete(row)


def set_source_status(ctx: AppContext, brain_id: str, slug: str, status: SourceStatus) -> None:
    """Set the status of one Source row. A missing row is skipped."""
    with ctx.sessions.begin() as session:
        row = session.scalars(
            select(SourceRow).where(SourceRow.brain_id == brain_id, SourceRow.slug == slug)
        ).first()
        if row is not None:
            row.status = status
            row.updated_at = ctx.clock()


def delete_source_row(ctx: AppContext, brain_id: str, slug: str) -> None:
    """Remove the row of one Source, if there is one."""
    with ctx.sessions.begin() as session:
        session.execute(
            delete(SourceRow).where(SourceRow.brain_id == brain_id, SourceRow.slug == slug)
        )


def source_row_slugs(ctx: AppContext, brain_id: str) -> set[str]:
    """The Source ids that the ``sources`` table has for a Brain."""
    with ctx.read_sessions() as session:
        return set(session.scalars(select(SourceRow.slug).where(SourceRow.brain_id == brain_id)))
