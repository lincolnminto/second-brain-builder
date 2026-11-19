"""Settings from ``SBB_*`` environment variables.

Settings come from the process environment only. Docker Compose loads ``.env``
for the containers. For a local run, use ``uv run --env-file .env ...``.
Empty values count as unset, so ``SBB_PASSWORD=`` does not enable a login with
an empty password.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

GIB = 1024**3


class Settings(BaseSettings):
    """Runtime configuration. Each field reads the variable ``SBB_<FIELD_NAME>``."""

    model_config = SettingsConfigDict(
        env_prefix="SBB_",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    data_dir: Path = Field(
        default=Path("/data"),
        description="Database, secret key, staged uploads, and search index.",
    )
    brains_dir: Path = Field(
        default=Path("/brains"),
        description="One git clone per Brain, at <brains_dir>/<brain-id>/.",
    )
    models_dir: Path = Field(
        default=Path("/models"),
        description="Docling and Whisper model cache. HF_HOME points here.",
    )
    secret_key: SecretStr | None = Field(
        default=None,
        description="Fernet key for credentials. If unset, the key in secret_key_path is used "
        "or generated.",
    )
    password: SecretStr | None = Field(
        default=None,
        description="If set, the web UI needs a login with this password.",
    )
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"],
        description="Host header allowlist. The variable is a comma-separated list. "
        "Values are stripped and lowercased.",
    )
    public_url: str = Field(
        default="http://localhost:8484",
        description="Base URL shown in agent setup, without a trailing slash.",
    )
    web_dir: Path | None = Field(
        default=None,
        description="Directory of the built web UI (with index.html). If unset, the app "
        "serves web/dist of the source tree when it exists.",
    )
    github_client_id: str | None = Field(
        default=None,
        description="GitHub App client id for the device flow. The project GitHub App "
        "is not registered yet, so there is no default.",
    )
    github_app_slug: str | None = Field(
        default=None,
        description="GitHub App slug for the install URL. The project GitHub App "
        "is not registered yet, so there is no default.",
    )
    lfs_quota_bytes: int = Field(
        default=10 * GIB,
        gt=0,
        description="Git LFS usage above which an upload needs confirmation.",
    )
    max_upload_bytes: int = Field(
        default=2 * GIB,
        gt=0,
        description="Hard size limit for one upload.",
    )
    ingest_max_tokens: int = Field(
        default=400_000,
        gt=0,
        description="LLM token budget for one Ingest run.",
    )
    ingest_part_tokens: int = Field(
        default=16_000,
        gt=0,
        description="Target token size of one Ingest Part.",
    )
    worker_concurrency: int = Field(
        default=1,
        ge=1,
        description="Number of jobs that the worker runs in parallel.",
    )

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list):
            return [str(host).strip().lower() for host in value if str(host).strip()]
        return value

    @field_validator("public_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def database_path(self) -> Path:
        """Main SQLite database."""
        return self.data_dir / "sbb.sqlite"

    @property
    def search_db_path(self) -> Path:
        """SQLite FTS5 search index."""
        return self.data_dir / "search.sqlite"

    @property
    def uploads_dir(self) -> Path:
        """Staged uploads, one ``<uuid>/`` directory per upload."""
        return self.data_dir / "uploads"

    @property
    def secret_key_path(self) -> Path:
        """Generated Fernet key, used when ``secret_key`` is unset."""
        return self.data_dir / "secret.key"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process settings, read once from the environment.

    Call ``get_settings.cache_clear()`` to read the environment again.
    """
    return Settings()
