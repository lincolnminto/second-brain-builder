"""Provider-neutral chat and transcription models.

These models are the contract between ``providers`` and its callers (``ingest``,
``services``, ``worker``). Each Provider client maps them to and from its own API.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from second_brain_builder.domain import Segment, StrEnum, Transcriber, Usage

__all__ = [
    "ChatModel",
    "ChatResponse",
    "Message",
    "ProviderConfig",
    "ProviderKind",
    "Segment",
    "StopReason",
    "ToolCall",
    "ToolSpec",
    "Transcriber",
    "Usage",
]


class ProviderKind(StrEnum):
    """The API that a Provider speaks. The base URL is configurable, so any
    OpenAI-compatible or Anthropic-compatible endpoint works."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class ProviderConfig(BaseModel):
    """How to reach one Provider.

    ``base_url`` is the API root that the official SDK expects: with ``/v1`` for
    OpenAI-compatible endpoints (``https://api.openai.com/v1``,
    ``http://localhost:11434/v1``) and without it for Anthropic-compatible
    endpoints (``https://api.anthropic.com``). An empty ``api_key`` is allowed
    for local servers that need no key.

    Validation strips surrounding whitespace from both fields, so a key pasted
    with a trailing newline works. A key that still holds a space, a control
    character, or a non-ASCII character is rejected: it cannot go in an HTTP
    header. Validation errors never show the input, so they never show the key.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    kind: ProviderKind
    base_url: str
    api_key: SecretStr

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("base_url must be an absolute http:// or https:// URL")
        invalid = "base_url has an invalid port or host"
        try:
            # Reading the port raises ValueError for a port that is not a number
            # from 0 to 65535. httpx2 rejects some URLs that urlsplit accepts.
            port = parts.port
            httpx2.URL(value)
        except (ValueError, httpx2.InvalidURL):
            raise ValueError(invalid) from None
        if port == 0:
            raise ValueError(invalid)
        return value

    @field_validator("api_key")
    @classmethod
    def _check_api_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value().strip()
        if not all("!" <= char <= "~" for char in key):
            raise ValueError("api_key can contain only printable ASCII characters, without spaces")
        return SecretStr(key)


class ToolSpec(BaseModel):
    """A tool that the model can call. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    """One tool call that the model asked for."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """One conversation turn.

    - ``user``: ``content`` is the text.
    - ``assistant``: ``content`` is the text, if any, and ``tool_calls`` holds the
      calls that the model asked for. ``provider_content`` holds opaque blocks
      to replay unchanged when the Provider requires them.
    - ``tool``: the result of one tool call. ``tool_call_id`` names the call and
      ``content`` is the result text.
    """

    role: Literal["user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    provider_content: list[dict[str, Any]] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _check_role_fields(self) -> Message:
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("a tool message needs tool_call_id")
        if self.role != "tool" and self.tool_call_id is not None:
            raise ValueError("only a tool message can have tool_call_id")
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("only an assistant message can have tool_calls")
        if self.role != "assistant" and self.provider_content is not None:
            raise ValueError("only an assistant message can have provider_content")
        return self


class StopReason(StrEnum):
    """Why the model stopped. ``ChatResponse.stop_reason`` holds one of these values.

    Each client maps its API's own values to these. A value that no member covers
    is passed through unchanged.
    """

    END_TURN = "end_turn"
    """The model finished its answer."""
    TOOL_USE = "tool_use"
    """The model wants the results of ``tool_calls``."""
    MAX_TOKENS = "max_tokens"
    """The output was cut off by ``max_tokens`` or by the context window.
    ``tool_calls`` is empty."""
    REFUSAL = "refusal"
    """The model or a content filter of the Provider refused to answer.
    ``tool_calls`` is empty."""


class ChatResponse(BaseModel):
    """The model's answer to one ``ChatModel.chat`` call.

    When ``stop_reason`` is ``max_tokens`` or ``refusal``, ``tool_calls`` is
    empty for every Provider kind: a tool call in an answer that was cut off or
    refused can be incomplete, so no client returns it. ``text`` keeps the part
    of the answer that the model wrote.
    """

    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage
    stop_reason: str
    provider_content: list[dict[str, Any]] | None = Field(default=None, repr=False)

    def to_message(self) -> Message:
        """Return this answer as the assistant message for the next ``chat`` call."""
        return Message(
            role="assistant",
            content=self.text,
            tool_calls=list(self.tool_calls),
            provider_content=self.provider_content,
        )


class ChatModel(Protocol):
    """A chat model with tool calls, bound to one Provider and one model name."""

    def chat(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> ChatResponse:
        """Send one request and return the answer.

        Raises ``ProviderError`` (or a subclass) when the call fails.
        """
        ...
