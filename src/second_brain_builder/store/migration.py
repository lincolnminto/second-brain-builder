"""Run the Alembic migrations from code, without an ``alembic.ini`` file.

Migrations run with foreign keys off. SQLite changes most columns by copying the
table ("batch" mode): it creates a new table, copies the rows, drops the old
table, and renames the new one. With foreign keys on, ``DROP TABLE`` first deletes
every row and fires the ``ON DELETE`` actions of the child tables. For example, a
rebuild of ``brains`` would delete every Source, job, and event. SQLite ignores
``PRAGMA foreign_keys`` inside a transaction, so a revision cannot turn them off
itself: ``migration_connection`` does it before the transaction starts.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, Engine, event
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql import ClauseElement

from second_brain_builder.store.db import immediate_transactions
from second_brain_builder.store.models import Base

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_MAX_REPORTED_VIOLATIONS = 5


class MigrationError(Exception):
    """A migration left rows that break a foreign key. The database is unchanged."""


def alembic_config(script_location: Path = MIGRATIONS_DIR) -> Config:
    """Return an Alembic configuration for the store migrations in ``script_location``."""
    config = Config()
    # Config values use "%" for interpolation.
    config.set_main_option("script_location", str(script_location).replace("%", "%%"))
    config.set_main_option("path_separator", "os")
    return config


def migrate(engine: Engine) -> None:
    """Upgrade the database to the newest revision (``alembic upgrade head``).

    All pending revisions run in one ``BEGIN IMMEDIATE`` transaction. If the
    ``app`` and the ``worker`` start at the same time, one of them migrates and
    the other waits, then finds the database up to date. A failed revision
    changes nothing. Raises ``MigrationError`` if the revisions leave a row that
    breaks a foreign key.
    """
    upgrade(engine, alembic_config())


def upgrade(engine: Engine, config: Config, revision: str = "head") -> None:
    """Upgrade the database to ``revision`` with the revisions that ``config`` names.

    ``migrate`` calls it with the store migrations. See ``migration_connection``.
    """
    with migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


@contextmanager
def migration_connection(engine: Engine) -> Iterator[Connection]:
    """Yield a connection for Alembic, in a transaction with foreign keys off.

    The transaction starts with ``BEGIN IMMEDIATE`` and commits when the block
    ends without an error. If the block changed the database revision, the
    foreign keys are checked first, and a row that breaks one raises
    ``MigrationError`` and rolls back everything.

    The connection is closed at the end instead of going back to the pool, so no
    other code gets a connection with foreign keys off.
    """
    with immediate_transactions(engine).connect() as connection:
        sqlite_connection = connection.connection.driver_connection
        if not isinstance(sqlite_connection, sqlite3.Connection):
            raise TypeError("The store supports only the sqlite3 driver.")
        # Sent on the sqlite3 connection, which is in autocommit mode: through
        # SQLAlchemy, the statement would start a transaction and do nothing.
        sqlite_connection.execute("PRAGMA foreign_keys = OFF")
        try:
            with connection.begin():
                heads_before = _current_heads(connection)
                with _preserve_autoincrement(connection):
                    yield connection
                if _current_heads(connection) != heads_before:
                    _check_foreign_keys(connection)
        finally:
            connection.invalidate()


@contextmanager
def _preserve_autoincrement(connection: Connection) -> Iterator[None]:
    """Keep identity options and high-water marks when Alembic copies a table.

    SQLite reflection omits AUTOINCREMENT. Alembic creates ``_alembic_tmp_*``
    tables from that reflection. Seed the replacement's sequence before copying
    rows, including when the original table is empty or its highest id is gone.
    Execution listeners belong only to this connection and are removed on rollback.
    """
    sequences: dict[str, int] = {}

    def before_create(
        bind: Connection,
        statement: ClauseElement,
        _multiparams: object,
        _params: object,
        _execution_options: object,
    ) -> None:
        if not isinstance(statement, CreateTable):
            return
        table = statement.element
        if not table.name.startswith("_alembic_tmp_"):
            return
        original = table.name.removeprefix("_alembic_tmp_")
        model = Base.metadata.tables.get(original)
        if model is None or not model.dialect_options["sqlite"]["autoincrement"]:
            return
        table.dialect_options["sqlite"]["autoincrement"] = True
        sequence = bind.exec_driver_sql(
            "SELECT seq FROM sqlite_sequence WHERE name = ?", (original,)
        ).scalar_one_or_none()
        if sequence is not None:
            sequences[table.name] = sequence

    def after_create(
        bind: Connection,
        statement: ClauseElement,
        _multiparams: object,
        _params: object,
        _execution_options: object,
        _result: object,
    ) -> None:
        if isinstance(statement, CreateTable) and statement.element.name in sequences:
            table = statement.element
            bind.exec_driver_sql(
                "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)",
                (table.name, sequences.pop(table.name)),
            )

    event.listen(connection, "before_execute", before_create)
    event.listen(connection, "after_execute", after_create)
    try:
        yield
    finally:
        event.remove(connection, "after_execute", after_create)
        event.remove(connection, "before_execute", before_create)


def _current_heads(connection: Connection) -> tuple[str, ...]:
    return MigrationContext.configure(connection).get_current_heads()


def _check_foreign_keys(connection: Connection) -> None:
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if not violations:
        return
    shown = ", ".join(
        f"{table} row {rowid} (no {parent} row)"
        for table, rowid, parent, _ in violations[:_MAX_REPORTED_VIOLATIONS]
    )
    more = len(violations) - _MAX_REPORTED_VIOLATIONS
    if more > 0:
        shown += f", and {more} more"
    raise MigrationError(
        f"The migration left rows that break a foreign key: {shown}. Nothing was changed."
    )
