"""Later revisions run through the same code path as ``migrate()``."""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, select

from second_brain_builder.config import Settings
from second_brain_builder.domain import JobKind
from second_brain_builder.store import (
    BrainRow,
    CredentialRow,
    GitHubAccountRow,
    JobEventRow,
    JobQueue,
    JobRow,
    MigrationError,
    ProviderRow,
    SourceRow,
    make_engine,
    make_session_factory,
)
from second_brain_builder.store.migration import MIGRATIONS_DIR, alembic_config, upgrade

REVISIONS = Path(__file__).parents[1] / "fixtures" / "store" / "revisions"


def migrations_with(revision_file: str, tmp_path: Path) -> Config:
    """Return a config for the store migrations plus one fixture revision."""
    script_location = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, script_location, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(REVISIONS / revision_file, script_location / "versions")
    return alembic_config(script_location)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


@pytest.fixture
def new_engine(settings: Settings) -> Iterator[Engine]:
    """An engine for a database file that no migration has run on yet."""
    engine = make_engine(settings)
    yield engine
    engine.dispose()


def test_a_revision_that_rebuilds_parent_tables_keeps_the_child_rows(
    new_engine: Engine, tmp_path: Path
) -> None:
    engine = new_engine
    config = migrations_with("rev_0002_rebuild_parent_tables.py", tmp_path)
    upgrade(engine, config, "0001")
    sessions = make_session_factory(engine)
    with sessions.begin() as session:
        brain = BrainRow(name="Notes", repo_full_name="example-user/notes")
        api_key = CredentialRow(kind="provider_api_key", ciphertext=b"synthetic")
        github_token = CredentialRow(kind="github_tokens", ciphertext=b"synthetic")
        session.add_all([brain, api_key, github_token])
        session.flush()
        session.add_all(
            [
                SourceRow(brain_id=brain.id, slug="dune"),
                ProviderRow(
                    name="Local",
                    kind="openai",
                    base_url="http://localhost:1234/v1",
                    credential_id=api_key.id,
                ),
                GitHubAccountRow(
                    user_id=1,
                    login="example-user",
                    token_kind="pat",
                    noreply_email="1+example-user@users.noreply.github.com",
                    credential_id=github_token.id,
                ),
            ]
        )
    queue = JobQueue(engine)
    job = queue.enqueue(JobKind.SOURCE_ADD, brain.id, {})
    queue.emit(job.id, "progress", {"percent": 10})

    upgrade(engine, config)

    assert current_revision(engine) == "0002"
    with sessions() as session:
        assert session.scalars(select(SourceRow.brain_id)).all() == [brain.id]
        assert session.scalars(select(JobRow.id)).all() == [job.id]
        assert len(session.scalars(select(JobEventRow.id)).all()) == 2
        assert session.scalars(select(ProviderRow.credential_id)).all() == [api_key.id]
        assert session.scalars(select(GitHubAccountRow.credential_id)).all() == [github_token.id]

    # Foreign keys are on again, and point at the rebuilt tables.
    with sessions.begin() as session:
        session.delete(session.get_one(BrainRow, brain.id))
    with sessions() as session:
        assert session.scalars(select(SourceRow.id)).all() == []
        assert session.scalars(select(JobEventRow.id)).all() == []


def test_a_revision_that_breaks_a_foreign_key_changes_nothing(
    new_engine: Engine, tmp_path: Path
) -> None:
    engine = new_engine
    config = migrations_with("rev_0002_orphan_source.py", tmp_path)
    upgrade(engine, config, "0001")

    with pytest.raises(MigrationError, match=r"sources row \d+ \(no brains row\)"):
        upgrade(engine, config)

    assert current_revision(engine) == "0001"
    assert "color" not in {column["name"] for column in inspect(engine).get_columns("brains")}
    with make_session_factory(engine)() as session:
        assert session.scalars(select(SourceRow.id)).all() == []


@pytest.mark.parametrize("keep_row", [False, True])
def test_rebuild_preserves_deleted_integer_ids(
    new_engine: Engine, tmp_path: Path, keep_row: bool
) -> None:
    config = migrations_with("rev_0002_rebuild_parent_tables.py", tmp_path)
    upgrade(new_engine, config, "0001")
    sessions = make_session_factory(new_engine)
    with sessions.begin() as session:
        rows = [CredentialRow(kind="test", ciphertext=b"synthetic") for _ in range(2)]
        session.add_all(rows)
        session.flush()
        highest_id = rows[-1].id
        for row in rows[int(keep_row) :]:
            session.delete(row)

    upgrade(new_engine, config)

    with sessions.begin() as session:
        row = CredentialRow(kind="test", ciphertext=b"synthetic")
        session.add(row)
        session.flush()
        assert row.id > highest_id
    # The rebuilt table must keep this guarantee for future deletions as well.
    with sessions.begin() as session:
        session.delete(session.get_one(CredentialRow, row.id))
    with sessions.begin() as session:
        replacement = CredentialRow(kind="test", ciphertext=b"synthetic")
        session.add(replacement)
        session.flush()
        assert replacement.id > row.id
