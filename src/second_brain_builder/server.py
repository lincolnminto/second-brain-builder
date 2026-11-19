"""Production entry point: the HTTP API, the web UI, and the MCP server at ``/mcp``.

Run it with ``uvicorn second_brain_builder.server:create_server --factory``. The
``api`` package does not import ``mcp``; this module composes the two.
"""

from __future__ import annotations

from second_brain_builder.api import SecondBrainApp, create_app
from second_brain_builder.mcp import create_mcp_mount


def create_server() -> SecondBrainApp:
    """The app with settings from the environment and the MCP server mounted."""
    return create_app(mcp=create_mcp_mount)
