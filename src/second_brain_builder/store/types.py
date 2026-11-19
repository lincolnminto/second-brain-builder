"""Column types for SQLite: UTC datetimes and string enums."""

from datetime import datetime, timezone
from enum import Enum
from typing import Generic, TypeVar

from sqlalchemy import DateTime, String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

E = TypeVar("E", bound=Enum)

ENUM_LENGTH = 32
"""Column length for enum values. SQLite does not enforce it."""


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """An aware datetime, stored in UTC.

    SQLite has no time zone type. Values are converted to UTC and stored without
    an offset, and are read back as aware UTC datetimes. A naive datetime is
    rejected, because its time zone is unknown.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"Datetime must have a time zone: {value.isoformat()}")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class EnumText(TypeDecorator[E], Generic[E]):
    """A string enum member, stored as its value (``"source.add"``, not ``"SOURCE_ADD"``).

    The database column is a plain string with no CHECK constraint, so a new enum
    member needs no migration. Values are validated in Python on write and read.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[E]) -> None:
        super().__init__(length=ENUM_LENGTH)
        # Public, with the constructor argument's name: SQLAlchemy builds the
        # statement cache key from it.
        self.enum_class = enum_class

    def process_bind_param(self, value: E | str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        member = self.enum_class(value)
        return str(member.value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> E | None:
        if value is None:
            return None
        return self.enum_class(value)
