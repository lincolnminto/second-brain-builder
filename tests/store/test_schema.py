import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from second_brain_builder.config import Settings
from second_brain_builder.domain import JobKind, SourceStatus
from second_brain_builder.store import (
    Base,
    BrainRow,
    JobEventRow,
    JobQueue,
    JobRow,
    SourceRow,
    make_engine,
    make_session_factory,
    migrate,
    sqlite_engine,
)
from second_brain_builder.store.migration import alembic_config


def test_migrate_creates_the_schema_of_the_models(settings: Settings) -> None:
    engine = make_engine(settings)
    migrate(engine)

    assert settings.database_path.is_file()
    tables = set(inspect(engine).get_table_names())
    assert tables == set(Base.metadata.tables) | {"alembic_version"}
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert context.get_current_revision() == head
        # The migrations and the ORM models describe the same columns, types,
        # indexes, and constraints.
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()


def test_app_and_worker_can_migrate_at_the_same_time(settings: Settings) -> None:
    # Both containers call migrate() on start. One creates the tables, the other
    # waits for the lock and then finds the database at head.
    engines = [make_engine(settings) for _ in range(2)]
    barrier = threading.Barrier(len(engines))
    errors: list[BaseException] = []

    def run(engine: Engine) -> None:
        barrier.wait()
        try:
            migrate(engine)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=run, args=(engine,)) for engine in engines]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    sessions = make_session_factory(engines[0])
    with sessions.begin() as session:
        session.add(BrainRow(name="Notes", repo_full_name="example-user/notes"))
    migrate(engines[1])  # a restart migrates again and keeps the data
    with sessions() as session:
        assert session.scalars(select(BrainRow.name)).all() == ["Notes"]
    for engine in engines:
        engine.dispose()


def open_together(path: Path, count: int) -> list[str]:
    """Open ``count`` engines on ``path`` at the same moment; return each journal mode."""
    engines = [sqlite_engine(path) for _ in range(count)]
    barrier = threading.Barrier(count)
    modes: list[str] = []
    errors: list[BaseException] = []

    def connect(engine: Engine) -> None:
        barrier.wait()
        try:
            with engine.connect() as connection:
                modes.append(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one())
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=connect, args=(engine,)) for engine in engines]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    for engine in engines:
        engine.dispose()
    assert errors == []
    return modes


def test_connections_that_open_a_new_database_together_all_succeed(tmp_path: Path) -> None:
    # On the first start, the app and the worker open the new database at the
    # same time, and each connection switches it to WAL mode.
    for round_number in range(20):
        assert open_together(tmp_path / f"new-{round_number}.sqlite", 6) == ["wal"] * 6


def test_connections_use_wal_busy_timeout_and_foreign_keys(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 1000
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

    sessions = make_session_factory(engine)
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(SourceRow(brain_id="no-such-brain", slug="dune"))


def test_deleting_a_brain_deletes_its_sources_jobs_and_events(
    engine: Engine, queue: JobQueue
) -> None:
    sessions = make_session_factory(engine)
    with sessions.begin() as session:
        doomed = BrainRow(name="Old", repo_full_name="example-user/old")
        kept = BrainRow(name="Kept", repo_full_name="example-user/kept")
        session.add_all([doomed, kept])
        session.flush()
        session.add_all(
            [
                SourceRow(brain_id=doomed.id, slug="dune", status=SourceStatus.INGESTED),
                SourceRow(brain_id=kept.id, slug="dune", status=SourceStatus.INGESTED),
            ]
        )
    doomed_job = queue.enqueue(JobKind.SOURCE_ADD, doomed.id, {"brain_id": doomed.id})
    queue.emit(doomed_job.id, "progress", {"percent": 10})
    kept_job = queue.enqueue(JobKind.SOURCE_ADD, kept.id, {"brain_id": kept.id})

    with sessions.begin() as session:
        session.delete(session.get_one(BrainRow, doomed.id))

    with sessions() as session:
        assert session.scalars(select(SourceRow.brain_id)).all() == [kept.id]
        assert session.scalars(select(JobRow.id)).all() == [kept_job.id]
        assert set(session.scalars(select(JobEventRow.job_id))) == {kept_job.id}


def test_datetimes_are_stored_in_utc_and_naive_ones_are_rejected(engine: Engine) -> None:
    sessions = make_session_factory(engine)
    synced = datetime(2026, 11, 18, 14, 30, tzinfo=timezone(timedelta(hours=2)))
    with sessions.begin() as session:
        brain = BrainRow(name="Notes", repo_full_name="example-user/notes", last_synced_at=synced)
        session.add(brain)

    with engine.connect() as connection:
        stored = connection.exec_driver_sql("SELECT last_synced_at FROM brains").scalar()
    assert stored is not None and str(stored).startswith("2026-11-18 12:30:00")
    with sessions() as session:
        loaded = session.get_one(BrainRow, brain.id).last_synced_at
    assert loaded == synced
    assert loaded is not None and loaded.tzinfo == timezone.utc

    with pytest.raises(StatementError, match="time zone"), sessions.begin() as session:
        session.get_one(BrainRow, brain.id).last_synced_at = datetime(2026, 11, 18, 12, 0)


@pytest.mark.parametrize("immediate", [None, False], ids=["default", "deferred"])
def test_sessions_take_the_write_lock_before_they_read_unless_deferred(
    engine: Engine, settings: Settings, immediate: bool | None
) -> None:
    # By default a transaction holds the write lock from its first read, so it can
    # write next. A deferred one must not block other writers while it reads.
    if immediate is None:
        sessions = make_session_factory(engine)
    else:
        sessions = make_session_factory(engine, immediate=immediate)
    other = sqlite3.connect(settings.database_path, timeout=0.05, isolation_level=None)
    try:
        with sessions.begin() as session:
            session.scalars(select(BrainRow)).all()
            if immediate is None:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("BEGIN IMMEDIATE")
            else:
                other.execute("BEGIN IMMEDIATE")
                other.execute("ROLLBACK")
    finally:
        other.close()


def test_a_read_then_write_transaction_survives_a_concurrent_worker_commit(
    engine: Engine, settings: Settings
) -> None:
    # Services read and then write in one transaction: add_source looks for a
    # duplicate, then inserts. The worker commits job events through its own
    # engine at any time. A deferred transaction that writes after such a commit
    # fails at once with "database is locked".
    sha256 = "0" * 64
    sessions = make_session_factory(engine)
    with sessions.begin() as session:
        brain = BrainRow(name="Notes", repo_full_name="example-user/notes")
        session.add(brain)
    worker_engine = make_engine(settings)
    worker = JobQueue(worker_engine)
    job = worker.enqueue(JobKind.SOURCE_INGEST, brain.id, {})
    emitted = threading.Event()
    errors: list[BaseException] = []

    def emit_progress() -> None:
        try:
            worker.emit(job.id, "progress", {"percent": 50})
            emitted.set()
        except BaseException as error:
            errors.append(error)

    emitter = threading.Thread(target=emit_progress)
    with sessions.begin() as session:
        duplicate = session.scalars(
            select(SourceRow).where(SourceRow.brain_id == brain.id, SourceRow.sha256 == sha256)
        ).first()
        assert duplicate is None
        emitter.start()
        emitted.wait(timeout=0.5)  # the worker commits now, or waits for this transaction
        session.add(SourceRow(brain_id=brain.id, slug="dune", sha256=sha256))
    emitter.join(timeout=60)

    assert errors == []
    assert emitted.is_set()
    with sessions() as session:
        assert session.scalars(select(SourceRow.slug)).all() == ["dune"]
    assert [event.kind for event in worker.events_since(0)] == ["status", "progress"]
    worker_engine.dispose()
