"""FastAPI app, routers, response schemas, auth, and the static web UI.

``create_app`` builds the app; run it with
``uvicorn --factory second_brain_builder.api.app:create_app``. ``mount_mcp`` serves
an MCP server at ``/mcp``.
"""

from second_brain_builder.api.app import create_app, mount_mcp
from second_brain_builder.api.state import McpFactory, McpMount, Principal, SecondBrainApp

__all__ = [
    "McpFactory",
    "McpMount",
    "Principal",
    "SecondBrainApp",
    "create_app",
    "mount_mcp",
]
