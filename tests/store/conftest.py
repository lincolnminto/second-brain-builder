from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Engine

from second_brain_builder.config import Settings
from second_brain_builder.store import (
    BrainRow,
    JobQueue,
    make_engine,
    make_session_factory,
    migrate,
)


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    """A migrated database file in the test's data directory."""
    engine = make_engine(settings)
    migrate(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def queue(engine: Engine) -> JobQueue:
    return JobQueue(engine)


@pytest.fixture
def add_brain(engine: Engine) -> Callable[[str], str]:
    """Insert a Brain row and return its id."""
    sessions = make_session_factory(engine)

    def add(name: str) -> str:
        with sessions.begin() as session:
            brain = BrainRow(name=name, repo_full_name=f"example-user/{name}")
            session.add(brain)
            session.flush()
            return brain.id

    return add
