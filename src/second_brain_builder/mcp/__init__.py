"""MCP tools over Streamable HTTP.

Build the API with ``create_app(context=ctx, mcp=create_mcp_mount)``. The API's
security middleware checks every request's Host and API token before dispatch.
"""

from second_brain_builder.mcp.server import create_mcp_mount

__all__ = ["create_mcp_mount"]
