"""A faulty revision: it adds a column, then leaves a Source without a Brain.

Test fixture: a later revision that the store tests put after the real ones.

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
    op.add_column("brains", sa.Column("color", sa.String(length=16), nullable=True))
    op.execute(
        "INSERT INTO sources (brain_id, slug, folder, status, created_at, updated_at) "
        "VALUES ('no-such-brain', 'orphan', '', 'queued', "
        "'2026-11-18 12:00:00', '2026-11-18 12:00:00')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM sources WHERE slug = 'orphan'")
    op.drop_column("brains", "color")
