"""Models that cross the boundary of the brains module."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from second_brain_builder.okf import OKF_VERSION, Timestamp

SCHEMA_VERSION = 1
"""The ``schema_version`` of ``.brain/brain.yaml`` that this code writes."""

DEFAULT_LANGUAGE = "en"

# A BCP 47 tag in its common forms: "en", "pt-BR", "zh-Hant-TW", "es-419".
_LANGUAGE_RE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")
# Visible ASCII only. A token goes into an HTTP header, so a space or a line break
# in it could add header lines.
_TOKEN_RE = re.compile(r"[\x21-\x7e]+")


class BrainSettings(BaseModel):
    """The content of ``.brain/brain.yaml``.

    Unknown keys in the file are ignored, so a Brain that a newer version wrote
    still loads.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    okf_version: str = OKF_VERSION
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    name: str
    language: str = DEFAULT_LANGUAGE
    """BCP 47 language tag of the Wiki Pages, for example ``en`` or ``pt-BR``."""
    created_at: Timestamp
    """When the Brain was created. It must have a UTC offset."""

    @field_validator("name")
    @classmethod
    def _one_line_name(cls, value: str) -> str:
        name = " ".join(value.split())
        if not name:
            raise ValueError("the Brain name is empty")
        return name

    @field_validator("language")
    @classmethod
    def _bcp47_language(cls, value: str) -> str:
        if not _LANGUAGE_RE.fullmatch(value):
            raise ValueError(f"not a BCP 47 language tag: {value!r}")
        return value

    @field_validator("created_at")
    @classmethod
    def _has_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must have a UTC offset")
        return value


class GitIdentity(BaseModel):
    """Author and committer of the commits in a Brain repo.

    Use the GitHub noreply address (``<id>+<login>@users.noreply.github.com``),
    never a real email.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    email: str = Field(min_length=1)


class GitAuth(BaseModel):
    """A GitHub token for git over HTTPS.

    ``GitRepo`` sends it only in an ``http.extraheader`` set through environment
    variables. It never goes into argv, ``.git/config``, or a remote URL. A
    validation error never shows the token, also when it was given as a plain
    ``str``.
    """

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    token: SecretStr

    @model_validator(mode="before")
    @classmethod
    def _wrap_plain_token(cls, data: Any) -> Any:
        # Pydantic keeps the raw input of a failed field in ValidationError.errors()
        # and .json(). A SecretStr input shows there only as "**********".
        if isinstance(data, dict) and isinstance(data.get("token"), str):
            return {**data, "token": SecretStr(data["token"])}
        return data

    @field_validator("token")
    @classmethod
    def _header_safe(cls, value: SecretStr) -> SecretStr:
        if not _TOKEN_RE.fullmatch(value.get_secret_value()):
            raise ValueError("the token is empty or has spaces or control characters")
        return value


class PageSummary(BaseModel):
    """One OKF page of a Brain, without its body."""

    model_config = ConfigDict(frozen=True)

    path: PurePosixPath
    """Path from the Brain root, for example ``wiki/entities/ishmael.md``."""
    type: str
    title: str
    """The frontmatter title, or the filename stem when the page has none."""
    description: str | None = None


class GraphNode(BaseModel):
    """One page in the link graph."""

    model_config = ConfigDict(frozen=True)

    path: PurePosixPath
    type: str
    title: str


class GraphEdge(BaseModel):
    """A link from one page to another page. Citations count as links."""

    model_config = ConfigDict(frozen=True)

    from_path: PurePosixPath
    to_path: PurePosixPath


class Graph(BaseModel):
    """The pages of a Brain and the links between them, both sorted by path."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
