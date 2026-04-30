"""FastMCP server definition for kanbantool-mcp."""

from __future__ import annotations

from fastmcp import FastMCP

mcp: FastMCP = FastMCP("kanbantool-mcp")


@mcp.tool
def ping() -> str:
    """Return ``pong``. Useful as a smoke test for the MCP transport."""
    return "pong"


def run() -> None:
    mcp.run()
