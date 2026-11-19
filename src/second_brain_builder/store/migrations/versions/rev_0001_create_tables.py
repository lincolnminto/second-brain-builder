"""Create every store table.

Revision ID: 0001
Revises:
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_settings")),
    )

    op.create_table(
        "credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credentials")),
        sqlite_autoincrement=True,
    )

    op.create_table(
        "github_account",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("login", sa.String(length=100), nullable=False),
        sa.Column("noreply_email", sa.String(length=320), nullable=False),
        sa.Column("token_kind", sa.String(length=16), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("connected_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name=op.f("fk_github_account_credential_id_credentials"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_github_account")),
    )
    op.create_index(op.f("ix_github_account_credential_id"), "github_account", ["credential_id"])

    op.create_table(
        "providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name=op.f("fk_providers_credential_id_credentials"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_providers")),
        sa.UniqueConstraint("name", name=op.f("uq_providers_name")),
        sqlite_autoincrement=True,
    )
    op.create_index(op.f("ix_providers_credential_id"), "providers", ["credential_id"])

    op.create_table(
        "task_settings",
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_task_settings_provider_id_providers"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("task", name=op.f("pk_task_settings")),
    )
    op.create_index(op.f("ix_task_settings_provider_id"), "task_settings", ["provider_id"])

    op.create_table(
        "brains",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("repo_full_name", sa.String(length=200), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brains")),
        sa.UniqueConstraint("repo_full_name", name=op.f("uq_brains_repo_full_name")),
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brain_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("folder", sa.String(length=1024), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=1024), nullable=True),
        sa.Column("media_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["brain_id"],
            ["brains.id"],
            name=op.f("fk_sources_brain_id_brains"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_sources_brain_id_slug", "sources", ["brain_id", "slug"], unique=True)
    op.create_index("ix_sources_brain_id_sha256", "sources", ["brain_id", "sha256"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brain_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["brain_id"],
            ["brains.id"],
            name=op.f("fk_jobs_brain_id_brains"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_jobs_status_id", "jobs", ["status", "id"])
    op.create_index("ix_jobs_brain_id_status", "jobs", ["brain_id", "status"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("brain_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_job_events_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["brain_id"],
            ["brains.id"],
            name=op.f("fk_job_events_brain_id_brains"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_events")),
        sqlite_autoincrement=True,
    )
    op.create_index(op.f("ix_job_events_job_id"), "job_events", ["job_id"])
    op.create_index("ix_job_events_brain_id_id", "job_events", ["brain_id", "id"])

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_tokens_token_hash")),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("api_tokens")
    op.drop_table("job_events")
    op.drop_table("jobs")
    op.drop_table("sources")
    op.drop_table("brains")
    op.drop_table("task_settings")
    op.drop_table("providers")
    op.drop_table("github_account")
    op.drop_table("credentials")
    op.drop_table("settings")
