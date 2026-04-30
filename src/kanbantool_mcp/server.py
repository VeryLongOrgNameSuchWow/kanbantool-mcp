"""FastMCP server definition for kanbantool-mcp."""

from __future__ import annotations

from datetime import datetime

from fastmcp import FastMCP

from .client import KanbanToolClient
from .config import Config
from .models import Board, ChangelogEntry, Task

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


# Hard ceiling on a single page. Defends against an LLM hallucinating a huge
# limit and pulling far more than the API or the agent's context can sanely
# absorb in one round-trip. Pagination via ``page`` is the supported way to
# walk past it.
_SEARCH_TASKS_MAX_LIMIT = 50


@mcp.tool
async def search_tasks(
    query: str,
    board_id: int | None = None,
    limit: int = 25,
    page: int = 1,
) -> list[Task]:
    """Search tasks across boards using Kanban Tool's query DSL.

    ``query`` is forwarded to the API verbatim — do not URL-encode it,
    do not wrap the whole expression in quotes, and do not reconstruct it
    from individual operator keyword arguments. Quote a single value only
    when it contains spaces (e.g. ``name:"ship the thing"``).

    Supported operators (combine with spaces; terms are AND-ed together):

    - ``@username``           — assignee, e.g. ``@alice``
    - ``name:<text>``         — title contains, e.g. ``name:"deploy script"``
    - ``priority:<level>``    — priority level, e.g. ``priority:high``
    - ``tags:<tag>``          — tag match, e.g. ``tags:bug``
    - ``due_date=<iso-date>`` — due-date equality, e.g. ``due_date=2026-05-01``
    - ``subtasks_count<N>``   — also ``>``, ``=``; e.g. ``subtasks_count>0``
    - ``archived:<bool>``     — include archived, e.g. ``archived:true``

    If the user asks for something the operators above don't cover (full-text
    search of comments, fuzzy matching, etc.), say so plainly rather than
    inventing syntax — the API will silently return zero results for unknown
    operators.

    ``board_id`` scopes the search to a single board when provided; omit it
    to search every board the token can see. ``limit`` is clamped to
    ``50`` server-side here; use ``page`` (1-indexed) to walk further results.
    """
    capped_limit = min(limit, _SEARCH_TASKS_MAX_LIMIT)
    params: dict[str, str | int] = {
        "query": query,
        "limit": capped_limit,
        "page": page,
    }
    if board_id is not None:
        params["board_id"] = board_id

    data = await _get_client().request("GET", "tasks/search", params=params)
    raw = data.get("tasks", []) if isinstance(data, dict) else []
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed search payload").
    return [Task.model_validate(t) for t in raw]


def run() -> None:
    mcp.run()
