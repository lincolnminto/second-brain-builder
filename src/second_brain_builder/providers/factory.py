"""Build Provider clients from a ``ProviderConfig``.

Every function takes an optional ``transport``. Tests pass an
``httpx2.MockTransport``; production code leaves it out. The official SDKs use
``httpx2``, the successor of ``httpx``, so the transport is an ``httpx2`` one;
any other transport raises ``TypeError``.

Retries: connection errors and HTTP 408, 409, 429, and 5xx are retried up to
four times with backoff. A timeout is never retried, because the Provider can
still be generating the answer. Chat and transcription calls wait up to 15
minutes for an answer; ``list_models`` waits up to 30 seconds.
"""

from __future__ import annotations

import httpx2

from second_brain_builder.providers._anthropic import AnthropicChatModel, list_anthropic_models
from second_brain_builder.providers._openai import (
    OpenAIChatModel,
    OpenAITranscriber,
    list_openai_models,
)
from second_brain_builder.providers.models import (
    ChatModel,
    ProviderConfig,
    ProviderKind,
    Transcriber,
)

__all__ = ["list_models", "make_chat_model", "make_transcriber"]


def make_chat_model(
    config: ProviderConfig, model: str, *, transport: httpx2.BaseTransport | None = None
) -> ChatModel:
    """Return a chat model for ``model`` on the Provider in ``config``."""
    if config.kind == ProviderKind.ANTHROPIC:
        return AnthropicChatModel(config, model, transport)
    return OpenAIChatModel(config, model, transport)


def make_transcriber(
    config: ProviderConfig, model: str, *, transport: httpx2.BaseTransport | None = None
) -> Transcriber:
    """Return a transcriber for ``model``. Only OpenAI-compatible Providers transcribe.

    Raises ``ValueError`` for an Anthropic-compatible Provider.
    """
    if config.kind != ProviderKind.OPENAI:
        raise ValueError("Transcription needs an OpenAI-compatible Provider.")
    return OpenAITranscriber(config, model, transport)


def list_models(
    config: ProviderConfig, *, transport: httpx2.BaseTransport | None = None
) -> list[str]:
    """Return the model ids that the Provider offers.

    Raises ``ProviderError`` (or a subclass) when the call fails.
    """
    if config.kind == ProviderKind.ANTHROPIC:
        return list_anthropic_models(config, transport)
    return list_openai_models(config, transport)
