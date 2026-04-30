"""FastMCP server definition for kanbantool-mcp."""

from __future__ import annotations

from fastmcp import FastMCP

from .client import KanbanToolClient
from .config import Config
from .models import Board

mcp: FastMCP = FastMCP("kanbantool-mcp")

# Module-level singleton. The stdio MCP runs in a single asyncio loop on a
# single thread, so no lock is needed around lazy init.
_client: KanbanToolClient | None = None


def _get_client() -> KanbanToolClient:
    global _client
    if _client is None:
        _client = KanbanToolClient(Config.from_env())
    return _client


@mcp.tool
def ping() -> str:
    """Return ``pong``. Useful as a smoke test for the MCP transport."""
    return "pong"


@mcp.tool
async def list_boards() -> list[Board]:
    """List boards visible to the authenticated user."""
    data = await _get_client().request("GET", "users/current")
    raw = data.get("boards", []) if isinstance(data, dict) else []
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed boards payload").
    return [Board.model_validate(b) for b in raw]


def run() -> None:
    mcp.run()
