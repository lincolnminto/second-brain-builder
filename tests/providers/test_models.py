"""Validation rules of the provider-neutral models."""

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from second_brain_builder.providers import Message, ProviderConfig, ProviderKind


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"role": "tool", "content": "result"}, id="tool-without-call-id"),
        pytest.param(
            {"role": "user", "content": "Hello", "tool_call_id": "call_1"}, id="call-id-on-user"
        ),
        pytest.param(
            {"role": "user", "tool_calls": [{"id": "call_1", "name": "finish"}]},
            id="tool-calls-on-user",
        ),
    ],
)
def test_message_rejects_fields_that_do_not_fit_its_role(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Message.model_validate(fields)


def test_provider_config_normalizes_base_url() -> None:
    config = ProviderConfig(
        kind=ProviderKind.OPENAI, base_url=" http://localhost:11434/v1/ ", api_key=SecretStr("")
    )

    assert config.base_url == "http://localhost:11434/v1"


@pytest.mark.parametrize(
    "base_url",
    [
        "localhost:11434/v1",
        "ftp://llm.example.com",
        "https://",
        # Ports that the SDKs' HTTP client rejects only when the first call is made.
        "http://localhost:11434a/v1",
        "http://localhost:99999/v1",
        "http://localhost:0/v1",
        "http://[::1/v1",
    ],
)
def test_provider_config_rejects_invalid_base_url(base_url: str) -> None:
    with pytest.raises(ValidationError, match="base_url"):
        ProviderConfig(kind=ProviderKind.OPENAI, base_url=base_url, api_key=SecretStr("key"))


def test_provider_config_strips_whitespace_around_api_key() -> None:
    # Keys copied from a terminal or a file often end with a newline.
    config = ProviderConfig(
        kind=ProviderKind.OPENAI,
        base_url="http://localhost:11434/v1",
        api_key=SecretStr(" sbb-synthetic-key\r\n"),
    )

    assert config.api_key.get_secret_value() == "sbb-synthetic-key"


@pytest.mark.parametrize(
    "api_key",
    ["sbb-synthetic\nkey", "Bearer sbb-synthetic-key", "sbb-synth\u00e9tic-key"],
    ids=["newline-inside", "space-inside", "non-ascii"],
)
def test_provider_config_rejects_api_key_that_cannot_be_sent(api_key: str) -> None:
    with pytest.raises(ValidationError, match="api_key") as caught:
        ProviderConfig.model_validate(
            {"kind": "anthropic", "base_url": "https://claude.example.com", "api_key": api_key}
        )

    assert api_key not in str(caught.value)
    assert "sbb-synth" not in str(caught.value)
