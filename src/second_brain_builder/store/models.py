"""ORM models, one per table.

Class names end in ``Row`` so that they do not clash with the models that cross
module boundaries (for example ``Job``, which ``JobQueue`` returns). Datetimes are
aware and in UTC. Integer ids use SQLite ``AUTOINCREMENT``, so the id of a deleted
row is never given to a new row: a stale id in a client never points at another row.

Deleting a Brain deletes its ``sources``, ``jobs``, and ``job_events`` rows.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, LargeBinary, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from second_brain_builder.domain import (
    BrainStatus,
    JobKind,
    JobStatus,
    SourceStatus,
    TaskMode,
    TaskName,
)
from second_brain_builder.store.types import EnumText, UTCDateTime, utc_now

# Named constraints, so that later migrations can drop or change them. SQLite
# alters tables by copying them ("batch" mode), which needs the names.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_AUTOINCREMENT = {"sqlite_autoincrement": True}


def _new_brain_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    """Declarative base for every store table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class SettingRow(Base):
    """An app-wide setting: one JSON value per key."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class CredentialRow(Base):
    """A secret encrypted with ``Crypto``, such as a GitHub token set or a Provider API key."""

    __tablename__ = "credentials"
    __table_args__ = _AUTOINCREMENT

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class GitHubAccountRow(Base):
    """The connected GitHub account. The app has at most one."""

    __tablename__ = "github_account"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    login: Mapped[str] = mapped_column(String(100))
    noreply_email: Mapped[str] = mapped_column(String(320))
    token_kind: Mapped[str] = mapped_column(String(16))  # "app" or "pat"
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("credentials.id", ondelete="RESTRICT"), index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class ProviderRow(Base):
    """An LLM Provider. ``kind`` is a ``providers.ProviderKind`` value."""

    __tablename__ = "providers"
    __table_args__ = _AUTOINCREMENT

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(2048))
    # None when the Provider needs no API key, for example a local server.
    credential_id: Mapped[int | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class TaskSettingRow(Base):
    """Where a task runs: locally in the worker, or with a Provider and model."""

    __tablename__ = "task_settings"

    task: Mapped[TaskName] = mapped_column(EnumText(TaskName), primary_key=True)
    mode: Mapped[TaskMode] = mapped_column(EnumText(TaskMode))
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), index=True
    )
    model: Mapped[str | None] = mapped_column(String(200))


class BrainRow(Base):
    """A Brain and the GitHub repository that holds it."""

    __tablename__ = "brains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_brain_id)
    name: Mapped[str] = mapped_column(String(200))
    repo_full_name: Mapped[str] = mapped_column(String(200), unique=True)
    language: Mapped[str] = mapped_column(String(35), default="en")  # BCP 47
    status: Mapped[BrainStatus] = mapped_column(EnumText(BrainStatus), default=BrainStatus.CREATING)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SourceRow(Base):
    """A Source of a Brain. The worker can rebuild these rows from the Brain clone."""

    __tablename__ = "sources"
    __table_args__ = (
        Index("ix_sources_brain_id_slug", "brain_id", "slug", unique=True),
        Index("ix_sources_brain_id_sha256", "brain_id", "sha256"),
        _AUTOINCREMENT,
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    brain_id: Mapped[str] = mapped_column(ForeignKey("brains.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(200))  # the Source id, unique per Brain
    folder: Mapped[str] = mapped_column(String(1024), default="")  # slug path; "" is the root
    # The Source file in the Brain repo, for example "sources/books/dune/dune.epub".
    path: Mapped[str | None] = mapped_column(String(2048))
    sha256: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(1024))
    media_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[SourceStatus] = mapped_column(
        EnumText(SourceStatus), default=SourceStatus.QUEUED
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class JobRow(Base):
    """A job for the worker. Use ``JobQueue`` to change job status."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_id", "status", "id"),
        Index("ix_jobs_brain_id_status", "brain_id", "status"),
        _AUTOINCREMENT,
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # None for a job that writes to no Brain. Such jobs never wait for other jobs.
    brain_id: Mapped[str | None] = mapped_column(ForeignKey("brains.id", ondelete="CASCADE"))
    kind: Mapped[JobKind] = mapped_column(EnumText(JobKind))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[JobStatus] = mapped_column(EnumText(JobStatus), default=JobStatus.QUEUED)
    # How many times a worker stopped while the job was running.
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    stop_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    input_tokens: Mapped[int | None] = mapped_column()
    output_tokens: Mapped[int | None] = mapped_column()


class JobEventRow(Base):
    """An event that a job emitted, for the Server-Sent Events stream."""

    __tablename__ = "job_events"
    __table_args__ = (
        Index("ix_job_events_brain_id_id", "brain_id", "id"),
        _AUTOINCREMENT,
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    # A copy of the job's brain_id, so that a Brain's events need no join.
    brain_id: Mapped[str | None] = mapped_column(ForeignKey("brains.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    kind: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class ApiTokenRow(Base):
    """An API token for agents. Only the SHA-256 hash of the token is stored."""

    __tablename__ = "api_tokens"
    __table_args__ = _AUTOINCREMENT

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # hex SHA-256
    prefix: Mapped[str] = mapped_column(String(32))  # shown in the UI to identify the token
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
