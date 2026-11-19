"""OpenAI-compatible Provider: chat completions, audio transcriptions, and models."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import httpx2
import openai
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionFunctionToolParam,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
)
from pydantic import BaseModel, Field, SecretStr

from second_brain_builder.providers._common import (
    CUT_OFF_STOP_REASONS,
    LIST_MODELS_TIMEOUT,
    REQUEST_TIMEOUT,
    Failure,
    make_http_client,
    parse_response,
    redact_errors,
    send_with_retries,
    should_retry_status,
)
from second_brain_builder.providers.errors import (
    ProviderError,
    ProviderResponseError,
    connection_error,
    status_error,
)
from second_brain_builder.providers.models import (
    ChatResponse,
    Message,
    ProviderConfig,
    Segment,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)

ResultT = TypeVar("ResultT")

_FINISH_REASONS: dict[str, str] = {
    "stop": StopReason.END_TURN.value,
    "length": StopReason.MAX_TOKENS.value,
    "tool_calls": StopReason.TOOL_USE.value,
    "function_call": StopReason.TOOL_USE.value,
    "content_filter": StopReason.REFUSAL.value,
}

# Sent as the bearer token when the Provider has no API key.
_NO_API_KEY = "no-key"


class _Client:
    """An OpenAI SDK client that raises ``ProviderError`` instead of SDK errors."""

    def __init__(
        self,
        config: ProviderConfig,
        transport: httpx2.BaseTransport | None,
        timeout: httpx2.Timeout = REQUEST_TIMEOUT,
    ) -> None:
        self._api_key: SecretStr = config.api_key
        self._base_url = config.base_url
        self.sdk = openai.OpenAI(
            # The SDK refuses an empty key, but local servers need none.
            api_key=config.api_key.get_secret_value() or _NO_API_KEY,
            base_url=config.base_url,
            timeout=timeout,
            # send() retries, because the SDK would also retry timeouts.
            max_retries=0,
            http_client=make_http_client(transport, timeout),
        )

    def send(self, request: Callable[[], ResultT]) -> ResultT:
        """Run ``request``, retry what can be retried, and translate SDK errors.

        No SDK error is chained to the ``ProviderError`` that it raises.
        """
        return redact_errors(
            lambda: send_with_retries(request, openai.APIError, self._translate), self._api_key
        )

    def _translate(self, exc: Exception) -> Failure:
        if isinstance(exc, openai.APIStatusError):
            return Failure(
                status_error(exc.status_code, exc.body, self._api_key),
                retry=should_retry_status(exc.response),
                headers=exc.response.headers,
            )
        if isinstance(exc, openai.APITimeoutError):
            error = connection_error(self._base_url, timed_out=True, api_key=self._api_key)
            return Failure(error, retry=False)
        if isinstance(exc, openai.APIConnectionError):
            error = connection_error(self._base_url, timed_out=False, api_key=self._api_key)
            return Failure(error, retry=True)
        if isinstance(exc, openai.APIResponseValidationError):
            message = "The Provider answer does not have the expected format."
            return Failure(ProviderResponseError(message, status_code=exc.status_code), retry=False)
        return Failure(ProviderError("The Provider call failed."), retry=False)


# --- chat --------------------------------------------------------------------------


class _FunctionCall(BaseModel):
    name: str
    # The API sends a JSON string. Some compatible servers send an object.
    arguments: str | dict[str, Any] | None = None


class _ToolCall(BaseModel):
    id: str | None = None
    type: str = "function"
    function: _FunctionCall | None = None


class _AssistantMessage(BaseModel):
    content: str | None = None
    refusal: str | None = None
    tool_calls: list[_ToolCall] | None = None


class _Choice(BaseModel):
    message: _AssistantMessage
    finish_reason: str | None = None


class _CompletionUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class _Completion(BaseModel):
    choices: list[_Choice] = Field(min_length=1)
    usage: _CompletionUsage | None = None


class OpenAIChatModel:
    """``ChatModel`` for the chat completions API of an OpenAI-compatible Provider."""

    def __init__(
        self, config: ProviderConfig, model: str, transport: httpx2.BaseTransport | None = None
    ) -> None:
        self._client = _Client(config, transport)
        self._model = model
        # OpenAI reasoning models reject max_tokens. Many compatible servers know
        # only max_tokens. Start with max_tokens and switch once if it is rejected.
        self._use_max_completion_tokens = False

    def chat(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> ChatResponse:
        request_messages = to_openai_messages(system, messages)
        request_tools = [to_openai_tool(tool) for tool in tools]
        return self._client.send(
            lambda: from_openai_completion(
                self._create(request_messages, request_tools, max_tokens)
            )
        )

    def _create(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[ChatCompletionFunctionToolParam],
        max_tokens: int,
    ) -> _Completion:
        completions = self._client.sdk.chat.completions.with_raw_response
        tools_param = tools if tools else openai.omit
        if not self._use_max_completion_tokens:
            try:
                raw = completions.create(
                    model=self._model, messages=messages, tools=tools_param, max_tokens=max_tokens
                )
                return parse_response(_Completion, raw.content, raw.status_code)
            except openai.BadRequestError as exc:
                if exc.param != "max_tokens" or exc.code != "unsupported_parameter":
                    raise
            self._use_max_completion_tokens = True
        raw = completions.create(
            model=self._model,
            messages=messages,
            tools=tools_param,
            max_completion_tokens=max_tokens,
        )
        return parse_response(_Completion, raw.content, raw.status_code)


def to_openai_messages(system: str, messages: list[Message]) -> list[ChatCompletionMessageParam]:
    """Map the system prompt and the conversation to chat completion messages."""
    result: list[ChatCompletionMessageParam] = []
    if system:
        result.append({"role": "system", "content": system})
    for message in messages:
        if message.role == "user":
            result.append({"role": "user", "content": message.content or ""})
        elif message.role == "tool":
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id or "",
                    "content": message.content or "",
                }
            )
        else:
            result.append(_assistant_message(message))
    return result


def _assistant_message(message: Message) -> ChatCompletionAssistantMessageParam:
    if not message.tool_calls:
        return {"role": "assistant", "content": message.content or ""}
    tool_calls: list[ChatCompletionMessageFunctionToolCallParam] = [
        {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }
        for call in message.tool_calls
    ]
    return {"role": "assistant", "content": message.content, "tool_calls": tool_calls}


def to_openai_tool(tool: ToolSpec) -> ChatCompletionFunctionToolParam:
    """Map a tool to a chat completion function tool."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def from_openai_completion(completion: _Completion) -> ChatResponse:
    """Map the first choice of a chat completion to a ``ChatResponse``.

    An answer that was cut off or refused has no tool calls: see ``ChatResponse``.
    """
    choice = completion.choices[0]
    message = choice.message
    calls = [
        (index, call.id, call.function)
        for index, call in enumerate(message.tool_calls or [])
        if call.type == "function" and call.function is not None
    ]
    refused = message.refusal is not None
    stop_reason = _stop_reason(choice.finish_reason, bool(calls), refused)
    tool_calls: list[ToolCall] = []
    if stop_reason not in CUT_OFF_STOP_REASONS:
        for index, call_id, function in calls:
            arguments = _parse_arguments(function.arguments)
            if arguments is None:
                raise ProviderResponseError(
                    "The model called a tool with arguments that are not a JSON object."
                )
            tool_calls.append(
                ToolCall(id=call_id or f"call_{index}", name=function.name, arguments=arguments)
            )
    usage = completion.usage
    return ChatResponse(
        text=message.content or (message.refusal if refused else None),
        tool_calls=tool_calls,
        usage=Usage(
            input_tokens=(usage.prompt_tokens or 0) if usage else 0,
            output_tokens=(usage.completion_tokens or 0) if usage else 0,
        ),
        stop_reason=stop_reason,
    )


def _parse_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the arguments as a dict, or None if they are not a JSON object."""
    if isinstance(raw, dict):
        return raw
    if raw is None or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _stop_reason(finish_reason: str | None, has_tool_calls: bool, refused: bool) -> str:
    if refused:
        return StopReason.REFUSAL.value
    if finish_reason is None:
        return StopReason.TOOL_USE.value if has_tool_calls else StopReason.END_TURN.value
    reason = _FINISH_REASONS.get(finish_reason, finish_reason)
    # Some compatible servers report "stop" for an answer with tool calls.
    if reason == StopReason.END_TURN.value and has_tool_calls:
        return StopReason.TOOL_USE.value
    return reason


# --- transcription -----------------------------------------------------------------


class _VerboseSegment(BaseModel):
    start: float
    end: float
    text: str


class _VerboseTranscription(BaseModel):
    text: str = ""
    duration: float | None = None
    segments: list[_VerboseSegment] | None = None


class OpenAITranscriber:
    """``Transcriber`` for the audio transcriptions API of an OpenAI-compatible Provider.

    The model must support ``response_format="verbose_json"`` (for OpenAI:
    ``whisper-1``). Providers limit the upload size (OpenAI: 25 MB).
    """

    def __init__(
        self, config: ProviderConfig, model: str, transport: httpx2.BaseTransport | None = None
    ) -> None:
        self._client = _Client(config, transport)
        self._model = model

    def transcribe(self, audio: Path, language: str | None) -> list[Segment]:
        """Transcribe ``audio``. ``language`` is BCP 47; only its language subtag is sent."""
        transcriptions = self._client.sdk.audio.transcriptions.with_raw_response

        def request() -> _VerboseTranscription:
            raw = transcriptions.create(
                file=audio,
                model=self._model,
                response_format="verbose_json",
                language=whisper_language(language) if language else openai.omit,
            )
            return parse_response(_VerboseTranscription, raw.content, raw.status_code)

        return to_segments(self._client.send(request))


def whisper_language(language: str) -> str:
    """Return the ISO 639 code that Whisper expects: ``pt-BR`` becomes ``pt``."""
    return language.split("-")[0].strip().lower()


def to_segments(transcription: _VerboseTranscription) -> list[Segment]:
    """Map a verbose transcription to segments. Empty segments are left out.

    A server that sends no segments gives one segment with the full text.
    """
    if transcription.segments:
        return [
            Segment(start=segment.start, end=segment.end, text=segment.text.strip())
            for segment in transcription.segments
            if segment.text.strip()
        ]
    text = transcription.text.strip()
    if not text:
        return []
    return [Segment(start=0.0, end=transcription.duration or 0.0, text=text)]


# --- models ------------------------------------------------------------------------


class _ModelEntry(BaseModel):
    id: str


class _ModelList(BaseModel):
    data: list[_ModelEntry]


def list_openai_models(
    config: ProviderConfig, transport: httpx2.BaseTransport | None = None
) -> list[str]:
    """Return the model ids from ``GET /models``, in the Provider's order."""
    client = _Client(config, transport, LIST_MODELS_TIMEOUT)
    with client.sdk:

        def request() -> _ModelList:
            raw = client.sdk.models.with_raw_response.list()
            return parse_response(_ModelList, raw.content, raw.status_code)

        models = client.send(request)
    return list(dict.fromkeys(model.id for model in models.data))
