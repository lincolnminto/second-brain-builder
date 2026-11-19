"""Widen two columns of parent tables, as autogenerate writes it for SQLite.

Test fixture: a later revision that the store tests put after the real ones.
SQLite cannot change a column type in place, so each batch operation rebuilds
its table: copy, ``DROP TABLE``, rename. ``brains`` has ``ON DELETE CASCADE``
children, and ``credentials`` has ``SET NULL`` and ``RESTRICT`` children.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("brains", schema=None) as batch_op:
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=200),
            type_=sa.String(length=400),
            existing_nullable=False,
        )

    with op.batch_alter_table("credentials", schema=None) as batch_op:
        batch_op.alter_column(
            "kind",
            existing_type=sa.String(length=64),
            type_=sa.String(length=100),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("credentials", schema=None) as batch_op:
        batch_op.alter_column(
            "kind",
            existing_type=sa.String(length=100),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    with op.batch_alter_table("brains", schema=None) as batch_op:
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=400),
            type_=sa.String(length=200),
            existing_nullable=False,
        )
