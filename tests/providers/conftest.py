"""Check the tool pairing contract on every outgoing Anthropic request."""

import json
from typing import Any

import httpx2
import pytest


def assert_anthropic_tool_pairing(body: dict[str, Any]) -> None:
    turns = body["messages"]
    for index, turn in enumerate(turns):
        if turn["role"] != "assistant" or not isinstance(turn["content"], list):
            continue
        call_ids = {block["id"] for block in turn["content"] if block["type"] == "tool_use"}
        if not call_ids:
            continue
        assert index + 1 < len(turns), "tool_use has no following turn"
        following = turns[index + 1]
        assert following["role"] == "user", "tool_use must be followed by a user turn"
        assert isinstance(following["content"], list)
        result_ids = {
            block["tool_use_id"] for block in following["content"] if block["type"] == "tool_result"
        }
        assert call_ids <= result_ids, "tool_use has no result in the immediately following turn"
        seen_text = False
        for block in following["content"]:
            if block["type"] == "tool_result":
                assert not seen_text, "tool results must precede user text"
            else:
                seen_text = True


@pytest.fixture(autouse=True)
def check_anthropic_request_pairing(monkeypatch: pytest.MonkeyPatch) -> None:
    send = httpx2.Client.send

    def checked_send(
        client: httpx2.Client, request: httpx2.Request, *args: Any, **kwargs: Any
    ) -> httpx2.Response:
        if request.method == "POST" and request.url.path.endswith("/v1/messages"):
            assert_anthropic_tool_pairing(json.loads(request.content))
        return send(client, request, *args, **kwargs)

    monkeypatch.setattr(httpx2.Client, "send", checked_send)
