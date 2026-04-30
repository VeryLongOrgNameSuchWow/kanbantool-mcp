"""FastMCP server definition for kanbantool-mcp."""

from __future__ import annotations

from fastmcp import FastMCP

from .client import KanbanToolClient
from .config import Config
from .models import Board, Task

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
async def get_task(task_id: int) -> Task:
    """Fetch a task by id.

    Surfaces the task's headline metadata along with subtask count, comment
    count, and total tracked time. Use the dedicated subtask/comment/time
    tools to drill into the nested collections.
    """
    data = await _get_client().request("GET", f"tasks/{task_id}")
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed task payload").
    return Task.model_validate(data)


def run() -> None:
    mcp.run()
