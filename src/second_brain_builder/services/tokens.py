"""API tokens for agents (``Authorization: Bearer sbb_...``).

A token is ``sbb_`` plus 43 random URL-safe characters (256 bits). Only its
SHA-256 is stored; the token itself is returned once, by ``create_api_token``.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta

from pydantic import SecretStr
from sqlalchemy import select

from second_brain_builder.services._common import one_line_name
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import AuthError, NotFoundError
from second_brain_builder.services.models import ApiTokenView, CreatedApiToken
from second_brain_builder.store import ApiTokenRow

TOKEN_PREFIX = "sbb_"  # noqa: S105 (the public prefix, not a secret)
TOKEN_RANDOM_BYTES = 32
DISPLAY_PREFIX_LENGTH = len(TOKEN_PREFIX) + 6
"""``ApiTokenView.prefix``: ``sbb_`` and the first 6 random characters."""

LAST_USED_RESOLUTION = timedelta(minutes=1)
"""``verify_api_token`` writes ``last_used_at`` at most once per this time, per token."""

_TOKEN_RE = re.compile(r"sbb_[A-Za-z0-9_-]{16,200}")


def hash_token(token: str) -> str:
    """The stored form of a token: its SHA-256, hex."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_api_token(ctx: AppContext, name: str) -> CreatedApiToken:
    """Create a token. ``CreatedApiToken.secret`` is the only copy of it.

    Raises:
        InvalidInputError: The name is empty or longer than 200 characters.
    """
    clean_name = one_line_name(name, "token name")
    secret = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_RANDOM_BYTES)
    with ctx.sessions.begin() as session:
        row = ApiTokenRow(
            name=clean_name,
            token_hash=hash_token(secret),
            prefix=secret[:DISPLAY_PREFIX_LENGTH],
            created_at=ctx.clock(),
        )
        session.add(row)
        session.flush()
        view = _view(row)
    return CreatedApiToken(token=view, secret=secret)


def list_api_tokens(ctx: AppContext) -> list[ApiTokenView]:
    """Every token, oldest first, without secrets."""
    with ctx.read_sessions() as session:
        rows = session.scalars(select(ApiTokenRow).order_by(ApiTokenRow.id)).all()
        return [_view(row) for row in rows]


def delete_api_token(ctx: AppContext, token_id: int) -> None:
    """Revoke a token. Raises ``NotFoundError``."""
    with ctx.sessions.begin() as session:
        row = session.get(ApiTokenRow, token_id)
        if row is None:
            raise NotFoundError(f"API token {token_id} does not exist.")
        session.delete(row)


def verify_api_token(ctx: AppContext, token: SecretStr) -> ApiTokenView:
    """Check a Bearer token and record its use (``last_used_at``).

    Raises:
        AuthError: The token is malformed, unknown, or revoked (``service="app"``).
    """
    value = token.get_secret_value().strip()
    if _TOKEN_RE.fullmatch(value) is None:
        raise AuthError("The API token is not valid.", service="app")
    digest = hash_token(value)
    now = ctx.clock()
    with ctx.read_sessions() as session:
        row = session.scalars(select(ApiTokenRow).where(ApiTokenRow.token_hash == digest)).first()
        if row is None:
            raise AuthError("The API token is not valid.", service="app")
        view = _view(row)
    if view.last_used_at is not None and now - view.last_used_at < LAST_USED_RESOLUTION:
        return view
    with ctx.sessions.begin() as session:
        stored = session.get(ApiTokenRow, view.id)
        if stored is None:  # revoked in the meantime
            raise AuthError("The API token is not valid.", service="app")
        stored.last_used_at = now
        return _view(stored)


def _view(row: ApiTokenRow) -> ApiTokenView:
    return ApiTokenView(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )
