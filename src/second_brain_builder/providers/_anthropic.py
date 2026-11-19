"""Anthropic-compatible Provider: messages and models."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeVar, cast

import anthropic
import httpx2
from anthropic.types import (
    MessageParam,
    TextBlockParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlockParam,
)
from pydantic import BaseModel, Discriminator, Field, SecretStr, Tag, model_validator

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
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)

ResultT = TypeVar("ResultT")
_log = logging.getLogger(__name__)

_BlockParam = TextBlockParam | ToolUseBlockParam | ToolResultBlockParam

_STOP_REASONS: dict[str, str] = {
    "end_turn": StopReason.END_TURN.value,
    "stop_sequence": StopReason.END_TURN.value,
    "tool_use": StopReason.TOOL_USE.value,
    "max_tokens": StopReason.MAX_TOKENS.value,
    "model_context_window_exceeded": StopReason.MAX_TOKENS.value,
    "refusal": StopReason.REFUSAL.value,
}

# The Anthropic API returns at most this many models per page.
_MODELS_PAGE_SIZE = 1000

# Sent as the key when the Provider has no API key. The SDK sends an empty key
# as the header value "Bearer ", which the HTTP layer refuses to send. With no
# key at all, the SDK would read ANTHROPIC_API_KEY and other credentials of the
# host and send them to the configured base URL.
_NO_API_KEY = "no-key"


def sdk_base_url(base_url: str) -> str:
    """Return the base URL without a trailing ``/v1``: the SDK adds ``/v1/...`` itself."""
    return base_url.removesuffix("/v1")


class _Client:
    """An Anthropic SDK client that raises ``ProviderError`` instead of SDK errors."""

    def __init__(
        self,
        config: ProviderConfig,
        transport: httpx2.BaseTransport | None,
        timeout: httpx2.Timeout = REQUEST_TIMEOUT,
    ) -> None:
        self._api_key: SecretStr = config.api_key
        self._base_url = sdk_base_url(config.base_url)
        self.cache_supported = True
        self.sdk = anthropic.Anthropic(
            api_key=config.api_key.get_secret_value() or _NO_API_KEY,
            base_url=self._base_url,
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
            lambda: send_with_retries(request, anthropic.APIError, self._translate), self._api_key
        )

    def _translate(self, exc: Exception) -> Failure:
        if isinstance(exc, anthropic.APIStatusError):
            return Failure(
                status_error(exc.status_code, exc.body, self._api_key),
                retry=should_retry_status(exc.response),
                headers=exc.response.headers,
            )
        if isinstance(exc, anthropic.APITimeoutError):
            error = connection_error(self._base_url, timed_out=True, api_key=self._api_key)
            return Failure(error, retry=False)
        if isinstance(exc, anthropic.APIConnectionError):
            error = connection_error(self._base_url, timed_out=False, api_key=self._api_key)
            return Failure(error, retry=True)
        if isinstance(exc, anthropic.APIResponseValidationError):
            message = "The Provider answer does not have the expected format."
            return Failure(ProviderResponseError(message, status_code=exc.status_code), retry=False)
        return Failure(ProviderError("The Provider call failed."), retry=False)


# --- chat --------------------------------------------------------------------------


class _TextBlock(BaseModel):
    type: Literal["text"]
    text: str


class _ToolUseBlock(BaseModel):
    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, Any]


class _OtherBlock(BaseModel):
    """A block replayed unchanged without provider-neutral interpretation."""

    type: str


def _block_kind(value: object) -> str:
    kind = value.get("type") if isinstance(value, dict) else None
    if kind == "text" or kind == "tool_use":
        return str(kind)
    return "other"


_Block = Annotated[
    Annotated[_TextBlock, Tag("text")]
    | Annotated[_ToolUseBlock, Tag("tool_use")]
    | Annotated[_OtherBlock, Tag("other")],
    Discriminator(_block_kind),
]


class _Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None


class _Message(BaseModel):
    content: list[_Block]
    raw_content: list[dict[str, Any]] = Field(repr=False)
    stop_reason: str | None = None
    usage: _Usage | None = None

    @model_validator(mode="before")
    @classmethod
    def _preserve_raw_content(cls, value: object) -> object:
        # Validate known blocks separately, retaining every raw field and its
        # order rather than reconstructing blocks from parsed models.
        if isinstance(value, dict):
            return {**value, "raw_content": value.get("content")}
        return value


class AnthropicChatModel:
    """``ChatModel`` for the messages API of an Anthropic-compatible Provider."""

    def __init__(
        self,
        config: ProviderConfig,
        model: str,
        transport: httpx2.BaseTransport | None = None,
        *,
        cache_supported: bool = True,
        on_cache_unsupported: Callable[[], None] | None = None,
    ) -> None:
        self._client = _Client(config, transport)
        self._client.cache_supported = cache_supported
        self._on_cache_unsupported = on_cache_unsupported
        self._model = model

    def chat(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> ChatResponse:
        request_tools = [to_anthropic_tool(tool) for tool in tools]
        create = self._client.sdk.messages.with_raw_response.create

        sent_cache_control = False

        def send(*, cache_control: bool) -> Any:
            nonlocal sent_cache_control
            request_messages = to_anthropic_messages(messages, cache_control=cache_control)
            sent_cache_control = cache_control and (
                bool(system)
                or any(
                    isinstance(turn["content"], list)
                    and any("cache_control" in block for block in turn["content"])
                    for turn in request_messages
                )
            )
            request_system: str | list[TextBlockParam] = system
            if system and cache_control:
                request_system = [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                ]
            return create(
                model=self._model,
                max_tokens=max_tokens,
                messages=request_messages,
                system=request_system if system else anthropic.omit,
                tools=request_tools if request_tools else anthropic.omit,
            )

        def request() -> ChatResponse:
            try:
                raw = send(cache_control=self._client.cache_supported)
            except anthropic.APIStatusError as exc:
                if not sent_cache_control or exc.status_code not in (400, 422):
                    raise
                try:
                    raw = send(cache_control=False)
                except Exception as retry_exc:
                    raise retry_exc from exc
                self._client.cache_supported = False
                if self._on_cache_unsupported is not None:
                    self._on_cache_unsupported()
            return from_anthropic_message(parse_response(_Message, raw.read(), raw.status_code))

        return self._client.send(request)


def to_anthropic_messages(
    messages: list[Message], *, cache_control: bool = False
) -> list[MessageParam]:
    """Map the conversation to Anthropic turns.

    Tool results become ``tool_result`` blocks in a user turn. Consecutive messages
    that map to the same role are merged into one turn, so the results of parallel
    tool calls share one user turn. In a user turn, tool results come first, as
    the API requires.
    """
    turns: list[tuple[Literal["user", "assistant"], list[_BlockParam]]] = []
    for message in messages:
        role: Literal["user", "assistant"] = "assistant" if message.role == "assistant" else "user"
        blocks = _blocks(message)
        if cache_control and message.role == "user":
            for block in blocks:
                if block["type"] == "text":
                    block["cache_control"] = {"type": "ephemeral"}
                    cache_control = False
                    break
        if not blocks:
            continue
        if turns and turns[-1][0] == role:
            turns[-1][1].extend(blocks)
        else:
            turns.append((role, blocks))
    missing_count = 0
    for index, (role, blocks) in enumerate(turns):
        if role != "assistant":
            continue
        call_ids = [block["id"] for block in blocks if block["type"] == "tool_use"]
        if not call_ids:
            continue
        if index + 1 == len(turns):
            turns.append(("user", []))
        results = turns[index + 1][1]
        recorded = {block["tool_use_id"] for block in results if block["type"] == "tool_result"}
        for call_id in call_ids:
            if call_id not in recorded:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "is_error": True,
                        "content": "Error: no result was recorded for this call.",
                    }
                )
                missing_count += 1
    if missing_count:
        _log.warning("Added %d missing Anthropic tool results.", missing_count)
    result: list[MessageParam] = []
    for role, blocks in turns:
        if role == "user":
            ordered = sorted(blocks, key=lambda block: block["type"] != "tool_result")
            result.append({"role": "user", "content": ordered})
        else:
            result.append({"role": "assistant", "content": blocks})
    return result


def _blocks(message: Message) -> list[_BlockParam]:
    if message.role == "assistant" and message.provider_content is not None:
        # The SDK also accepts opaque block types beyond its declared union.
        # Copy the list so merging consecutive turns cannot alter the message.
        return cast(list[_BlockParam], list(message.provider_content))
    blocks: list[_BlockParam] = []
    if message.role == "tool":
        result: ToolResultBlockParam = {
            "type": "tool_result",
            "tool_use_id": message.tool_call_id or "",
        }
        if message.content:
            result["content"] = message.content
        blocks.append(result)
        return blocks
    # The API rejects empty text blocks.
    if message.content and message.content.strip():
        blocks.append({"type": "text", "text": message.content})
    for call in message.tool_calls:
        blocks.append(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        )
    return blocks


def to_anthropic_tool(tool: ToolSpec) -> ToolParam:
    """Map a tool to an Anthropic custom tool."""
    return {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}


def from_anthropic_message(message: _Message) -> ChatResponse:
    """Map an Anthropic message to a ``ChatResponse``.

    An answer that was cut off or refused has no tool calls: see ``ChatResponse``.
    The API returns a ``tool_use`` block that ``max_tokens`` cut off, and its
    partial ``input`` is often a valid object.
    """
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in message.content:
        if isinstance(block, _TextBlock):
            texts.append(block.text)
        elif isinstance(block, _ToolUseBlock):
            tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
    stop_reason = _stop_reason(message.stop_reason, bool(tool_calls))
    cut_off_tool_call_ids = (
        [call.id for call in tool_calls] if stop_reason == StopReason.MAX_TOKENS.value else []
    )
    if stop_reason in CUT_OFF_STOP_REASONS:
        tool_calls = []
    usage = message.usage or _Usage()
    return ChatResponse(
        text="".join(texts) or None,
        tool_calls=tool_calls,
        cut_off_tool_call_ids=cut_off_tool_call_ids,
        provider_content=message.raw_content,
        # input_tokens leaves out the tokens read from or written to the prompt cache.
        usage=Usage(
            input_tokens=usage.input_tokens
            + (usage.cache_creation_input_tokens or 0)
            + (usage.cache_read_input_tokens or 0),
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
        ),
        stop_reason=stop_reason,
    )


def _stop_reason(stop_reason: str | None, has_tool_calls: bool) -> str:
    if stop_reason is None:
        return StopReason.TOOL_USE.value if has_tool_calls else StopReason.END_TURN.value
    return _STOP_REASONS.get(stop_reason, stop_reason)


# --- models ------------------------------------------------------------------------


class _ModelEntry(BaseModel):
    id: str


class _ModelPage(BaseModel):
    data: list[_ModelEntry]
    has_more: bool = False
    last_id: str | None = None


def list_anthropic_models(
    config: ProviderConfig, transport: httpx2.BaseTransport | None = None
) -> list[str]:
    """Return the model ids from ``GET /v1/models``, all pages, in the Provider's order."""
    client = _Client(config, transport, LIST_MODELS_TIMEOUT)
    ids: list[str] = []
    after_id: str | None = None
    with client.sdk:
        while True:
            page = client.send(_models_page_request(client, after_id))
            ids.extend(model.id for model in page.data)
            if not page.has_more or not page.last_id or page.last_id == after_id:
                break
            after_id = page.last_id
    return list(dict.fromkeys(ids))


def _models_page_request(client: _Client, after_id: str | None) -> Callable[[], _ModelPage]:
    def request() -> _ModelPage:
        raw = client.sdk.models.with_raw_response.list(
            limit=_MODELS_PAGE_SIZE, after_id=after_id if after_id else anthropic.omit
        )
        return parse_response(_ModelPage, raw.read(), raw.status_code)

    return request
