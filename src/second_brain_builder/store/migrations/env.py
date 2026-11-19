"""Alembic environment for the store database.

``store.migrate(engine)`` passes an open connection in
``config.attributes["connection"]``. The Alembic CLI (development only, see
``alembic.ini``) passes none, so this module opens the database in ``SBB_DATA_DIR``.
Both use ``migration_connection``, which turns foreign keys off for the migration.

Revisions must not import app code: autogenerate writes the store column types
as the plain SQLAlchemy types that they store.
"""

from typing import Any, Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import Connection

from second_brain_builder.config import get_settings
from second_brain_builder.store.db import make_engine
from second_brain_builder.store.migration import migration_connection
from second_brain_builder.store.models import Base
from second_brain_builder.store.types import ENUM_LENGTH, EnumText, UTCDateTime


def _render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> str | Literal[False]:
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    if type_ == "type" and isinstance(obj, EnumText):
        return f"sa.String(length={ENUM_LENGTH})"
    return False


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,  # SQLite changes most columns by copying the table
        render_item=_render_item,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_offline() -> None:
    context.configure(
        url="sqlite://",
        target_metadata=Base.metadata,
        literal_binds=True,
        render_as_batch=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_online() -> None:
    connection = context.config.attributes.get("connection")
    if isinstance(connection, Connection):
        _run_migrations(connection)
        return
    engine = make_engine(get_settings())
    try:
        with migration_connection(engine) as new_connection:
            _run_migrations(new_connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    _run_offline()
else:
    _run_online()
