"""Providers, encrypted API keys, task settings, and the model accessors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from second_brain_builder.providers import ChatResponse, Message, ProviderConfig, ToolSpec, Usage
from second_brain_builder.services import (
    AppContext,
    ConflictError,
    InvalidInputError,
    ProviderInput,
    ProviderKind,
    ProviderUpdate,
    TaskMode,
    TaskSettingInput,
    TaskSettingsInput,
    check_provider,
    create_provider,
    delete_provider,
    get_ingest_model,
    get_task_settings,
    get_transcriber,
    list_providers,
    put_task_settings,
    update_provider,
)
from second_brain_builder.services.providers import provider_config
from second_brain_builder.store import CredentialRow

if TYPE_CHECKING:
    from tests.services.conftest import FakeProvider

# Synthetic value, built from parts so secret scanners do not flag a test literal.
API_KEY = "sk-" + "synthetic-key-for-tests-7f3a"
# Synthetic value, built from parts so secret scanners do not flag a test literal.
OTHER_API_KEY = "sk-" + "synthetic-key-for-tests-91c2"
OPENAI_URL = "https://llm.example.com/v1"
ANTHROPIC_URL = "https://claude.example.com"


class ScriptedChatModel:
    """A ChatModel that ends every conversation at once."""

    def __init__(self, config: ProviderConfig, model: str) -> None:
        self.config = config
        self.model = model

    def chat(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        *,
        effort: Literal["low", "medium", "high"] | None = None,
    ) -> ChatResponse:
        return ChatResponse(text="done", usage=Usage(), stop_reason="end_turn")


def openai_input(name: str = "Local LLM", api_key: str = API_KEY) -> ProviderInput:
    return ProviderInput(
        name=name, kind=ProviderKind.OPENAI, base_url=OPENAI_URL, api_key=SecretStr(api_key)
    )


def ciphertexts(ctx: AppContext) -> list[bytes]:
    with ctx.read_sessions() as session:
        return list(session.scalars(select(CredentialRow.ciphertext)))


# --- Providers and key masking --------------------------------------------------------


def test_api_key_is_stored_encrypted_and_shown_as_its_last_four_characters(
    ctx: AppContext,
) -> None:
    view = create_provider(ctx, openai_input())

    assert (view.has_api_key, view.api_key_last4) == (True, "7f3a")
    assert API_KEY not in view.model_dump_json()
    assert API_KEY not in list_providers(ctx)[0].model_dump_json()
    [ciphertext] = ciphertexts(ctx)
    assert API_KEY.encode() not in ciphertext
    assert provider_config(ctx, view.id).api_key.get_secret_value() == API_KEY


def test_short_api_key_shows_no_characters(ctx: AppContext) -> None:
    view = create_provider(ctx, openai_input(api_key="abc123"))

    assert (view.has_api_key, view.api_key_last4) == (True, None)


def test_provider_without_key_stores_no_credential(ctx: AppContext) -> None:
    view = create_provider(ctx, openai_input(api_key="  "))

    assert (view.has_api_key, view.api_key_last4) == (False, None)
    assert ciphertexts(ctx) == []


def test_update_keeps_the_key_unless_a_new_one_is_given(ctx: AppContext) -> None:
    view = create_provider(ctx, openai_input())

    renamed = update_provider(ctx, view.id, ProviderUpdate(name="Renamed"))
    assert renamed.name == "Renamed"
    assert provider_config(ctx, view.id).api_key.get_secret_value() == API_KEY

    removed = update_provider(ctx, view.id, ProviderUpdate(api_key=SecretStr("")))
    assert removed.has_api_key is False
    assert ciphertexts(ctx) == []


def test_a_new_endpoint_needs_the_api_key_again(
    ctx: AppContext, provider_api: FakeProvider
) -> None:
    view = create_provider(ctx, openai_input())
    collector = "https://collector.example.net/v1"

    for change in (
        ProviderUpdate(base_url=collector),
        ProviderUpdate(kind=ProviderKind.ANTHROPIC),
    ):
        with pytest.raises(InvalidInputError, match="API key again"):
            update_provider(ctx, view.id, change)
    provider_api.reply("GET", "/v1/models", {"object": "list", "data": []})
    check_provider(ctx, view.id)
    assert [request.url.host for request in provider_api.requests] == ["llm.example.com"]

    # The same URL in another form is no change. A new URL with a new key is fine.
    same = update_provider(ctx, view.id, ProviderUpdate(base_url=f" {OPENAI_URL}/ "))
    assert (same.base_url, same.api_key_last4) == (OPENAI_URL, "7f3a")
    moved = update_provider(
        ctx, view.id, ProviderUpdate(base_url=collector, api_key=SecretStr(OTHER_API_KEY))
    )
    assert moved.base_url == collector
    assert provider_config(ctx, view.id).api_key.get_secret_value() == OTHER_API_KEY


def test_a_provider_without_a_key_can_move_to_a_new_endpoint(ctx: AppContext) -> None:
    view = create_provider(ctx, openai_input(api_key=""))

    moved = update_provider(ctx, view.id, ProviderUpdate(base_url="http://127.0.0.1:8080/v1"))

    assert (moved.base_url, moved.has_api_key) == ("http://127.0.0.1:8080/v1", False)


def test_invalid_base_url_is_invalid_input_without_the_key(ctx: AppContext) -> None:
    data = ProviderInput(
        name="Broken", kind=ProviderKind.OPENAI, base_url="ftp://x", api_key=SecretStr(API_KEY)
    )

    with pytest.raises(InvalidInputError) as caught:
        create_provider(ctx, data)

    assert API_KEY not in str(caught.value)


def test_provider_names_are_unique_ignoring_case(ctx: AppContext) -> None:
    create_provider(ctx, openai_input("Local LLM"))

    with pytest.raises(ConflictError):
        create_provider(ctx, openai_input("local llm"))


def test_check_provider_lists_the_models(ctx: AppContext, provider_api: FakeProvider) -> None:
    view = create_provider(ctx, openai_input())
    provider_api.reply(
        "GET",
        "/v1/models",
        {"object": "list", "data": [{"id": "chat-large", "object": "model", "created": 0}]},
    )

    result = check_provider(ctx, view.id)

    assert (result.ok, result.model_count) == (True, 1)
    assert provider_api.requests[0].headers["Authorization"] == f"Bearer {API_KEY}"


def test_check_provider_reports_a_rejected_key_without_the_key(
    ctx: AppContext, provider_api: FakeProvider
) -> None:
    view = create_provider(ctx, openai_input())
    provider_api.reply("GET", "/v1/models", {"error": {"message": "invalid key"}}, status=401)

    result = check_provider(ctx, view.id)

    assert result.ok is False
    assert "401" in result.message
    assert API_KEY not in result.message


# --- task settings and model accessors ------------------------------------------------


def test_nothing_is_configured_by_default(ctx: AppContext) -> None:
    settings = get_task_settings(ctx)

    assert settings.ingest.configured is False
    assert (settings.transcription.mode, settings.transcription.configured) == (
        TaskMode.LOCAL,
        True,
    )
    assert get_ingest_model(ctx) is None
    assert get_transcriber(ctx) is None


def test_ingest_model_comes_from_the_task_setting(make_ctx: Any) -> None:
    ctx: AppContext = make_ctx()
    ctx.chat_model_factory = ScriptedChatModel
    view = create_provider(ctx, openai_input())
    put_task_settings(
        ctx,
        TaskSettingsInput(
            ingest=TaskSettingInput(mode=TaskMode.PROVIDER, provider_id=view.id, model="chat-large")
        ),
    )

    ingest = get_ingest_model(ctx)

    assert ingest is not None
    assert (ingest.model_name, ingest.provider_id) == ("chat-large", view.id)
    assert isinstance(ingest.chat_model, ScriptedChatModel)
    assert ingest.chat_model.config.api_key.get_secret_value() == API_KEY
    assert get_task_settings(ctx).ingest.configured is True


def test_ingest_cannot_run_locally(ctx: AppContext) -> None:
    with pytest.raises(InvalidInputError, match="Ingest needs"):
        put_task_settings(
            ctx, TaskSettingsInput(ingest=TaskSettingInput(mode=TaskMode.LOCAL, model="x"))
        )


def test_transcription_needs_an_openai_compatible_provider(ctx: AppContext) -> None:
    anthropic = create_provider(
        ctx,
        ProviderInput(
            name="Claude",
            kind=ProviderKind.ANTHROPIC,
            base_url=ANTHROPIC_URL,
            api_key=SecretStr(""),
        ),
    )

    with pytest.raises(InvalidInputError, match="OpenAI-compatible"):
        put_task_settings(
            ctx,
            TaskSettingsInput(
                transcription=TaskSettingInput(
                    mode=TaskMode.PROVIDER, provider_id=anthropic.id, model="whisper-1"
                )
            ),
        )


def test_transcriber_comes_from_an_openai_provider(ctx: AppContext) -> None:
    view = create_provider(ctx, openai_input())
    put_task_settings(
        ctx,
        TaskSettingsInput(
            transcription=TaskSettingInput(
                mode=TaskMode.PROVIDER, provider_id=view.id, model="whisper-1"
            )
        ),
    )

    assert get_transcriber(ctx) is not None

    with pytest.raises(InvalidInputError, match="Transcription uses this Provider"):
        update_provider(ctx, view.id, ProviderUpdate(kind=ProviderKind.ANTHROPIC))


def test_deleting_a_provider_resets_the_tasks_that_use_it(ctx: AppContext) -> None:
    view = create_provider(ctx, openai_input())
    put_task_settings(
        ctx,
        TaskSettingsInput(
            ingest=TaskSettingInput(mode=TaskMode.PROVIDER, provider_id=view.id, model="chat"),
            transcription=TaskSettingInput(
                mode=TaskMode.PROVIDER, provider_id=view.id, model="whisper-1"
            ),
        ),
    )

    delete_provider(ctx, view.id)

    settings = get_task_settings(ctx)
    assert settings.ingest.configured is False
    assert settings.transcription.mode is TaskMode.LOCAL
    assert get_ingest_model(ctx) is None
    assert ciphertexts(ctx) == []
