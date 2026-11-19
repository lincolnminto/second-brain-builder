from pathlib import Path

import pytest

from second_brain_builder.config import Settings


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SBB_DATA_DIR", "/srv/sbb-data")
    monkeypatch.setenv("SBB_ALLOWED_HOSTS", " Brain.Example.COM , 192.0.2.10,,")
    monkeypatch.setenv("SBB_PUBLIC_URL", "https://brain.example.com/")
    monkeypatch.setenv("SBB_MAX_UPLOAD_BYTES", "1048576")
    monkeypatch.setenv("SBB_PASSWORD", "correct-horse")
    monkeypatch.setenv("SBB_INGEST_PART_TOKENS", "12000")

    settings = Settings()

    assert settings.allowed_hosts == ["brain.example.com", "192.0.2.10"]
    assert settings.public_url == "https://brain.example.com"
    assert settings.max_upload_bytes == 1048576
    assert settings.ingest_part_tokens == 12000
    assert settings.database_path == Path("/srv/sbb-data/sbb.sqlite")
    assert settings.search_db_path == Path("/srv/sbb-data/search.sqlite")
    assert settings.uploads_dir == Path("/srv/sbb-data/uploads")
    assert settings.secret_key_path == Path("/srv/sbb-data/secret.key")
    assert settings.password is not None
    assert settings.password.get_secret_value() == "correct-horse"
    assert "correct-horse" not in repr(settings)


def test_empty_variables_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Compose writes empty values for variables that .env leaves blank. An empty
    # SBB_PASSWORD must not turn on a login with an empty password.
    monkeypatch.setenv("SBB_PASSWORD", "")
    monkeypatch.setenv("SBB_SECRET_KEY", "")
    monkeypatch.setenv("SBB_ALLOWED_HOSTS", "")

    settings = Settings()

    assert settings.password is None
    assert settings.secret_key is None
    assert settings.allowed_hosts == ["localhost", "127.0.0.1", "[::1]"]
    assert settings.ingest_part_tokens == 16000
