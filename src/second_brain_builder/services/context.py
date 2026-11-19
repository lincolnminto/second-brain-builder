"""The shared state of the use cases: database, credentials, search index, and job queue.

The ``app`` creates one ``AppContext`` at startup and passes it to every use case.
The ``worker`` can create its own to use the shared accessors
(``get_github_auth``, ``get_ingest_model``, ``get_transcriber``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
import httpx2
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from second_brain_builder.config import Settings
from second_brain_builder.providers import ChatModel, ProviderConfig
from second_brain_builder.search import SearchIndex
from second_brain_builder.store import (
    Crypto,
    JobQueue,
    make_engine,
    make_session_factory,
    migrate,
    utc_now,
)

ChatModelFactory = Callable[[ProviderConfig, str], ChatModel]
"""Builds the chat model for a Provider and a model name. Tests pass a scripted one."""

Clock = Callable[[], datetime]
"""Returns the current time as an aware UTC datetime."""


@dataclass
class AppContext:
    """Everything that the use cases share. Build it with ``AppContext.create``.

    The seams for tests are ``github_transport`` (an ``httpx`` transport for every
    GitHub call), ``provider_transport`` (an ``httpx2`` transport for every
    Provider call), ``chat_model_factory`` (replaces ``make_chat_model``), and
    ``clock``.
    """

    settings: Settings
    engine: Engine
    sessions: sessionmaker[Session]
    """``BEGIN IMMEDIATE`` sessions, for transactions that write."""
    read_sessions: sessionmaker[Session]
    """``BEGIN DEFERRED`` sessions, for transactions that only read."""
    crypto: Crypto
    search: SearchIndex
    jobs: JobQueue
    github_transport: httpx.BaseTransport | None = None
    provider_transport: httpx2.BaseTransport | None = None
    chat_model_factory: ChatModelFactory | None = None
    clock: Clock = utc_now
    github_refresh_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    """Serializes GitHub token refreshes in this process."""
    add_source_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    """Serializes, in this process, the checks before an upload or a retried job is enqueued."""

    @classmethod
    def create(
        cls,
        settings: Settings,
        *,
        github_transport: httpx.BaseTransport | None = None,
        provider_transport: httpx2.BaseTransport | None = None,
        chat_model_factory: ChatModelFactory | None = None,
        clock: Clock = utc_now,
        migrate_database: bool = True,
    ) -> AppContext:
        """Open the database (and migrate it), the secret key, and the search index."""
        engine = make_engine(settings)
        if migrate_database:
            migrate(engine)
        return cls(
            settings=settings,
            engine=engine,
            sessions=make_session_factory(engine),
            read_sessions=make_session_factory(engine, immediate=False),
            crypto=Crypto.load_or_create(settings),
            search=SearchIndex(settings.search_db_path),
            jobs=JobQueue(engine),
            github_transport=github_transport,
            provider_transport=provider_transport,
            chat_model_factory=chat_model_factory,
            clock=clock,
        )

    def close(self) -> None:
        """Close the database connections."""
        self.engine.dispose()

    def clone_path(self, brain_id: str) -> Path:
        """The Brain clone, ``<brains_dir>/<brain-id>``.

        Call it only with a Brain id from the database: the id is a path part.
        """
        return self.settings.brains_dir / brain_id
