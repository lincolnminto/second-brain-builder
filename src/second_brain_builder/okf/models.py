"""OKF v0.3 data models: page types, frontmatter, pages, and validation issues."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from second_brain_builder.domain import StrEnum

OKF_VERSION = "0.3"
"""The OKF version that this module reads and writes."""

RESERVED_FILENAMES = frozenset({"index.md", "log.md"})
"""Filenames that are never concepts, at any level of a bundle (OKF §3.1)."""


class OKFError(ValueError):
    """Text that cannot be read as an OKF page, for example frontmatter that is not YAML."""


class PageType(StrEnum):
    """The page types of a Brain. ``SOURCE`` is a Canonical Document; the others are Wiki Pages."""

    SOURCE = "source"
    SUMMARY = "summary"
    ENTITY = "entity"
    CONCEPT = "concept"
    COMPARISON = "comparison"
    SYNTHESIS = "synthesis"
    NAME_INDEX = "name-index"

    @property
    def is_wiki_page(self) -> bool:
        """True for Wiki types, including code-built indexes, that must cite Sources."""
        return self is not PageType.SOURCE


_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DATE_TIME_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(\.\d+)?"
    r"(?:(?P<utc>[Zz])|(?P<sign>[+-])(?P<oh>\d{2}):(?P<om>\d{2}))?"
)


def parse_timestamp(value: str) -> datetime:
    """Parse an RFC 3339 ``date-time`` or ``full-date`` (OKF §5).

    A date only means the start of that day in UTC. A date-time without an offset
    gives a naive ``datetime``: OKF does not allow it, and validation reports it.

    Raises:
        ValueError: The value is not in either form.
    """
    text = value.strip()
    date_match = _DATE_RE.fullmatch(text)
    if date_match:
        year, month, day = (int(part) for part in date_match.groups())
        return datetime(year, month, day, tzinfo=timezone.utc)
    match = _DATE_TIME_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"not an RFC 3339 timestamp: {value!r}")
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    fraction = match.group(7)
    microsecond = int((fraction[1:] + "000000")[:6]) if fraction else 0
    tz: timezone | None = None
    if match.group("utc"):
        tz = timezone.utc
    elif match.group("sign"):
        if int(match.group("om")) > 59:
            raise ValueError(f"not an RFC 3339 timestamp: {value!r}")
        offset = timedelta(hours=int(match.group("oh")), minutes=int(match.group("om")))
        tz = timezone(-offset if match.group("sign") == "-" else offset)
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=tz)


def _coerce_timestamp(value: Any) -> datetime:
    # Pydantic would read a number as Unix time; OKF timestamps are RFC 3339 only.
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        return parse_timestamp(value)
    raise ValueError(f"not an RFC 3339 timestamp: {value!r}")


Timestamp = Annotated[datetime, BeforeValidator(_coerce_timestamp)]
"""A ``datetime`` that also accepts RFC 3339 strings and dates, as OKF timestamps do."""


WIKI_PAGE_TYPES = frozenset(page_type.value for page_type in PageType if page_type.is_wiki_page)
"""The ``type`` values of Wiki Pages. A Wiki Page must cite every Source in its ``sources``."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_extension_keys(extensions: dict[str, Any], fields: frozenset[str]) -> None:
    clashes = sorted(key for key in extensions if key in fields)
    if clashes:
        raise ValueError(f"extensions repeat keys that have their own field: {', '.join(clashes)}")


class SourceRef(_Model):
    """One ``sources`` entry: a material that the page derives from (OKF §5.1).

    ``id`` is the footnote label that cites the source in the body.
    """

    id: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    title: str | None = None
    author: str | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)
    """Every other key of the entry, for example ``usage_count`` or ``x-sbb-...``."""

    @model_validator(mode="after")
    def _extensions_do_not_repeat_fields(self) -> SourceRef:
        _check_extension_keys(self.extensions, _SOURCE_REF_KEYS)
        return self


class Stamp(_Model):
    """A ``generated`` record or one ``verified`` event: who or what acted, and when (OKF §5.2)."""

    by: str = Field(min_length=1)
    at: Timestamp
    extensions: dict[str, Any] = Field(default_factory=dict)
    """Every other key of the mapping."""

    @model_validator(mode="after")
    def _extensions_do_not_repeat_fields(self) -> Stamp:
        _check_extension_keys(self.extensions, _STAMP_KEYS)
        return self


class Frontmatter(_Model):
    """The YAML frontmatter of a page (OKF §4.1).

    ``type`` is empty when the frontmatter has no ``type`` key: the file is then not
    a concept (OKF §3.2), for example the bundle-root ``index.md``. Every key without
    a field of its own is kept in ``extensions``, also spec keys such as
    ``usage_window`` and ``okf_version``, so a read and a write keep it.
    """

    type: str
    title: str | None = None
    description: str | None = None
    resource: str | None = None
    tags: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    generated: Stamp | None = None
    verified: list[Stamp] = Field(default_factory=list)
    status: str | None = None
    superseded_by: str | None = None
    stale_after: Timestamp | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _extensions_do_not_repeat_fields(self) -> Frontmatter:
        _check_extension_keys(self.extensions, FRONTMATTER_KEYS)
        return self


class Page(_Model):
    """One Markdown file of a bundle.

    ``path`` is relative to the bundle root, for example ``wiki/entities/ishmael.md``.
    ``frontmatter`` is ``None`` when the file has no frontmatter block. ``body`` is the
    Markdown after the frontmatter, without the blank lines that separate the two.
    """

    path: PurePosixPath
    frontmatter: Frontmatter | None
    body: str = ""

    @field_validator("path")
    @classmethod
    def _path_is_relative(cls, path: PurePosixPath) -> PurePosixPath:
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError(f"page path must be relative to the bundle root: {path}")
        return path


Severity = Literal["error", "warning"]


class Issue(BaseModel):
    """One validation finding for one file."""

    model_config = ConfigDict(frozen=True)

    path: PurePosixPath
    code: str
    message: str
    severity: Severity


class IssueCode(StrEnum):
    """The ``code`` values of validation issues.

    Errors break OKF v0.3 or a Brain rule that code relies on. Warnings are advice:
    OKF consumers accept these files.
    """

    # Errors
    INVALID_ENCODING = "invalid-encoding"
    INVALID_FRONTMATTER = "invalid-frontmatter"
    MISSING_TYPE = "missing-type"
    MISSING_SOURCES = "missing-sources"
    DUPLICATE_SOURCE_ID = "duplicate-source-id"
    UNKNOWN_CITATION = "unknown-citation"
    UNCITED_SOURCE = "uncited-source"
    TIMESTAMP_WITHOUT_OFFSET = "timestamp-without-offset"
    INVALID_TIMESTAMP = "invalid-timestamp"
    MISSING_INDEX = "missing-index"
    OKF_VERSION = "okf-version"
    INDEX_FRONTMATTER = "index-frontmatter"
    LOG_FRONTMATTER = "log-frontmatter"
    LOG_DATE_HEADING = "log-date-heading"
    # Warnings
    NOT_A_CONCEPT = "not-a-concept"
    UNDEFINED_FOOTNOTE = "undefined-footnote"
    BROKEN_LINK = "broken-link"
    UNPREFIXED_EXTENSION_KEY = "unprefixed-extension-key"
    WIKILINK = "wikilink"
    LOG_ORDER = "log-order"


FRONTMATTER_KEYS = frozenset(name for name in Frontmatter.model_fields if name != "extensions")
"""The frontmatter keys that have a field of their own in ``Frontmatter``."""

_SOURCE_REF_KEYS = frozenset(name for name in SourceRef.model_fields if name != "extensions")
_STAMP_KEYS = frozenset(name for name in Stamp.model_fields if name != "extensions")


def is_concept(page: Page) -> bool:
    """True when the page is an OKF concept: it has a ``type`` and a non-reserved name (§3.2)."""
    return (
        page.frontmatter is not None
        and bool(page.frontmatter.type)
        and page.path.name not in RESERVED_FILENAMES
    )
