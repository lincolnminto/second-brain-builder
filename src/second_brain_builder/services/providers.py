"""LLM Providers, their encrypted API keys, and the task settings.

API keys are stored encrypted in ``credentials`` and never leave this module in
plain text, except inside the ``ProviderConfig`` that the clients need. Views
show only the last four characters of a key.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from second_brain_builder.domain import TaskMode, TaskName
from second_brain_builder.providers import (
    ChatModel,
    ProviderAuthError,
    ProviderConfig,
    ProviderError,
    ProviderKind,
    Transcriber,
    list_models,
    make_chat_model,
    make_transcriber,
)
from second_brain_builder.services._common import one_line_name
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import (
    AuthError,
    ConflictError,
    InvalidInputError,
    NotConfiguredError,
    NotFoundError,
    UpstreamError,
)
from second_brain_builder.services.models import (
    ProviderInput,
    ProviderTestResult,
    ProviderUpdate,
    ProviderView,
    TaskSettingInput,
    TaskSettingsInput,
    TaskSettingsView,
    TaskSettingView,
)
from second_brain_builder.store import (
    CredentialRow,
    DecryptionError,
    ProviderRow,
    SettingRow,
    TaskSettingRow,
)

_log = logging.getLogger(__name__)

PROVIDER_CREDENTIAL_KIND = "provider_api_key"
"""``credentials.kind`` of a Provider API key."""

MASK_MIN_KEY_LENGTH = 8
"""A key shorter than this shows no characters at all, so it is never shown in full."""

MASK_VISIBLE_CHARS = 4

MAX_MODEL_LENGTH = 200


@dataclass(frozen=True)
class IngestModel:
    """The chat model for Ingest, with the model name for ``generated.by``."""

    chat_model: ChatModel
    model_name: str
    provider_id: int


# --- Providers ---------------------------------------------------------------------------


def list_providers(ctx: AppContext) -> list[ProviderView]:
    """Every Provider, sorted by name."""
    with ctx.read_sessions() as session:
        rows = session.scalars(select(ProviderRow).order_by(ProviderRow.name)).all()
        return [_view(ctx, session, row) for row in rows]


def get_provider(ctx: AppContext, provider_id: int) -> ProviderView:
    with ctx.read_sessions() as session:
        return _view(ctx, session, _row(session, provider_id))


def create_provider(ctx: AppContext, data: ProviderInput) -> ProviderView:
    """Add a Provider. A non-empty API key is stored encrypted.

    Raises:
        InvalidInputError: The name is empty, the base URL is not an absolute
            http(s) URL, or the key has characters that cannot go in a header.
        ConflictError: Another Provider has the same name.
    """
    name = one_line_name(data.name, "Provider name")
    config = _config(data.kind, data.base_url, data.api_key)
    key = config.api_key.get_secret_value()
    with ctx.sessions.begin() as session:
        _check_unique_name(session, name, None)
        credential_id = _add_credential(ctx, session, key) if key else None
        row = ProviderRow(
            name=name,
            kind=config.kind.value,
            base_url=config.base_url,
            credential_id=credential_id,
            created_at=ctx.clock(),
        )
        session.add(row)
        session.flush()
        return _view(ctx, session, row)


def update_provider(ctx: AppContext, provider_id: int, data: ProviderUpdate) -> ProviderView:
    """Change a Provider. Fields that are ``None`` stay; an empty ``api_key`` removes the key.

    A stored key goes only to the endpoint that it was entered for: a change of
    the kind or the base URL needs ``api_key`` in the same request. Otherwise a
    client that cannot read the key could send it to another host with
    ``check_provider``.

    Raises:
        NotFoundError: No such Provider.
        InvalidInputError: A value is not valid, the kind or base URL changes
            without ``api_key`` while a key is stored, or the kind changes to
            Anthropic while transcription uses the Provider.
        ConflictError: Another Provider has the new name.
    """
    with ctx.sessions.begin() as session:
        row = _row(session, provider_id)
        name = row.name if data.name is None else one_line_name(data.name, "Provider name")
        kind = ProviderKind(row.kind) if data.kind is None else data.kind
        base_url = row.base_url if data.base_url is None else data.base_url
        new_key = data.api_key if data.api_key is not None else SecretStr("")
        config = _config(kind, base_url, new_key)
        _check_unique_name(session, name, row.id)
        if config.kind is not ProviderKind.OPENAI and _transcribes_with(session, row.id):
            raise InvalidInputError(
                "Transcription uses this Provider, and transcription needs an "
                "OpenAI-compatible Provider. Change the transcription setting first."
            )
        endpoint_changed = config.kind.value != row.kind or config.base_url != row.base_url
        if endpoint_changed and data.api_key is None and row.credential_id is not None:
            raise InvalidInputError(
                "Enter the API key again when you change the kind or the base URL of a "
                "Provider. The stored key is sent only to the endpoint it was entered for."
            )
        row.name = name
        row.kind = config.kind.value
        row.base_url = config.base_url
        if data.api_key is not None:
            _replace_key(ctx, session, row, config.api_key.get_secret_value())
        session.flush()
        return _view(ctx, session, row)


def delete_provider(ctx: AppContext, provider_id: int) -> None:
    """Delete a Provider and its key. Task settings that use it go back to their defaults.

    Raises:
        NotFoundError: No such Provider.
    """
    with ctx.sessions.begin() as session:
        row = _row(session, provider_id)
        for setting in session.scalars(
            select(TaskSettingRow).where(TaskSettingRow.provider_id == row.id)
        ).all():
            session.delete(setting)
        credential_id = row.credential_id
        session.delete(row)
        session.flush()
        if credential_id is not None:
            credential = session.get(CredentialRow, credential_id)
            if credential is not None:
                session.delete(credential)


def check_provider(ctx: AppContext, provider_id: int) -> ProviderTestResult:
    """Test a Provider with one call that lists its models.

    A failed call is a result with ``ok=False`` and the reason, not an error.

    Raises:
        NotFoundError: No such Provider.
    """
    try:
        config = provider_config(ctx, provider_id)
    except AuthError as exc:
        return ProviderTestResult(ok=False, message=exc.message)
    try:
        models = list_models(config, transport=ctx.provider_transport)
    except ProviderError as exc:
        return ProviderTestResult(ok=False, message=str(exc))
    return ProviderTestResult(
        ok=True,
        message=f"The Provider answered with {len(models)} models.",
        model_count=len(models),
    )


def list_provider_models(ctx: AppContext, provider_id: int) -> list[str]:
    """The model ids that the Provider offers.

    Raises:
        NotFoundError: No such Provider.
        AuthError: The Provider rejected the key, or the key cannot be decrypted.
        UpstreamError: The Provider failed.
    """
    config = provider_config(ctx, provider_id)
    try:
        return list_models(config, transport=ctx.provider_transport)
    except ProviderAuthError as exc:
        raise AuthError(str(exc), service="provider") from exc
    except ProviderError as exc:
        raise UpstreamError(str(exc), service="provider", status_code=exc.status_code) from exc


def provider_config(ctx: AppContext, provider_id: int) -> ProviderConfig:
    """The config of a Provider, with its decrypted API key.

    Raises:
        NotFoundError: No such Provider.
        AuthError: The stored key cannot be decrypted (the secret key changed).
    """
    with ctx.read_sessions() as session:
        row = _row(session, provider_id)
        return _row_config(ctx, session, row)


# --- task settings -------------------------------------------------------------------------


def get_task_settings(ctx: AppContext) -> TaskSettingsView:
    """Where Ingest and transcription run."""
    with ctx.read_sessions() as session:
        rows = {row.task: row for row in session.scalars(select(TaskSettingRow))}
        kinds = _provider_kinds(session)
    return TaskSettingsView(
        ingest=_task_view(TaskName.INGEST, rows.get(TaskName.INGEST), kinds),
        transcription=_task_view(TaskName.TRANSCRIPTION, rows.get(TaskName.TRANSCRIPTION), kinds),
    )


def put_task_settings(ctx: AppContext, data: TaskSettingsInput) -> TaskSettingsView:
    """Replace the task settings. ``None`` resets a task to its default.

    Raises:
        InvalidInputError: Ingest without a Provider and model, a Provider that
            does not exist, a model name that is empty, or transcription with a
            Provider that is not OpenAI-compatible.
    """
    with ctx.sessions.begin() as session:
        kinds = _provider_kinds(session)
        rows: list[TaskSettingRow] = []
        if data.ingest is not None:
            rows.append(_ingest_row(data.ingest, kinds))
        if data.transcription is not None:
            rows.append(_transcription_row(data.transcription, kinds))
        for old in session.scalars(select(TaskSettingRow)).all():
            session.delete(old)
        session.flush()
        session.add_all(rows)
    return get_task_settings(ctx)


def ingest_configured(session: Session) -> bool:
    """True when an ingest Provider and model are set."""
    row = session.get(TaskSettingRow, TaskName.INGEST)
    return (
        row is not None
        and row.mode is TaskMode.PROVIDER
        and row.provider_id is not None
        and bool(row.model)
    )


def get_ingest_model(ctx: AppContext) -> IngestModel | None:
    """The chat model for Ingest, or ``None`` when no ingest Provider is set.

    Raises:
        AuthError: The Provider's key cannot be decrypted.
    """
    with ctx.read_sessions() as session:
        row = session.get(TaskSettingRow, TaskName.INGEST)
        if row is None or row.mode is not TaskMode.PROVIDER or not row.model:
            return None
        provider = None if row.provider_id is None else session.get(ProviderRow, row.provider_id)
        if provider is None:
            return None
        config = _row_config(ctx, session, provider)
        model_name = row.model
        provider_id = provider.id
    if ctx.chat_model_factory is not None:
        chat_model = ctx.chat_model_factory(config, model_name)
    elif config.kind is ProviderKind.ANTHROPIC:
        from second_brain_builder.providers._anthropic import AnthropicChatModel

        cache_key = f"provider.{provider_id}.cache_control_unsupported"
        with ctx.read_sessions() as session:
            unsupported = session.get(SettingRow, cache_key)
            cache_supported = unsupported is None or unsupported.value is not True

        def remember_unsupported() -> None:
            with ctx.sessions.begin() as session:
                session.merge(SettingRow(key=cache_key, value=True))

        chat_model = AnthropicChatModel(
            config,
            model_name,
            ctx.provider_transport,
            cache_supported=cache_supported,
            on_cache_unsupported=remember_unsupported,
        )
    else:
        chat_model = make_chat_model(config, model_name, transport=ctx.provider_transport)
    return IngestModel(chat_model=chat_model, model_name=model_name, provider_id=provider_id)


def get_transcriber(ctx: AppContext) -> Transcriber | None:
    """The Provider transcriber, or ``None`` when transcription runs locally (Whisper).

    Raises:
        AuthError: The Provider's key cannot be decrypted.
        NotConfiguredError: The Provider is not OpenAI-compatible.
    """
    with ctx.read_sessions() as session:
        row = session.get(TaskSettingRow, TaskName.TRANSCRIPTION)
        if row is None or row.mode is not TaskMode.PROVIDER or not row.model:
            return None
        provider = None if row.provider_id is None else session.get(ProviderRow, row.provider_id)
        if provider is None:
            return None
        config = _row_config(ctx, session, provider)
        model_name = row.model
    try:
        return make_transcriber(config, model_name, transport=ctx.provider_transport)
    except ValueError as exc:
        raise NotConfiguredError(
            "Transcription needs an OpenAI-compatible Provider. Change the transcription setting."
        ) from exc


# --- helpers ---------------------------------------------------------------------------------


def _row(session: Session, provider_id: int) -> ProviderRow:
    row = session.get(ProviderRow, provider_id)
    if row is None:
        raise NotFoundError(f"Provider {provider_id} does not exist.")
    return row


def _config(kind: ProviderKind, base_url: str, api_key: SecretStr) -> ProviderConfig:
    try:
        return ProviderConfig(kind=kind, base_url=base_url, api_key=api_key)
    except ValidationError as exc:
        # The model hides input values in its errors, so no key is in the messages.
        problems = "; ".join(
            str(error["msg"]).removeprefix("Value error, ")
            for error in exc.errors(include_url=False, include_input=False, include_context=False)
        )
        raise InvalidInputError(f"The Provider settings are not valid: {problems}.") from None


def _check_unique_name(session: Session, name: str, own_id: int | None) -> None:
    statement = select(ProviderRow.id).where(func.lower(ProviderRow.name) == name.lower())
    if own_id is not None:
        statement = statement.where(ProviderRow.id != own_id)
    if session.scalar(statement) is not None:
        raise ConflictError(f"A Provider named {name!r} exists already.")


def _add_credential(ctx: AppContext, session: Session, key: str) -> int:
    now = ctx.clock()
    credential = CredentialRow(
        kind=PROVIDER_CREDENTIAL_KIND,
        ciphertext=ctx.crypto.encrypt(key),
        created_at=now,
        updated_at=now,
    )
    session.add(credential)
    session.flush()
    return credential.id


def _replace_key(ctx: AppContext, session: Session, row: ProviderRow, key: str) -> None:
    credential = (
        None if row.credential_id is None else session.get(CredentialRow, row.credential_id)
    )
    if not key:
        row.credential_id = None
        session.flush()
        if credential is not None:
            session.delete(credential)
        return
    if credential is None:
        row.credential_id = _add_credential(ctx, session, key)
        return
    credential.ciphertext = ctx.crypto.encrypt(key)
    credential.updated_at = ctx.clock()


def _stored_key(ctx: AppContext, session: Session, row: ProviderRow) -> str:
    """The decrypted key, ``""`` without one. Raises ``DecryptionError``."""
    if row.credential_id is None:
        return ""
    credential = session.get(CredentialRow, row.credential_id)
    return "" if credential is None else ctx.crypto.decrypt(credential.ciphertext)


def _row_config(ctx: AppContext, session: Session, row: ProviderRow) -> ProviderConfig:
    try:
        key = _stored_key(ctx, session, row)
    except DecryptionError:
        raise AuthError(
            f"The API key of Provider {row.name!r} cannot be decrypted (the secret key "
            "changed?). Enter the key again.",
            service="provider",
        ) from None
    return ProviderConfig(
        kind=ProviderKind(row.kind), base_url=row.base_url, api_key=SecretStr(key)
    )


def _view(ctx: AppContext, session: Session, row: ProviderRow) -> ProviderView:
    last4: str | None = None
    has_key = row.credential_id is not None
    if has_key:
        try:
            key = _stored_key(ctx, session, row)
        except DecryptionError:
            key = ""
            _log.warning("The API key of Provider %d cannot be decrypted.", row.id)
        if len(key) >= MASK_MIN_KEY_LENGTH:
            last4 = key[-MASK_VISIBLE_CHARS:]
    return ProviderView(
        id=row.id,
        name=row.name,
        kind=ProviderKind(row.kind),
        base_url=row.base_url,
        has_api_key=has_key,
        api_key_last4=last4,
        created_at=row.created_at,
    )


def _provider_kinds(session: Session) -> dict[int, ProviderKind]:
    return {
        provider_id: ProviderKind(kind)
        for provider_id, kind in session.execute(select(ProviderRow.id, ProviderRow.kind))
    }


def _transcribes_with(session: Session, provider_id: int) -> bool:
    row = session.get(TaskSettingRow, TaskName.TRANSCRIPTION)
    return row is not None and row.mode is TaskMode.PROVIDER and row.provider_id == provider_id


def _model_name(value: str | None, required: bool) -> str | None:
    model = (value or "").strip()
    if not model:
        if required:
            raise InvalidInputError("Choose a model.")
        return None
    if len(model) > MAX_MODEL_LENGTH or any(char.isspace() for char in model):
        raise InvalidInputError("A model name has no spaces and at most 200 characters.")
    return model


def _provider_id(value: int | None, kinds: dict[int, ProviderKind]) -> int:
    if value is None:
        raise InvalidInputError("Choose a Provider.")
    if value not in kinds:
        raise InvalidInputError(f"Provider {value} does not exist.")
    return value


def _ingest_row(data: TaskSettingInput, kinds: dict[int, ProviderKind]) -> TaskSettingRow:
    if data.mode is not TaskMode.PROVIDER:
        raise InvalidInputError("Ingest needs an LLM Provider; it cannot run locally.")
    return TaskSettingRow(
        task=TaskName.INGEST,
        mode=TaskMode.PROVIDER,
        provider_id=_provider_id(data.provider_id, kinds),
        model=_model_name(data.model, required=True),
    )


def _transcription_row(data: TaskSettingInput, kinds: dict[int, ProviderKind]) -> TaskSettingRow:
    if data.mode is TaskMode.LOCAL:
        if data.provider_id is not None:
            raise InvalidInputError("Local transcription uses no Provider.")
        return TaskSettingRow(
            task=TaskName.TRANSCRIPTION,
            mode=TaskMode.LOCAL,
            provider_id=None,
            model=_model_name(data.model, required=False),
        )
    provider_id = _provider_id(data.provider_id, kinds)
    if kinds[provider_id] is not ProviderKind.OPENAI:
        raise InvalidInputError("Transcription needs an OpenAI-compatible Provider.")
    return TaskSettingRow(
        task=TaskName.TRANSCRIPTION,
        mode=TaskMode.PROVIDER,
        provider_id=provider_id,
        model=_model_name(data.model, required=True),
    )


def _task_view(
    task: TaskName, row: TaskSettingRow | None, kinds: dict[int, ProviderKind]
) -> TaskSettingView:
    if row is None:
        local = task is TaskName.TRANSCRIPTION
        return TaskSettingView(
            task=task,
            mode=TaskMode.LOCAL if local else TaskMode.PROVIDER,
            provider_id=None,
            model=None,
            configured=local,
        )
    if row.mode is TaskMode.LOCAL:
        configured = task is TaskName.TRANSCRIPTION
    else:
        kind = None if row.provider_id is None else kinds.get(row.provider_id)
        configured = kind is not None and bool(row.model)
        if task is TaskName.TRANSCRIPTION:
            configured = configured and kind is ProviderKind.OPENAI
    return TaskSettingView(
        task=task,
        mode=row.mode,
        provider_id=row.provider_id,
        model=row.model,
        configured=configured,
    )
