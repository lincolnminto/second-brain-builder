"""TokenSet behavior that services rely on to decide when to refresh."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import SecretStr

from second_brain_builder.github import TokenSet

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
MARGIN = timedelta(minutes=5)


@pytest.mark.parametrize(
    ("expires_at", "expected"),
    [
        (None, False),  # a token without expiry never needs a refresh
        (NOW + timedelta(hours=1), False),
        (NOW + timedelta(minutes=4), True),  # inside the margin
        (NOW - timedelta(minutes=1), True),  # already expired
    ],
)
def test_expires_within_margin(expires_at: datetime | None, expected: bool) -> None:
    tokens = TokenSet(
        kind="app", access_token=SecretStr("ghu_synthetic0001"), expires_at=expires_at
    )

    assert tokens.expires_within(MARGIN, now=NOW) is expected


def test_expiry_from_storage_is_normalized_to_utc() -> None:
    # SQLite gives back naive datetimes; other stores may keep another offset.
    tokens = TokenSet(
        kind="app",
        access_token=SecretStr("ghu_synthetic0001"),
        expires_at=datetime(2026, 6, 1, 12, 0),
        refresh_expires_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3))),
    )

    assert tokens.expires_at == NOW
    assert tokens.expires_at is not None
    assert tokens.expires_at.tzinfo == timezone.utc
    assert tokens.refresh_expires_at == NOW
    assert tokens.refresh_expires_at is not None
    assert tokens.refresh_expires_at.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 6, 1, 11, 58),  # naive, as from SQLite or datetime.utcnow(): UTC
        datetime(2026, 6, 1, 8, 58, tzinfo=timezone(timedelta(hours=-3))),
    ],
)
def test_expires_within_takes_now_in_the_same_forms_as_the_fields(now: datetime) -> None:
    tokens = TokenSet(
        kind="app",
        access_token=SecretStr("ghu_synthetic0001"),
        expires_at=datetime(2026, 6, 1, 12, 0),  # naive from storage: UTC
    )

    # Both values mean 11:58 UTC, two minutes before the token expires.
    assert tokens.expires_within(timedelta(minutes=5), now=now) is True
    assert tokens.expires_within(timedelta(minutes=1), now=now) is False
