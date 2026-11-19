"""LLM Provider clients (OpenAI- and Anthropic-compatible) and transcription.

``make_chat_model`` returns a ``ChatModel`` for either API family,
``make_transcriber`` returns a ``Transcriber`` for an OpenAI-compatible Provider,
and ``list_models`` lists the models of a Provider. Failed calls raise
``ProviderError`` or one of its subclasses. Error messages never contain the API key.
"""

from second_brain_builder.providers.errors import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderNotFoundError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from second_brain_builder.providers.factory import list_models, make_chat_model, make_transcriber
from second_brain_builder.providers.models import (
    ChatModel,
    ChatResponse,
    Message,
    ProviderConfig,
    ProviderKind,
    Segment,
    StopReason,
    ToolCall,
    ToolSpec,
    Transcriber,
    Usage,
)

__all__ = [
    "ChatModel",
    "ChatResponse",
    "Message",
    "ProviderAuthError",
    "ProviderConfig",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderKind",
    "ProviderNotFoundError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "Segment",
    "StopReason",
    "ToolCall",
    "ToolSpec",
    "Transcriber",
    "Usage",
    "list_models",
    "make_chat_model",
    "make_transcriber",
]
