"""API tokens: shown once, stored as SHA-256, verified with a throttled last_used_at."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from second_brain_builder.services import (
    AppContext,
    AuthError,
    NotFoundError,
    create_api_token,
    delete_api_token,
    list_api_tokens,
    verify_api_token,
)
from second_brain_builder.store import ApiTokenRow

if TYPE_CHECKING:
    from tests.services.conftest import FakeClock


def test_token_is_returned_once_and_only_its_hash_is_stored(ctx: AppContext) -> None:
    created = create_api_token(ctx, "  Laptop   agent ")

    secret = created.secret
    assert secret.startswith("sbb_")
    assert len(secret) >= 40
    assert created.token.name == "Laptop agent"
    assert secret.startswith(created.token.prefix)
    with ctx.read_sessions() as session:
        row = session.scalars(select(ApiTokenRow)).one()
        stored = [str(value) for value in (row.name, row.token_hash, row.prefix)]
    assert row.token_hash == hashlib.sha256(secret.encode()).hexdigest()
    assert all(secret not in value for value in stored)
    assert secret not in list_api_tokens(ctx)[0].model_dump_json()


def test_verify_records_use_at_most_once_a_minute(ctx: AppContext, clock: FakeClock) -> None:
    created = create_api_token(ctx, "agent")

    first = verify_api_token(ctx, SecretStr(created.secret))
    assert first.last_used_at == clock.now

    used_at = clock.now
    clock.advance(timedelta(seconds=30))
    assert verify_api_token(ctx, SecretStr(created.secret)).last_used_at == used_at

    clock.advance(timedelta(minutes=1))
    assert verify_api_token(ctx, SecretStr(created.secret)).last_used_at == clock.now


@pytest.mark.parametrize(
    "token", ["", "sbb_short", "ghp_syntheticPersonalToken000000000001", "sbb_" + "x" * 43]
)
def test_malformed_or_unknown_tokens_are_rejected(ctx: AppContext, token: str) -> None:
    create_api_token(ctx, "agent")

    with pytest.raises(AuthError) as caught:
        verify_api_token(ctx, SecretStr(token))
    assert caught.value.service == "app"


def test_deleted_token_no_longer_verifies(ctx: AppContext) -> None:
    created = create_api_token(ctx, "agent")

    delete_api_token(ctx, created.token.id)

    with pytest.raises(AuthError):
        verify_api_token(ctx, SecretStr(created.secret))
    with pytest.raises(NotFoundError):
        delete_api_token(ctx, created.token.id)
