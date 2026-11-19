"""Changes to the generated OpenAPI document that FastAPI cannot express on a route.

FastAPI documents every response model of a route under the route's response
media type. ``GET /api/events`` returns a ``text/event-stream`` response from a JSON
route, so that its errors stay ``application/json``; this module then moves the
documented ``JobEventView`` to ``text/event-stream``, as the item schema of the
stream (OpenAPI 3.2 ``itemSchema``).
"""

from __future__ import annotations

from typing import Any

EVENTS_PATH = "/api/events"


def document_event_stream(schema: dict[str, Any]) -> None:
    """Describe the 200 response of ``GET /api/events`` as Server-Sent Events."""
    content: dict[str, Any] = schema["paths"][EVENTS_PATH]["get"]["responses"]["200"]["content"]
    json_content = content.pop("application/json")
    content["text/event-stream"] = {
        "schema": {"type": "string"},
        "itemSchema": {
            "type": "object",
            "required": ["id", "data"],
            "properties": {
                "id": {"type": "string", "description": "The event id."},
                "data": {
                    "type": "string",
                    "contentMediaType": "application/json",
                    "contentSchema": json_content["schema"],
                },
            },
        },
    }
