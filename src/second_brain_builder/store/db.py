"""SQLite engine, connection pragmas, and session factories.

The ``app`` and ``worker`` containers open the same SQLite file. Every connection
uses WAL mode (readers do not block the writer), a busy timeout (a writer waits
for the other writer instead of failing), and enforced foreign keys.

Sessions from ``make_session_factory`` start each transaction with
``BEGIN IMMEDIATE``: the transaction takes the write lock first, so its reads see
the latest data and its writes cannot conflict. A ``BEGIN DEFERRED`` transaction
takes no lock until it writes. In WAL mode, a deferred transaction that read data
before another connection committed cannot upgrade to a write: it fails at once
with "database is locked", and the busy timeout does not help. The usual pattern
in services, read and then write in one transaction, needs ``BEGIN IMMEDIATE``.

Keep immediate transactions short: each one blocks every other writer until it
ends. Do not keep one open across slow work, such as an upload, a git command, or
an HTTP call. Code that only reads and must not wait for writers, such as an event
stream, can use ``make_session_factory(engine, immediate=False)``. Transactions
from ``engine.begin()`` and ``engine.connect()`` are deferred; use
``immediate_transactions(engine)`` for an immediate one.
"""

import sqlite3
import time
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, Connection
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from second_brain_builder.config import Settings

BUSY_TIMEOUT_MS = 30_000
"""How long a connection waits for a lock held by another connection or process."""

_BEGIN_OPTION = "sbb_sqlite_begin"
_BEGIN_DEFERRED = "BEGIN DEFERRED"
_BEGIN_IMMEDIATE = "BEGIN IMMEDIATE"
_LOCKED_MESSAGE = "database is locked"
_WAL_RETRY_SECONDS = 0.01


def make_engine(settings: Settings) -> Engine:
    """Return the engine for the main database, ``<data_dir>/sbb.sqlite``.

    The data directory is created if it does not exist. Call ``migrate(engine)``
    before the first use of a new database.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return sqlite_engine(settings.database_path)


def sqlite_engine(path: Path) -> Engine:
    """Return an engine for the SQLite file at ``path`` with the store pragmas."""
    engine = create_engine(URL.create("sqlite", database=str(path)))
    event.listen(engine, "connect", _configure_connection)
    event.listen(engine, "begin", _begin_transaction)
    return engine


def immediate_transactions(engine: Engine) -> Engine:
    """Return a view of ``engine`` whose transactions start with ``BEGIN IMMEDIATE``.

    The view shares the connection pool and the event listeners of ``engine``.
    """
    return engine.execution_options(**{_BEGIN_OPTION: _BEGIN_IMMEDIATE})


def make_session_factory(engine: Engine, *, immediate: bool = True) -> sessionmaker[Session]:
    """Return a session factory for services and the worker.

    Objects stay usable after commit (``expire_on_commit=False``), so a function
    can return ORM objects that it loaded in a session it has closed. Use
    ``with factory.begin() as session:`` for a transaction that commits on success
    and rolls back on an exception.

    Each transaction starts with ``BEGIN IMMEDIATE``, so a transaction can read
    rows and then write based on them (see the module docstring). With
    ``immediate=False``, transactions start with ``BEGIN DEFERRED``. Use that only
    for sessions that never write.
    """
    bind = immediate_transactions(engine) if immediate else engine
    return sessionmaker(bind=bind, expire_on_commit=False)


def _configure_connection(
    dbapi_connection: DBAPIConnection, connection_record: ConnectionPoolEntry
) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        raise TypeError("The store supports only the sqlite3 driver.")
    # The sqlite3 module sends its own BEGIN only before INSERT, UPDATE, and
    # DELETE, never before SELECT or DDL. Turn that off: _begin_transaction sends
    # BEGIN for every transaction instead.
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    try:
        # The busy timeout comes first, so the journal mode change waits for locks.
        cursor.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS:d}")
        _set_wal_mode(cursor)
        cursor.execute("PRAGMA foreign_keys = ON")
        # NORMAL is durable in WAL mode except for the last commits before a power loss.
        cursor.execute("PRAGMA synchronous = NORMAL")
    finally:
        cursor.close()


def _set_wal_mode(cursor: sqlite3.Cursor) -> None:
    """Switch the database to WAL mode. The mode stays set in the database file.

    The busy timeout does not cover one case: two connections that switch a new
    database at the same time, for example the ``app`` and the ``worker`` on the
    first start. Each one holds a read lock that the other must wait for, so
    SQLite fails one of them at once instead of letting both wait. Retry that one
    until the busy timeout ends: it then finds the database in WAL mode.
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    while True:
        try:
            cursor.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as error:
            if str(error) != _LOCKED_MESSAGE or time.monotonic() >= deadline:
                raise
        time.sleep(_WAL_RETRY_SECONDS)


def _begin_transaction(connection: Connection) -> None:
    statement = connection.get_execution_options().get(_BEGIN_OPTION, _BEGIN_DEFERRED)
    if statement not in (_BEGIN_DEFERRED, _BEGIN_IMMEDIATE):
        raise ValueError(f"Unknown transaction mode: {statement!r}")
    connection.exec_driver_sql(statement)
