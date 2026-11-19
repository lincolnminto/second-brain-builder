"""Contract tests for the Provider clients against scripted HTTP endpoints.

The official SDKs send their requests through ``httpx2``, the successor of
``httpx``, so most scripted endpoints sit behind an ``httpx2.MockTransport``.
A MockTransport skips the checks of the real HTTP stack (header values, for
example), so the tests for those checks use ``LocalServer``, a real HTTP server
on 127.0.0.1.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from pydantic import SecretStr

from second_brain_builder.providers import (
    ChatResponse,
    Message,
    ProviderAuthError,
    ProviderConfig,
    ProviderConnectionError,
    ProviderError,
    ProviderKind,
    ProviderNotFoundError,
    ProviderRateLimitError,
    ProviderResponseError,
    Segment,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
    list_models,
    make_chat_model,
    make_transcriber,
)
from second_brain_builder.providers._common import (
    LIST_MODELS_TIMEOUT,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
)

API_KEY = "sbb-synthetic-test-key"
# Lets the SDKs retry at once instead of after their backoff delay.
NO_RETRY_DELAY = {"retry-after-ms": "1"}
CHAT_PATHS = {ProviderKind.OPENAI: "/v1/chat/completions", ProviderKind.ANTHROPIC: "/v1/messages"}

SYSTEM = "You maintain a wiki about plants."
READ_PAGE = ToolSpec(
    name="read_page",
    description="Read one Wiki Page.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
)


@dataclass
class Reply:
    method: str
    path: str
    status: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)


class FakeProvider:
    """Answers requests in order from a script and records every request.

    A request that does not match the next reply gets HTTP 418, which the clients
    raise as ``ProviderError``. The fake never raises in the transport, because
    the SDKs would retry that as a connection error.
    """

    def __init__(self) -> None:
        self.replies: list[Reply] = []
        self.requests: list[httpx2.Request] = []
        self.transport = httpx2.MockTransport(self._handle)

    def reply(
        self,
        method: str,
        path: str,
        body: Any,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Queue one reply. A ``str`` body is sent as HTML, anything else as JSON."""
        self.replies.append(Reply(method, path, status, body, headers or {}))

    def json_body(self, index: int) -> Any:
        return json.loads(self.requests[index].content)

    def multipart_fields(self, index: int) -> dict[str, tuple[str | None, bytes]]:
        """Return ``{name: (filename, content)}`` of a ``multipart/form-data`` request."""
        request = self.requests[index]
        head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
        message = BytesParser(policy=HTTP).parsebytes(head + request.content)
        assert isinstance(message, EmailMessage)
        fields: dict[str, tuple[str | None, bytes]] = {}
        for part in message.iter_parts():
            assert isinstance(part, EmailMessage)
            name = part.get_param("name", header="content-disposition")
            payload = part.get_payload(decode=True)
            assert isinstance(name, str)
            assert isinstance(payload, bytes)
            fields[name] = (part.get_filename(), payload)
        return fields

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.replies:
            return httpx2.Response(418, text=f"unexpected {request.method} {request.url}")
        reply = self.replies.pop(0)
        if (request.method, request.url.path) != (reply.method, reply.path):
            expected = f"{reply.method} {reply.path}"
            return httpx2.Response(
                418, text=f"expected {expected}, got {request.method} {request.url}"
            )
        if isinstance(reply.body, str):
            headers = {"content-type": "text/html", **reply.headers}
            return httpx2.Response(reply.status, text=reply.body, headers=headers)
        return httpx2.Response(reply.status, json=reply.body, headers=reply.headers)


@dataclass
class ReceivedRequest:
    method: str
    path: str
    headers: dict[str, str]


class LocalServer:
    """A real HTTP server on 127.0.0.1 that answers every request with one JSON body."""

    def __init__(self) -> None:
        self.body: Any = {}
        self.requests: list[ReceivedRequest] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._answer()

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("content-length", "0")))
                self._answer()

            def _answer(self) -> None:
                headers = {name.lower(): value for name, value in self.headers.items()}
                server.requests.append(ReceivedRequest(self.command, self.path, headers))
                content = json.dumps(server.body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler


def make_config(kind: ProviderKind, base_url: str | None = None) -> ProviderConfig:
    default_url = {
        ProviderKind.OPENAI: "https://llm.example.com/v1",
        ProviderKind.ANTHROPIC: "https://claude.example.com",
    }[kind]
    return ProviderConfig(kind=kind, base_url=base_url or default_url, api_key=SecretStr(API_KEY))


def openai_completion(
    message: dict[str, Any], finish_reason: str | None, usage: dict[str, int] | None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", **message},
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return body


def anthropic_message(
    content: list[dict[str, Any]], stop_reason: str, usage: dict[str, int]
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "test-model",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }


def hello_answer(kind: ProviderKind) -> dict[str, Any]:
    """Return the body of a plain text answer to a chat call."""
    if kind == ProviderKind.OPENAI:
        return openai_completion(
            {"content": "Hello."}, "stop", {"prompt_tokens": 3, "completion_tokens": 2}
        )
    return anthropic_message(
        [{"type": "text", "text": "Hello."}], "end_turn", {"input_tokens": 3, "output_tokens": 2}
    )


def ok_reply(fake: FakeProvider, kind: ProviderKind) -> None:
    """Queue a plain text answer for a chat call."""
    fake.reply("POST", CHAT_PATHS[kind], hello_answer(kind))


def say_hello(config: ProviderConfig, fake: FakeProvider) -> ChatResponse:
    model = make_chat_model(config, "test-model", transport=fake.transport)
    return model.chat("", [Message(role="user", content="Hello")], [], max_tokens=16)


@pytest.fixture
def fake() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def local_server() -> Iterator[LocalServer]:
    server = LocalServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(params=[ProviderKind.OPENAI, ProviderKind.ANTHROPIC], ids=lambda kind: kind.value)
def kind(request: pytest.FixtureRequest) -> ProviderKind:
    value = request.param
    assert isinstance(value, ProviderKind)
    return value


# --- OpenAI-compatible chat ----------------------------------------------------------


def test_openai_tool_call_round_trip(fake: FakeProvider) -> None:
    arguments = '{"path": "wiki/concepts/leaf.md"}'
    fake.reply(
        "POST",
        "/v1/chat/completions",
        openai_completion(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_page", "arguments": arguments},
                    }
                ],
            },
            "tool_calls",
            {"prompt_tokens": 120, "completion_tokens": 15, "total_tokens": 135},
        ),
    )
    fake.reply(
        "POST",
        "/v1/chat/completions",
        openai_completion(
            {"content": "A leaf makes food from light."},
            "stop",
            {"prompt_tokens": 160, "completion_tokens": 9, "total_tokens": 169},
        ),
    )
    model = make_chat_model(
        make_config(ProviderKind.OPENAI), "test-model", transport=fake.transport
    )
    conversation = [Message(role="user", content="What does a leaf do?")]

    first = model.chat(SYSTEM, conversation, [READ_PAGE], max_tokens=1024)
    conversation += [
        first.to_message(),
        Message(role="tool", tool_call_id="call_1", content="A leaf is an organ of a plant."),
    ]
    second = model.chat(SYSTEM, conversation, [READ_PAGE], max_tokens=1024)

    assert first == ChatResponse(
        text=None,
        tool_calls=[
            ToolCall(id="call_1", name="read_page", arguments={"path": "wiki/concepts/leaf.md"})
        ],
        usage=Usage(input_tokens=120, output_tokens=15),
        stop_reason=StopReason.TOOL_USE.value,
    )
    assert second == ChatResponse(
        text="A leaf makes food from light.",
        usage=Usage(input_tokens=160, output_tokens=9),
        stop_reason=StopReason.END_TURN.value,
    )
    assert fake.requests[0].headers["authorization"] == f"Bearer {API_KEY}"
    assert fake.json_body(0) == {
        "model": "test-model",
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "What does a leaf do?"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_page",
                    "description": "Read one Wiki Page.",
                    "parameters": READ_PAGE.parameters,
                },
            }
        ],
    }
    sent_back = fake.json_body(1)["messages"][2:]
    assert sent_back == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_page", "arguments": arguments},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "A leaf is an organ of a plant."},
    ]


def test_openai_switches_to_max_completion_tokens_once_rejected(fake: FakeProvider) -> None:
    # OpenAI reasoning models reject max_tokens; compatible servers may know only it.
    fake.reply(
        "POST",
        "/v1/chat/completions",
        {
            "error": {
                "message": "Unsupported parameter: 'max_tokens' is not supported with this "
                "model. Use 'max_completion_tokens' instead.",
                "type": "invalid_request_error",
                "param": "max_tokens",
                "code": "unsupported_parameter",
            }
        },
        status=400,
    )
    ok_reply(fake, ProviderKind.OPENAI)
    ok_reply(fake, ProviderKind.OPENAI)
    model = make_chat_model(
        make_config(ProviderKind.OPENAI), "test-model", transport=fake.transport
    )

    model.chat("", [Message(role="user", content="Hello")], [], max_tokens=64)
    model.chat("", [Message(role="user", content="Hello again")], [], max_tokens=64)

    limits = [
        {key: body[key] for key in ("max_tokens", "max_completion_tokens") if key in body}
        for body in (fake.json_body(index) for index in range(3))
    ]
    assert limits == [
        {"max_tokens": 64},
        {"max_completion_tokens": 64},
        {"max_completion_tokens": 64},
    ]


@pytest.mark.parametrize(
    ("message", "finish_reason", "usage", "expected"),
    [
        pytest.param(
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_7",
                        "type": "function",
                        "function": {"name": "read_page", "arguments": '{"path": "index.md"}'},
                    }
                ],
            },
            "stop",
            {"prompt_tokens": 10, "completion_tokens": 4},
            ChatResponse(
                tool_calls=[
                    ToolCall(id="call_7", name="read_page", arguments={"path": "index.md"})
                ],
                usage=Usage(input_tokens=10, output_tokens=4),
                stop_reason=StopReason.TOOL_USE.value,
            ),
            id="stop-with-tool-calls-means-tool-use",
        ),
        pytest.param(
            {
                "content": "Reading the page.",
                "tool_calls": [
                    {
                        "id": "call_8",
                        "type": "function",
                        "function": {"name": "read_page", "arguments": '{"path": "wiki/conc'},
                    }
                ],
            },
            "length",
            {"prompt_tokens": 10, "completion_tokens": 64},
            ChatResponse(
                text="Reading the page.",
                usage=Usage(input_tokens=10, output_tokens=64),
                stop_reason=StopReason.MAX_TOKENS.value,
            ),
            id="tool-call-cut-off-by-max-tokens-is-dropped",
        ),
        pytest.param(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "read_page", "arguments": {"path": "index.md"}},
                    }
                ],
            },
            "tool_calls",
            None,
            ChatResponse(
                tool_calls=[
                    ToolCall(id="call_0", name="read_page", arguments={"path": "index.md"})
                ],
                usage=Usage(input_tokens=0, output_tokens=0),
                stop_reason=StopReason.TOOL_USE.value,
            ),
            id="object-arguments-no-id-no-usage",
        ),
        pytest.param(
            {"content": None, "refusal": "I can not help with that."},
            "stop",
            {"prompt_tokens": 10, "completion_tokens": 7},
            ChatResponse(
                text="I can not help with that.",
                usage=Usage(input_tokens=10, output_tokens=7),
                stop_reason=StopReason.REFUSAL.value,
            ),
            id="refusal",
        ),
    ],
)
def test_openai_answers_from_compatible_servers_are_normalized(
    fake: FakeProvider,
    message: dict[str, Any],
    finish_reason: str,
    usage: dict[str, int] | None,
    expected: ChatResponse,
) -> None:
    fake.reply("POST", "/v1/chat/completions", openai_completion(message, finish_reason, usage))

    response = say_hello(make_config(ProviderKind.OPENAI), fake)

    assert response == expected


def test_openai_tool_arguments_that_are_not_json_raise_response_error(fake: FakeProvider) -> None:
    fake.reply(
        "POST",
        "/v1/chat/completions",
        openai_completion(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_page", "arguments": "path=index.md"},
                    }
                ],
            },
            "tool_calls",
            {"prompt_tokens": 10, "completion_tokens": 4},
        ),
    )

    with pytest.raises(ProviderResponseError, match="not a JSON object"):
        say_hello(make_config(ProviderKind.OPENAI), fake)


# --- Anthropic-compatible chat -------------------------------------------------------


def test_anthropic_tool_call_round_trip(fake: FakeProvider) -> None:
    fake.reply(
        "POST",
        "/v1/messages",
        anthropic_message(
            [
                {"type": "text", "text": "I will read both pages."},
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "read_page",
                    "input": {"path": "wiki/concepts/leaf.md"},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_02",
                    "name": "read_page",
                    "input": {"path": "wiki/concepts/stoma.md"},
                },
            ],
            "tool_use",
            {"input_tokens": 200, "output_tokens": 40},
        ),
    )
    fake.reply(
        "POST",
        "/v1/messages",
        anthropic_message(
            [
                {"type": "text", "text": "Leaves breathe through "},
                {"type": "text", "text": "stomata."},
            ],
            "end_turn",
            {
                "input_tokens": 12,
                "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 400,
                "output_tokens": 8,
            },
        ),
    )
    model = make_chat_model(
        make_config(ProviderKind.ANTHROPIC), "test-model", transport=fake.transport
    )
    conversation = [Message(role="user", content="How do leaves breathe?")]

    first = model.chat(SYSTEM, conversation, [READ_PAGE], max_tokens=1024)
    conversation += [
        first.to_message(),
        Message(role="tool", tool_call_id="toolu_01", content="A leaf is an organ of a plant."),
        Message(role="tool", tool_call_id="toolu_02", content="A stoma is a pore in a leaf."),
        Message(role="user", content="Answer in one sentence."),
    ]
    second = model.chat(SYSTEM, conversation, [READ_PAGE], max_tokens=1024)

    assert first == ChatResponse(
        text="I will read both pages.",
        tool_calls=[
            ToolCall(id="toolu_01", name="read_page", arguments={"path": "wiki/concepts/leaf.md"}),
            ToolCall(id="toolu_02", name="read_page", arguments={"path": "wiki/concepts/stoma.md"}),
        ],
        usage=Usage(input_tokens=200, output_tokens=40),
        stop_reason=StopReason.TOOL_USE.value,
    )
    # Prompt cache reads and writes count as input tokens.
    assert second == ChatResponse(
        text="Leaves breathe through stomata.",
        usage=Usage(
            input_tokens=442, output_tokens=8, cache_read_tokens=400, cache_write_tokens=30
        ),
        stop_reason=StopReason.END_TURN.value,
    )
    # The SDK sends the key as x-api-key or as a bearer token, by version.
    headers = fake.requests[0].headers
    assert API_KEY in headers.get("x-api-key", "") + headers.get("authorization", "")
    assert fake.json_body(0) == {
        "model": "test-model",
        "max_tokens": 1024,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "How do leaves breathe?",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ],
        "tools": [
            {
                "name": "read_page",
                "description": "Read one Wiki Page.",
                "input_schema": READ_PAGE.parameters,
            }
        ],
    }
    # Both tool results and the user's note share one user turn, results first.
    assert fake.json_body(1)["messages"][0] == fake.json_body(0)["messages"][0]
    assert fake.json_body(1)["messages"][1:] == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will read both pages."},
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "read_page",
                    "input": {"path": "wiki/concepts/leaf.md"},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_02",
                    "name": "read_page",
                    "input": {"path": "wiki/concepts/stoma.md"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01",
                    "content": "A leaf is an organ of a plant.",
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_02",
                    "content": "A stoma is a pore in a leaf.",
                },
                {"type": "text", "text": "Answer in one sentence."},
            ],
        },
    ]


@pytest.mark.parametrize("status", [400, 422])
def test_anthropic_cache_rejection_retries_once_and_disables_cache_per_client(
    fake: FakeProvider, status: int
) -> None:
    fake.reply(
        "POST",
        "/v1/messages",
        {"error": {"type": "invalid_request_error", "message": "Unsupported cache_control"}},
        status=status,
    )
    for _ in range(2):
        fake.reply(
            "POST",
            "/v1/messages",
            anthropic_message([{"type": "text", "text": "Done."}], "end_turn", {}),
        )
    model = make_chat_model(
        make_config(ProviderKind.ANTHROPIC), "test-model", transport=fake.transport
    )
    for _ in range(2):
        assert model.chat(SYSTEM, [Message(role="user", content="Part 1")], [], 100).text == "Done."
    assert len(fake.requests) == 3
    assert "cache_control" in fake.requests[0].content.decode()
    for request in fake.requests[1:]:
        assert "cache_control" not in request.content.decode()
    assert fake.json_body(1)["system"] == SYSTEM


def test_anthropic_cache_retry_does_not_hide_a_second_rejection(fake: FakeProvider) -> None:
    for _ in range(2):
        fake.reply(
            "POST",
            "/v1/messages",
            {"error": {"type": "invalid_request_error", "message": "Unsupported cache_control"}},
            status=400,
        )
    model = make_chat_model(
        make_config(ProviderKind.ANTHROPIC), "test-model", transport=fake.transport
    )
    with pytest.raises(ProviderError):
        model.chat(SYSTEM, [Message(role="user", content="Part 1")], [], 100)
    assert len(fake.requests) == 2


def test_anthropic_without_system_prompt_or_tools_omits_them(fake: FakeProvider) -> None:
    ok_reply(fake, ProviderKind.ANTHROPIC)

    say_hello(make_config(ProviderKind.ANTHROPIC), fake)

    assert set(fake.json_body(0)) == {"model", "max_tokens", "messages"}


def test_anthropic_puts_tool_results_first_in_a_merged_user_turn(fake: FakeProvider) -> None:
    # The API rejects a user turn in which text comes before the tool results.
    ok_reply(fake, ProviderKind.ANTHROPIC)
    model = make_chat_model(
        make_config(ProviderKind.ANTHROPIC), "test-model", transport=fake.transport
    )
    conversation = [
        Message(role="user", content="What is a leaf?"),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="toolu_01", name="read_page", arguments={"path": "a.md"})],
        ),
        Message(role="user", content="Answer in one sentence."),
        Message(role="tool", tool_call_id="toolu_01", content="A leaf is an organ of a plant."),
    ]

    model.chat("", conversation, [READ_PAGE], max_tokens=64)

    assert fake.json_body(0)["messages"][2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01",
                "content": "A leaf is an organ of a plant.",
            },
            {"type": "text", "text": "Answer in one sentence."},
        ],
    }


@pytest.mark.parametrize(
    ("api_stop_reason", "expected"),
    [
        ("stop_sequence", StopReason.END_TURN.value),
        ("max_tokens", StopReason.MAX_TOKENS.value),
        ("model_context_window_exceeded", StopReason.MAX_TOKENS.value),
        ("refusal", StopReason.REFUSAL.value),
        ("pause_turn", "pause_turn"),
    ],
)
def test_anthropic_stop_reasons_are_normalized(
    fake: FakeProvider, api_stop_reason: str, expected: str
) -> None:
    fake.reply(
        "POST",
        "/v1/messages",
        anthropic_message(
            [{"type": "text", "text": "Leaves"}],
            api_stop_reason,
            {"input_tokens": 3, "output_tokens": 1},
        ),
    )

    response = say_hello(make_config(ProviderKind.ANTHROPIC), fake)

    assert response.stop_reason == expected


# --- both API families ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("stop_reason", "api_stop_reasons"),
    [
        (
            StopReason.MAX_TOKENS,
            {ProviderKind.OPENAI: "length", ProviderKind.ANTHROPIC: "max_tokens"},
        ),
        (
            StopReason.REFUSAL,
            {ProviderKind.OPENAI: "content_filter", ProviderKind.ANTHROPIC: "refusal"},
        ),
    ],
    ids=lambda value: value.value if isinstance(value, StopReason) else "",
)
def test_answer_that_was_cut_off_has_no_tool_calls(
    fake: FakeProvider,
    kind: ProviderKind,
    stop_reason: StopReason,
    api_stop_reasons: dict[ProviderKind, str],
) -> None:
    # The cut-off input is still a valid object. Only the stop reason shows that
    # the body is incomplete, and a caller must not write it to a Wiki Page.
    partial = {"path": "wiki/concepts/leaf.md", "body": "A leaf is an org"}
    usage = {"input_tokens": 10, "output_tokens": 64}
    if kind == ProviderKind.OPENAI:
        call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "write_page", "arguments": json.dumps(partial)},
        }
        body = openai_completion(
            {"content": "Writing the page.", "tool_calls": [call]},
            api_stop_reasons[kind],
            {"prompt_tokens": 10, "completion_tokens": 64},
        )
    else:
        blocks = [
            {"type": "text", "text": "Writing the page."},
            {"type": "tool_use", "id": "toolu_01", "name": "write_page", "input": partial},
        ]
        body = anthropic_message(blocks, api_stop_reasons[kind], usage)
    fake.reply("POST", CHAT_PATHS[kind], body)

    response = say_hello(make_config(kind), fake)

    assert response == ChatResponse(
        text="Writing the page.",
        usage=Usage(input_tokens=10, output_tokens=64),
        stop_reason=stop_reason.value,
    )


# --- errors and retries --------------------------------------------------------------


def test_invalid_tool_arguments_never_expose_the_key(fake: FakeProvider) -> None:
    fake.reply(
        "POST",
        CHAT_PATHS[ProviderKind.OPENAI],
        openai_completion(
            {"tool_calls": [{"function": {"name": API_KEY, "arguments": "{"}}]},
            "tool_calls",
            None,
        ),
    )
    with pytest.raises(ProviderResponseError) as caught:
        say_hello(make_config(ProviderKind.OPENAI), fake)
    error = caught.value
    assert API_KEY not in str(error)
    assert API_KEY not in repr(error)
    assert API_KEY not in "".join(traceback.format_exception(error))
    assert error.__context__ is None


def test_response_validation_errors_are_redacted(fake: FakeProvider, kind: ProviderKind) -> None:
    # A key can also occur in validation messages, including their fixed text.
    key = "format"
    config = make_config(kind).model_copy(update={"api_key": SecretStr(key)})
    fake.reply("POST", CHAT_PATHS[kind], {})
    with pytest.raises(ProviderResponseError) as caught:
        say_hello(config, fake)
    error = caught.value
    assert key not in str(error)
    assert key not in repr(error)
    assert key not in "".join(traceback.format_exception(error))
    assert error.__context__ is None
    assert error.__cause__ is None


@pytest.mark.parametrize("finish_reason", ["stop", "tool_calls", None])
def test_refusal_with_partial_text_drops_tools(
    fake: FakeProvider, finish_reason: str | None
) -> None:
    fake.reply(
        "POST",
        CHAT_PATHS[ProviderKind.OPENAI],
        openai_completion(
            {
                "content": "Partial answer.",
                "refusal": "I cannot continue.",
                "tool_calls": [
                    {"function": {"name": "write_page", "arguments": '{"body":"partial"}'}}
                ],
            },
            finish_reason,
            None,
        ),
    )
    response = say_hello(make_config(ProviderKind.OPENAI), fake)
    assert response.text == "Partial answer."
    assert response.stop_reason == StopReason.REFUSAL.value
    assert response.tool_calls == []


def test_rejected_key_raises_auth_error_that_never_shows_the_key(
    fake: FakeProvider, kind: ProviderKind
) -> None:
    # A compatible server may echo the key it rejected.
    fake.reply(
        "POST",
        CHAT_PATHS[kind],
        {"error": {"type": "authentication_error", "message": f"Invalid API key: {API_KEY}"}},
        status=401,
    )

    with pytest.raises(ProviderAuthError) as caught:
        say_hello(make_config(kind), fake)

    error = caught.value
    assert error.status_code == 401
    assert "Invalid API key: [redacted]" in str(error)
    # No SDK exception (with the request headers and raw body) is chained to it.
    assert error.__cause__ is None
    assert error.__context__ is None
    assert API_KEY not in "".join(traceback.format_exception(error))
    assert API_KEY not in repr(error)


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(404, ProviderNotFoundError), (400, ProviderError), (403, ProviderAuthError)],
)
def test_error_status_maps_to_typed_error(
    fake: FakeProvider, kind: ProviderKind, status: int, error_type: type[ProviderError]
) -> None:
    fake.reply(
        "POST",
        CHAT_PATHS[kind],
        {"error": {"message": "model 'test-model' not found"}},
        status=status,
    )

    with pytest.raises(ProviderError) as caught:
        say_hello(make_config(kind), fake)

    assert type(caught.value) is error_type
    assert caught.value.status_code == status
    assert "model 'test-model' not found" in str(caught.value)


def test_rate_limit_is_retried_then_raised(fake: FakeProvider, kind: ProviderKind) -> None:
    for _ in range(1 + MAX_RETRIES):
        fake.reply(
            "POST",
            CHAT_PATHS[kind],
            {"error": {"message": "Too many requests."}},
            status=429,
            headers=NO_RETRY_DELAY,
        )

    with pytest.raises(ProviderRateLimitError):
        say_hello(make_config(kind), fake)

    assert len(fake.requests) == 1 + MAX_RETRIES


def test_server_error_is_retried(fake: FakeProvider, kind: ProviderKind) -> None:
    fake.reply(
        "POST",
        CHAT_PATHS[kind],
        {"error": {"message": "Overloaded."}},
        status=503,
        headers=NO_RETRY_DELAY,
    )
    ok_reply(fake, kind)

    response = say_hello(make_config(kind), fake)

    assert response.text == "Hello."
    assert len(fake.requests) == 2


def test_unreachable_provider_raises_connection_error(
    kind: ProviderKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The SDKs wait between retries of a connection error; skip the waits.
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    attempts: list[httpx2.Request] = []

    def refuse(request: httpx2.Request) -> httpx2.Response:
        attempts.append(request)
        raise httpx2.ConnectError("connection refused", request=request)

    config = make_config(kind, "https://user:password@unreachable.example.com/v1")
    model = make_chat_model(config, "test-model", transport=httpx2.MockTransport(refuse))

    with pytest.raises(ProviderConnectionError) as caught:
        model.chat("", [Message(role="user", content="Hello")], [], max_tokens=16)

    assert "unreachable.example.com" in str(caught.value)
    assert "password" not in str(caught.value)
    assert caught.value.__context__ is None
    assert len(attempts) == 1 + MAX_RETRIES


def test_timeout_is_not_retried(kind: ProviderKind, monkeypatch: pytest.MonkeyPatch) -> None:
    # The Provider may still be generating the answer. A new request would start
    # the generation again and wait the whole read timeout once more.
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    attempts: list[httpx2.Request] = []

    def time_out(request: httpx2.Request) -> httpx2.Response:
        attempts.append(request)
        raise httpx2.ReadTimeout("timed out", request=request)

    model = make_chat_model(
        make_config(kind), "test-model", transport=httpx2.MockTransport(time_out)
    )

    with pytest.raises(ProviderConnectionError, match="did not answer in time") as caught:
        model.chat("", [Message(role="user", content="Hello")], [], max_tokens=16)

    assert caught.value.__context__ is None
    assert len(attempts) == 1


def test_retry_waits_as_long_as_retry_after_says(
    fake: FakeProvider, kind: ProviderKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)
    fake.reply(
        "POST",
        CHAT_PATHS[kind],
        {"error": {"message": "Too many requests."}},
        status=429,
        headers={"retry-after": "3"},
    )
    ok_reply(fake, kind)

    response = say_hello(make_config(kind), fake)

    assert response.text == "Hello."
    assert waits == [3.0]


def chat_with_local_server(local_server: LocalServer, kind: ProviderKind, api_key: str) -> str:
    """Send one chat call to ``local_server`` and return the key that it received."""
    local_server.body = hello_answer(kind)
    base_url = local_server.url + ("/v1" if kind == ProviderKind.OPENAI else "")
    config = ProviderConfig(kind=kind, base_url=base_url, api_key=SecretStr(api_key))

    response = make_chat_model(config, "test-model").chat(
        "", [Message(role="user", content="Hello")], [], max_tokens=16
    )

    assert response.text == "Hello."
    assert [request.path for request in local_server.requests] == [CHAT_PATHS[kind]]
    headers = local_server.requests[0].headers
    return headers.get("x-api-key") or headers["authorization"].removeprefix("Bearer ")


def test_provider_without_api_key_gets_the_request(
    local_server: LocalServer, kind: ProviderKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Local servers need no key. The real HTTP stack refuses to send an empty
    # bearer token, and the credentials of the host must not go to the Provider.
    host_secret = "sbb-synthetic-host-secret"
    for variable in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.setenv(variable, host_secret)

    sent_key = chat_with_local_server(local_server, kind, "")

    assert host_secret not in sent_key


def test_api_key_with_surrounding_whitespace_gets_the_request(
    local_server: LocalServer, kind: ProviderKind
) -> None:
    # The real HTTP stack refuses to send a header value with a newline.
    sent_key = chat_with_local_server(local_server, kind, f" {API_KEY}\n")

    assert sent_key == API_KEY


def test_httpx_transport_raises_type_error(kind: ProviderKind) -> None:
    # The SDKs use httpx2. An httpx transport would fail later with a bare AssertionError.
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))

    with pytest.raises(TypeError, match="httpx2"):
        make_chat_model(make_config(kind), "test-model", transport=transport)  # type: ignore[arg-type]


def test_html_answer_raises_response_error(fake: FakeProvider, kind: ProviderKind) -> None:
    # A wrong base URL can point at a web page that answers 200.
    fake.reply("POST", CHAT_PATHS[kind], "<html><body>Welcome</body></html>")

    with pytest.raises(ProviderResponseError, match="Check the base URL"):
        say_hello(make_config(kind), fake)


# --- list_models ---------------------------------------------------------------------


def test_openai_list_models(fake: FakeProvider) -> None:
    fake.reply(
        "GET",
        "/v1/models",
        {
            "object": "list",
            "data": [
                {"id": "chat-large", "object": "model", "created": 0, "owned_by": "example"},
                {"id": "whisper-test", "object": "model", "created": 0, "owned_by": "example"},
            ],
        },
    )

    models = list_models(make_config(ProviderKind.OPENAI), transport=fake.transport)

    assert models == ["chat-large", "whisper-test"]


def test_anthropic_list_models_reads_every_page(fake: FakeProvider) -> None:
    fake.reply(
        "GET",
        "/v1/models",
        {
            "data": [{"type": "model", "id": "model-new", "display_name": "New"}],
            "has_more": True,
            "first_id": "model-new",
            "last_id": "model-new",
        },
    )
    fake.reply(
        "GET",
        "/v1/models",
        {
            "data": [{"type": "model", "id": "model-old", "display_name": "Old"}],
            "has_more": False,
            "first_id": "model-old",
            "last_id": "model-old",
        },
    )
    # Users often paste the base URL with /v1; the SDK adds /v1 itself.
    config = make_config(ProviderKind.ANTHROPIC, "https://claude.example.com/v1/")

    models = list_models(config, transport=fake.transport)

    assert models == ["model-new", "model-old"]
    assert "after_id" not in fake.requests[0].url.params
    assert fake.requests[1].url.params["after_id"] == "model-new"


def test_list_models_waits_less_than_chat(fake: FakeProvider, kind: ProviderKind) -> None:
    # The app waits for the model list while it answers an HTTP request.
    fake.reply("GET", "/v1/models", {"data": [{"id": "chat-large"}], "has_more": False})
    ok_reply(fake, kind)

    list_models(make_config(kind), transport=fake.transport)
    say_hello(make_config(kind), fake)

    list_timeout, chat_timeout = (request.extensions["timeout"] for request in fake.requests)
    assert list_timeout["read"] == LIST_MODELS_TIMEOUT.read
    assert chat_timeout["read"] == REQUEST_TIMEOUT.read
    assert list_timeout["read"] < chat_timeout["read"]


def test_list_models_with_wrong_base_url_raises_not_found(fake: FakeProvider) -> None:
    fake.reply("GET", "/models", "404 page not found", status=404)
    config = make_config(ProviderKind.OPENAI, "http://localhost:11434")

    with pytest.raises(ProviderNotFoundError, match="Check the base URL"):
        list_models(config, transport=fake.transport)


# --- transcription -------------------------------------------------------------------


def test_transcription_maps_verbose_json_segments(fake: FakeProvider, tmp_path: Path) -> None:
    audio = tmp_path / "lecture.mp3"
    audio.write_bytes(b"ID3 synthetic audio bytes")
    fake.reply(
        "POST",
        "/v1/audio/transcriptions",
        {
            "task": "transcribe",
            "language": "portuguese",
            "duration": 6.2,
            "text": "Bom dia. Hoje falamos de folhas.",
            "segments": [
                {"id": 0, "seek": 0, "start": 0.0, "end": 2.5, "text": " Bom dia."},
                {"id": 1, "seek": 0, "start": 2.5, "end": 3.0, "text": "  "},
                {"id": 2, "seek": 0, "start": 3.0, "end": 6.2, "text": " Hoje falamos de folhas."},
            ],
        },
    )
    transcriber = make_transcriber(
        make_config(ProviderKind.OPENAI), "whisper-test", transport=fake.transport
    )

    segments = transcriber.transcribe(audio, "pt-BR")

    assert segments == [
        Segment(start=0.0, end=2.5, text="Bom dia."),
        Segment(start=3.0, end=6.2, text="Hoje falamos de folhas."),
    ]
    fields = fake.multipart_fields(0)
    assert fields["model"] == (None, b"whisper-test")
    assert fields["response_format"] == (None, b"verbose_json")
    assert fields["language"] == (None, b"pt")
    assert fields["file"] == ("lecture.mp3", b"ID3 synthetic audio bytes")


def test_transcription_without_segments_gives_one_segment(
    fake: FakeProvider, tmp_path: Path
) -> None:
    audio = tmp_path / "note.wav"
    audio.write_bytes(b"RIFF synthetic audio bytes")
    fake.reply("POST", "/v1/audio/transcriptions", {"text": " Water the plants. ", "duration": 1.5})
    transcriber = make_transcriber(
        make_config(ProviderKind.OPENAI), "whisper-test", transport=fake.transport
    )

    segments = transcriber.transcribe(audio, None)

    assert segments == [Segment(start=0.0, end=1.5, text="Water the plants.")]
    assert "language" not in fake.multipart_fields(0)


def test_anthropic_provider_cannot_transcribe() -> None:
    with pytest.raises(ValueError, match="OpenAI-compatible"):
        make_transcriber(make_config(ProviderKind.ANTHROPIC), "any-model")
