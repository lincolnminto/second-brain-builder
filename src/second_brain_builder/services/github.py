"""GitHub connection: device flow, personal access token, account, repos, and token access.

The token set of the connected account is stored encrypted in ``credentials``.
``get_github_auth`` and ``github_client`` give the worker and the other use
cases the stored token: a GitHub App token that expires within
``TOKEN_REFRESH_MARGIN`` is refreshed first.

A refresh revokes the old access token at once, and the ``app`` and the
``worker`` can each refresh. So a token can stop working before it expires:
get it again right before each GitHub call or git network operation, and do
not keep it for a whole job.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from second_brain_builder.brains import GitAuth, GitIdentity
from second_brain_builder.github import (
    DeviceDenied,
    DeviceExpired,
    DeviceFlowDisabledError,
    DevicePending,
    DeviceSlowDown,
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubNotFoundError,
    GitHubOAuthError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubUser,
    GitHubValidationError,
    TokenSet,
    install_url,
    poll_device_flow,
    refresh_token,
    start_device_flow,
)
from second_brain_builder.services._common import delete_setting, read_setting, write_setting
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import (
    AuthError,
    ConflictError,
    InvalidInputError,
    NotConfiguredError,
    NotConnectedError,
    NotFoundError,
    ServiceError,
    UpstreamError,
)
from second_brain_builder.services.models import (
    DeviceFlowPoll,
    DeviceFlowStart,
    GitHubAccountView,
    RepoView,
    TokenKind,
)
from second_brain_builder.store import BrainRow, CredentialRow, DecryptionError, GitHubAccountRow

_log = logging.getLogger(__name__)

TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
"""A GitHub App token that expires within this time is refreshed before use."""

REFRESH_RACE_WAIT_SECONDS = 3.0
"""How long a refresh that GitHub rejected waits for new tokens from another process.

GitHub accepts a refresh token only once. When the ``app`` and the ``worker``
refresh at the same time, GitHub rejects the second request, and the first
process stores the new tokens a moment later.
"""

_REFRESH_RACE_POLL_SECONDS = 0.05

GITHUB_CREDENTIAL_KIND = "github_tokens"
"""``credentials.kind`` of the connected account's encrypted token set."""

DEVICE_FLOW_SETTING = "github.device_flow"
"""``settings`` key of the device flow in progress. The device code in it is encrypted."""

_RECONNECT = "Connect GitHub again in the settings."


class GitHubAuth(BaseModel):
    """What git needs to read and write the Brain repos of the connected account.

    ``auth`` holds the token that was current when ``get_github_auth`` returned.
    A refresh by this or another process revokes it, so call ``get_github_auth``
    again right before each git network operation (clone, pull, push, LFS)
    instead of keeping one for a whole job. ``identity`` is the GitHub noreply
    identity for commits.
    """

    model_config = ConfigDict(frozen=True)

    auth: GitAuth
    identity: GitIdentity
    login: str
    token_kind: TokenKind


class _StoredTokens(BaseModel):
    """The plaintext of the encrypted credential. Never logged or returned."""

    model_config = ConfigDict(hide_input_in_errors=True)

    kind: TokenKind
    access_token: str
    expires_at: datetime | None = None
    refresh_token: str | None = None
    refresh_expires_at: datetime | None = None


class _DeviceFlowState(BaseModel):
    device_code: str
    """The device code, encrypted with ``Crypto`` (a Fernet token)."""
    interval: int
    expires_at: datetime


class _Account(BaseModel):
    """A copy of the account row, so it can leave its session."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    login: str
    noreply_email: str
    token_kind: TokenKind
    credential_id: int


# --- error mapping ---------------------------------------------------------------------


def github_service_error(exc: GitHubError) -> ServiceError:
    """The service error for a failed GitHub call.

    ``GitHubNotFoundError`` gives ``NotFoundError``, ``GitHubAuthError`` and
    ``GitHubPermissionError`` an ``AuthError`` (connect again or fix the
    permissions), ``GitHubValidationError`` an ``InvalidInputError``, and every
    other error an ``UpstreamError``. GitHub errors never hold a token, so callers
    chain them.
    """
    if isinstance(exc, GitHubAuthError):
        return AuthError(
            f"GitHub rejected the token of the connected account. {_RECONNECT}", service="github"
        )
    if isinstance(exc, GitHubRateLimitError):
        return UpstreamError(
            f"GitHub limits the request rate. Try again later. ({exc})",
            service="github",
            status_code=exc.status_code,
            retry_at=exc.reset_at,
        )
    if isinstance(exc, GitHubPermissionError):
        return AuthError(
            "GitHub refused the request. Check the permissions of the token or of the "
            f"GitHub App installation. ({exc})",
            service="github",
        )
    if isinstance(exc, GitHubNotFoundError):
        return NotFoundError(f"GitHub found no such resource, or the token cannot see it. ({exc})")
    if isinstance(exc, GitHubValidationError):
        return InvalidInputError(f"GitHub rejected the request. ({exc})")
    return UpstreamError(
        f"The GitHub request failed. ({exc})", service="github", status_code=exc.status_code
    )


@contextmanager
def github_errors() -> Iterator[None]:
    """Raise ``github_service_error`` for a ``GitHubError`` in the block.

    Put only GitHub calls in the block, so that a ``NotFoundError`` from it always
    means a GitHub resource.
    """
    try:
        yield
    except GitHubError as exc:
        raise github_service_error(exc) from exc


# --- device flow -----------------------------------------------------------------------


def start_github_device_flow(ctx: AppContext) -> DeviceFlowStart:
    """Start the device flow of the project GitHub App (step 1).

    A device flow that is in progress is replaced.

    Raises:
        NotConfiguredError: ``SBB_GITHUB_CLIENT_ID`` is unset, or the GitHub App
            does not have the device flow enabled.
        UpstreamError: GitHub failed.
    """
    client_id = _client_id(ctx)
    try:
        code = start_device_flow(client_id, transport=ctx.github_transport)
    except DeviceFlowDisabledError as exc:
        raise NotConfiguredError("The GitHub App does not have the device flow enabled.") from exc
    except GitHubError as exc:
        raise github_service_error(exc) from exc
    state = _DeviceFlowState(
        device_code=ctx.crypto.encrypt(code.device_code).decode("ascii"),
        interval=code.interval,
        expires_at=ctx.clock() + timedelta(seconds=code.expires_in),
    )
    with ctx.sessions.begin() as session:
        write_setting(session, DEVICE_FLOW_SETTING, state.model_dump(mode="json"))
    return DeviceFlowStart(
        user_code=code.user_code,
        verification_uri=code.verification_uri,
        expires_in=code.expires_in,
        interval=code.interval,
    )


def poll_github_device_flow(ctx: AppContext) -> DeviceFlowPoll:
    """Poll GitHub once for the user's authorization (step 3 of the device flow).

    When the user authorized the app, the token set is stored encrypted with the
    account's login, id, and noreply email, and the result has the account.

    Raises:
        ConflictError: No device flow is in progress.
        NotConfiguredError: ``SBB_GITHUB_CLIENT_ID`` is unset.
        UpstreamError: GitHub failed, or rejected the device code (start again).
    """
    client_id = _client_id(ctx)
    state = _read_device_state(ctx)
    if state is None:
        raise ConflictError("No GitHub device flow is in progress. Start it again.")
    if ctx.clock() >= state.expires_at:
        _clear_device_state(ctx)
        return DeviceFlowPoll(status="expired")
    try:
        device_code = ctx.crypto.decrypt(state.device_code.encode("ascii"))
    except DecryptionError:
        _clear_device_state(ctx)
        raise ConflictError("The GitHub device flow cannot continue. Start it again.") from None
    try:
        result = poll_device_flow(
            client_id, device_code, interval=state.interval, transport=ctx.github_transport
        )
    except DeviceFlowDisabledError as exc:
        _clear_device_state(ctx)
        raise NotConfiguredError("The GitHub App does not have the device flow enabled.") from exc
    except GitHubOAuthError as exc:
        # For example incorrect_device_code: this device flow cannot continue.
        _clear_device_state(ctx)
        raise UpstreamError(
            f"GitHub rejected the device flow. Start it again. ({exc})",
            service="github",
            status_code=exc.status_code,
        ) from exc
    except GitHubError as exc:
        raise github_service_error(exc) from exc
    if isinstance(result, DevicePending):
        return DeviceFlowPoll(status="pending", interval=state.interval)
    if isinstance(result, DeviceSlowDown):
        updated = state.model_copy(update={"interval": result.interval})
        with ctx.sessions.begin() as session:
            write_setting(session, DEVICE_FLOW_SETTING, updated.model_dump(mode="json"))
        return DeviceFlowPoll(status="slow_down", interval=result.interval)
    if isinstance(result, DeviceDenied):
        _clear_device_state(ctx)
        return DeviceFlowPoll(status="denied")
    if isinstance(result, DeviceExpired):
        _clear_device_state(ctx)
        return DeviceFlowPoll(status="expired")
    client = GitHubClient(result.access_token, ctx.github_transport, kind=result.kind)
    with github_errors():
        user = client.user()
    account = _save_account(ctx, result, user)
    return DeviceFlowPoll(status="connected", account=account)


# --- personal access token --------------------------------------------------------------


def connect_github_pat(ctx: AppContext, token: SecretStr) -> GitHubAccountView:
    """Connect GitHub with a personal access token that the user pasted.

    Surrounding whitespace is removed. The token is checked with a call to
    ``GET /user`` and then stored encrypted. It replaces a connected account.

    Raises:
        InvalidInputError: The token has characters that no GitHub token has, or
            GitHub rejected it.
        UpstreamError: GitHub failed.
    """
    value = token.get_secret_value().strip()
    try:
        client = GitHubClient(SecretStr(value), ctx.github_transport, kind="pat")
    except ValueError as exc:
        raise InvalidInputError(f"This is not a valid GitHub token. {exc}") from None
    try:
        with github_errors():
            user = client.user()
    except AuthError as exc:
        raise InvalidInputError(
            "GitHub rejected the token. Check that you copied the whole token and that "
            "it did not expire."
        ) from exc
    tokens = TokenSet(kind="pat", access_token=SecretStr(value))
    return _save_account(ctx, tokens, user)


# --- account ----------------------------------------------------------------------------


def get_github_account(ctx: AppContext) -> GitHubAccountView | None:
    """The connected account, or ``None``."""
    with ctx.read_sessions() as session:
        row = _account_row(session)
        return None if row is None else _account_view(row)


def disconnect_github(ctx: AppContext) -> None:
    """Remove the connected account and its stored token. Brains stay in the database.

    The GitHub App authorization stays on GitHub; the user can revoke it there.
    """
    with ctx.sessions.begin() as session:
        _delete_accounts(session)
        delete_setting(session, DEVICE_FLOW_SETTING)


def list_github_repos(ctx: AppContext) -> list[RepoView]:
    """The repositories that the connected account can use, with the Brain of each.

    Raises:
        NotConnectedError: No account is connected.
        AuthError: GitHub rejected the stored token.
        UpstreamError: GitHub failed.
    """
    client = github_client(ctx)
    with github_errors():
        repos = client.list_repos()
    with ctx.read_sessions() as session:
        brains = {
            name.lower(): brain_id
            for brain_id, name in session.execute(select(BrainRow.id, BrainRow.repo_full_name))
        }
    return [
        RepoView(
            full_name=repo.full_name,
            private=repo.private,
            default_branch=repo.default_branch,
            brain_id=brains.get(repo.full_name.lower()),
        )
        for repo in repos
    ]


def github_install_url(ctx: AppContext) -> str:
    """The page where the user installs the GitHub App on repositories.

    Raises:
        NotConfiguredError: ``SBB_GITHUB_APP_SLUG`` is unset.
    """
    slug = ctx.settings.github_app_slug
    if not slug:
        raise NotConfiguredError("SBB_GITHUB_APP_SLUG is not set.")
    return install_url(slug)


# --- accessors for other use cases and the worker -----------------------------------------


def get_github_auth(ctx: AppContext) -> GitHubAuth:
    """Git credentials and commit identity of the connected account.

    Raises:
        NotConnectedError: No account is connected.
        AuthError: The token expired and cannot be refreshed, GitHub rejected the
            refresh, or the stored token cannot be decrypted. Connect again.
        NotConfiguredError: The token must be refreshed, but
            ``SBB_GITHUB_CLIENT_ID`` is unset.
        UpstreamError: GitHub failed during a refresh.
    """
    account, tokens = _fresh_tokens(ctx)
    return GitHubAuth(
        auth=GitAuth(token=tokens.access_token),
        identity=GitIdentity(name=account.login, email=account.noreply_email),
        login=account.login,
        token_kind=tokens.kind,
    )


def github_client(ctx: AppContext) -> GitHubClient:
    """A ``GitHubClient`` with the connected account's token (refreshed if needed).

    Raises the same errors as ``get_github_auth``.
    """
    _, tokens = _fresh_tokens(ctx)
    return GitHubClient(tokens.access_token, ctx.github_transport, kind=tokens.kind)


# --- token storage and refresh ------------------------------------------------------------


def _fresh_tokens(ctx: AppContext) -> tuple[_Account, TokenSet]:
    account, tokens, ciphertext = _read_tokens(ctx)
    if not tokens.expires_within(TOKEN_REFRESH_MARGIN, now=ctx.clock()):
        return account, tokens
    with ctx.github_refresh_lock:
        # Another thread may have refreshed while this one waited.
        account, tokens, ciphertext = _read_tokens(ctx)
        if not tokens.expires_within(TOKEN_REFRESH_MARGIN, now=ctx.clock()):
            return account, tokens
        return _refresh(ctx, account, tokens, ciphertext)


def _refresh(
    ctx: AppContext, account: _Account, tokens: TokenSet, ciphertext: bytes
) -> tuple[_Account, TokenSet]:
    now = ctx.clock()
    refresh_expired = tokens.refresh_expires_at is not None and tokens.refresh_expires_at <= now
    if tokens.refresh_token is None or refresh_expired:
        if tokens.expires_at is not None and tokens.expires_at > now:
            return account, tokens  # still valid for a short time; nothing can renew it
        raise AuthError(f"The GitHub authorization expired. {_RECONNECT}", service="github")
    client_id = ctx.settings.github_client_id
    if not client_id:
        raise NotConfiguredError(
            "The GitHub token must be refreshed, but SBB_GITHUB_CLIENT_ID is not set."
        )
    try:
        refreshed = refresh_token(client_id, tokens, transport=ctx.github_transport)
    except GitHubOAuthError as exc:
        # GitHub revokes a refresh token when it is used. If another process
        # refreshed first, this one fails, and that process stores new tokens.
        current = _wait_for_new_tokens(ctx, ciphertext)
        if current is not None:
            return current
        raise AuthError(
            f"GitHub did not renew the authorization. {_RECONNECT}", service="github"
        ) from exc
    except GitHubError as exc:
        raise github_service_error(exc) from exc
    with ctx.sessions.begin() as session:
        row = session.get(GitHubAccountRow, account.user_id)
        credential = None if row is None else session.get(CredentialRow, row.credential_id)
        if row is None or credential is None:
            raise NotConnectedError("GitHub was disconnected during a token refresh.")
        credential.ciphertext = _encrypt_tokens(ctx, refreshed)
        credential.updated_at = ctx.clock()
        row.expires_at = refreshed.expires_at
    _log.info("Refreshed the GitHub token of %s.", account.login)
    return account, refreshed


def _wait_for_new_tokens(ctx: AppContext, ciphertext: bytes) -> tuple[_Account, TokenSet] | None:
    """The stored account and tokens once they differ from ``ciphertext``.

    Returns ``None`` when they did not change within ``REFRESH_RACE_WAIT_SECONDS``.
    """
    deadline = time.monotonic() + REFRESH_RACE_WAIT_SECONDS
    while True:
        account, tokens, current = _read_tokens(ctx)
        if current != ciphertext:
            return account, tokens
        if time.monotonic() >= deadline:
            return None
        time.sleep(_REFRESH_RACE_POLL_SECONDS)


def _read_tokens(ctx: AppContext) -> tuple[_Account, TokenSet, bytes]:
    with ctx.read_sessions() as session:
        row = _account_row(session)
        if row is None:
            raise NotConnectedError("GitHub is not connected. Connect it in the settings.")
        credential = session.get(CredentialRow, row.credential_id)
        if credential is None:  # the foreign key forbids it
            raise NotConnectedError("GitHub is not connected. Connect it in the settings.")
        account = _Account(
            user_id=row.user_id,
            login=row.login,
            noreply_email=row.noreply_email,
            token_kind=_token_kind(row.token_kind),
            credential_id=row.credential_id,
        )
        ciphertext = credential.ciphertext
    return account, _decrypt_tokens(ctx, ciphertext), ciphertext


def _save_account(ctx: AppContext, tokens: TokenSet, user: GitHubUser) -> GitHubAccountView:
    now = ctx.clock()
    with ctx.sessions.begin() as session:
        _delete_accounts(session)
        credential = CredentialRow(
            kind=GITHUB_CREDENTIAL_KIND,
            ciphertext=_encrypt_tokens(ctx, tokens),
            created_at=now,
            updated_at=now,
        )
        session.add(credential)
        session.flush()
        row = GitHubAccountRow(
            user_id=user.id,
            login=user.login,
            noreply_email=user.noreply_email,
            token_kind=tokens.kind,
            credential_id=credential.id,
            expires_at=tokens.expires_at,
            connected_at=now,
        )
        session.add(row)
        delete_setting(session, DEVICE_FLOW_SETTING)
        session.flush()
        return _account_view(row)


def _delete_accounts(session: Session) -> None:
    for row in session.scalars(select(GitHubAccountRow)).all():
        credential_id = row.credential_id
        session.delete(row)
        session.flush()  # the credential is RESTRICTed while the account refers to it
        credential = session.get(CredentialRow, credential_id)
        if credential is not None:
            session.delete(credential)
    session.flush()


def _account_row(session: Session) -> GitHubAccountRow | None:
    statement = select(GitHubAccountRow).order_by(GitHubAccountRow.connected_at.desc())
    return session.scalars(statement).first()


def _account_view(row: GitHubAccountRow) -> GitHubAccountView:
    return GitHubAccountView(
        user_id=row.user_id,
        login=row.login,
        noreply_email=row.noreply_email,
        token_kind=_token_kind(row.token_kind),
        expires_at=row.expires_at,
        connected_at=row.connected_at,
    )


def _token_kind(value: str) -> TokenKind:
    if value == "app":
        return "app"
    return "pat"


def _encrypt_tokens(ctx: AppContext, tokens: TokenSet) -> bytes:
    stored = _StoredTokens(
        kind=tokens.kind,
        access_token=tokens.access_token.get_secret_value(),
        expires_at=tokens.expires_at,
        refresh_token=(
            None if tokens.refresh_token is None else tokens.refresh_token.get_secret_value()
        ),
        refresh_expires_at=tokens.refresh_expires_at,
    )
    return ctx.crypto.encrypt(stored.model_dump_json())


def _decrypt_tokens(ctx: AppContext, ciphertext: bytes) -> TokenSet:
    try:
        stored = _StoredTokens.model_validate_json(ctx.crypto.decrypt(ciphertext))
    except (DecryptionError, ValidationError):
        raise AuthError(
            f"The stored GitHub token cannot be read (the secret key changed?). {_RECONNECT}",
            service="github",
        ) from None
    return TokenSet(
        kind=stored.kind,
        access_token=SecretStr(stored.access_token),
        expires_at=stored.expires_at,
        refresh_token=None if stored.refresh_token is None else SecretStr(stored.refresh_token),
        refresh_expires_at=stored.refresh_expires_at,
    )


def _client_id(ctx: AppContext) -> str:
    client_id = ctx.settings.github_client_id
    if not client_id:
        raise NotConfiguredError(
            "SBB_GITHUB_CLIENT_ID is not set, so the device flow cannot start. "
            "Connect with a personal access token instead."
        )
    return client_id


def _read_device_state(ctx: AppContext) -> _DeviceFlowState | None:
    with ctx.read_sessions() as session:
        value = read_setting(session, DEVICE_FLOW_SETTING)
    if value is None:
        return None
    try:
        return _DeviceFlowState.model_validate(value)
    except ValidationError:
        return None


def _clear_device_state(ctx: AppContext) -> None:
    with ctx.sessions.begin() as session:
        delete_setting(session, DEVICE_FLOW_SETTING)
