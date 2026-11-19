"""Models that cross the github module boundary.

Tokens are ``SecretStr``, so ``repr()``, ``str()``, and default serialization of a
model never show them. Call ``get_secret_value()`` only where the token is sent.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

TokenKind = Literal["app", "pat"]
"""``app``: a GitHub App user token from the device flow. ``pat``: a personal access token."""


class TokenSet(BaseModel):
    """The credentials of the connected GitHub account.

    All datetimes are absolute and in UTC. A naive datetime (for example one read
    back from SQLite) is taken to be UTC. ``expires_at`` and the refresh fields
    are ``None`` for tokens that do not expire.
    """

    model_config = ConfigDict(frozen=True)

    kind: TokenKind
    access_token: SecretStr
    expires_at: datetime | None = None
    refresh_token: SecretStr | None = None
    refresh_expires_at: datetime | None = None

    @field_validator("expires_at", "refresh_expires_at")
    @classmethod
    def _to_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _as_utc(value)

    def expires_within(self, margin: timedelta, now: datetime | None = None) -> bool:
        """Return True if the access token expires before ``now + margin``.

        ``now`` defaults to the current time. Like the fields, a naive ``now`` is
        taken to be UTC. A token without ``expires_at`` never expires, so the
        result is False.
        """
        if self.expires_at is None:
            return False
        current = _as_utc(now) if now is not None else datetime.now(timezone.utc)
        return self.expires_at <= current + margin


def _as_utc(value: datetime) -> datetime:
    """Return ``value`` in UTC. A naive ``value`` is taken to be UTC already."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class DeviceCode(BaseModel):
    """Step 1 of the device flow: the codes that the user and the app need.

    Show ``user_code`` and ``verification_uri`` to the user. Poll with
    ``device_code`` every ``interval`` seconds, for at most ``expires_in`` seconds.
    """

    model_config = ConfigDict(frozen=True)

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class DevicePending(BaseModel):
    """The user has not entered the code yet. Poll again after the interval."""

    model_config = ConfigDict(frozen=True)


class DeviceSlowDown(BaseModel):
    """The app polled too fast. Poll again, but wait ``interval`` seconds from now on."""

    model_config = ConfigDict(frozen=True)

    interval: int


class DeviceDenied(BaseModel):
    """The user cancelled the authorization. The user code cannot be used again."""

    model_config = ConfigDict(frozen=True)


class DeviceExpired(BaseModel):
    """The device code expired. Start the device flow again."""

    model_config = ConfigDict(frozen=True)


DevicePollResult = TokenSet | DevicePending | DeviceSlowDown | DeviceDenied | DeviceExpired
"""Every outcome of ``poll_device_flow`` that is not an error."""


class GitHubUser(BaseModel):
    """The connected GitHub user.

    ``noreply_email`` is ``<id>+<login>@users.noreply.github.com``. Brain commits
    use it, never a real email.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    login: str
    noreply_email: str


class RepoInfo(BaseModel):
    """A GitHub repository that can hold a Brain."""

    model_config = ConfigDict(frozen=True)

    full_name: str
    private: bool
    clone_url: str
    default_branch: str
