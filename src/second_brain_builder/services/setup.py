"""Health, login, and the setup status that the onboarding screens read."""

from __future__ import annotations

import hmac
import logging

from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import AuthError
from second_brain_builder.services.github import get_github_account
from second_brain_builder.services.models import AuthStatus, HealthStatus, SetupStatus, WorkerStatus
from second_brain_builder.services.providers import ingest_configured
from second_brain_builder.services.worker_status import get_worker_status
from second_brain_builder.store import BrainRow, ProviderRow

_log = logging.getLogger(__name__)


def check_health(ctx: AppContext) -> HealthStatus:
    """Whether the database answers, and whether the worker is online."""
    try:
        with ctx.read_sessions() as session:
            session.execute(select(1))
    except SQLAlchemyError as exc:
        _log.error("The database does not answer: %s", exc)
        return HealthStatus(ok=False, database=False, worker=WorkerStatus(online=False))
    return HealthStatus(ok=True, database=True, worker=get_worker_status(ctx))


def get_auth_status(ctx: AppContext) -> AuthStatus:
    return AuthStatus(login_required=ctx.settings.password is not None)


def check_password(ctx: AppContext, password: SecretStr) -> None:
    """Check the web UI password (``SBB_PASSWORD``) in constant time.

    Without ``SBB_PASSWORD`` every password is accepted: the UI needs no login.

    Raises:
        AuthError: The password is wrong (``service="app"``).
    """
    expected = ctx.settings.password
    if expected is None:
        return
    given = password.get_secret_value().encode("utf-8")
    if not hmac.compare_digest(given, expected.get_secret_value().encode("utf-8")):
        raise AuthError("The password is wrong.", service="app")


def get_setup_status(ctx: AppContext) -> SetupStatus:
    """GitHub connection, Brain and Provider counts, Ingest setting, and worker status."""
    account = get_github_account(ctx)
    with ctx.read_sessions() as session:
        brain_count = session.scalar(select(func.count()).select_from(BrainRow)) or 0
        provider_count = session.scalar(select(func.count()).select_from(ProviderRow)) or 0
        ingest = ingest_configured(session)
    return SetupStatus(
        github_connected=account is not None,
        github_login=None if account is None else account.login,
        github_app_configured=bool(ctx.settings.github_client_id),
        brain_count=brain_count,
        provider_count=provider_count,
        ingest_configured=ingest,
        worker=get_worker_status(ctx),
    )
