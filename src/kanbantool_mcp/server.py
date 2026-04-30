"""FastMCP server definition for kanbantool-mcp."""

from __future__ import annotations

from datetime import datetime

from fastmcp import FastMCP

from .client import KanbanToolClient
from .config import Config
from .models import Board, ChangelogEntry

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


@mcp.tool
async def get_board(board_id: int) -> Board:
    """Fetch a board with its columns, swimlanes, and custom-field definitions."""
    data = await _get_client().request("GET", f"boards/{board_id}")
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed board payload").
    return Board.model_validate(data)


@mcp.tool
async def recent_changes(board_id: int, since: datetime | None = None) -> list[ChangelogEntry]:
    """Fetch the changelog feed for a board — the change-tracking primitive
    that stands in for webhooks (Kanban Tool ships none).

    Poll sparingly — typical cadence 30-120s, not per-keystroke. Always pass
    ``since`` (timestamp of the last entry seen) on subsequent calls; omitting
    it returns the full history, which can be very large. Returns entries
    newest-first per the API."""
    params = {"since": since.isoformat()} if since is not None else None
    data = await _get_client().request("GET", f"boards/{board_id}/changelog", params=params)
    raw = data.get("changelog", []) if isinstance(data, dict) else []
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed changelog payload").
    return [ChangelogEntry.model_validate(entry) for entry in raw]


def run() -> None:
    mcp.run()
