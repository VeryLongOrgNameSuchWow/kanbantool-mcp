"""FastMCP server definition for kanbantool-mcp."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field, ValidationError, validate_call

from .client import KanbanToolClient
from .config import Config
from .exceptions import KanbanToolHTTPError
from .models import Board, ChangelogEntry, Comment, Subtask, Task

# Mirrors ``client._BODY_EXCERPT_LIMIT`` so payload-shape errors surface a
# truncated repr consistent with HTTP-error excerpts. Kept inline (rather than
# imported from ``client``) because that constant is module-private — exposing
# it would widen the API for one caller.
_PAYLOAD_EXCERPT_LIMIT = 200

mcp: FastMCP = FastMCP("kanbantool-mcp")

# Every tool below raises typed ``KanbanToolError`` subclasses on failure —
# ``KanbanToolPermissionError`` (401/403), ``KanbanToolValidationError`` (422
# with parsed ``field_errors``), or ``KanbanToolHTTPError`` (other 4xx/5xx).
# Docstrings call these out only where the failure mode meaningfully shapes
# tool selection or argument choice; see ``exceptions.py`` for the full ladder.

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
    """Smoke-test the MCP transport. Returns the literal string ``pong``."""
    return "pong"


@mcp.tool
async def list_boards() -> list[Board]:
    """List boards visible to the authenticated user. Use this to discover
    ``board_id`` values for the other tools."""
    data = await _get_client().request("GET", "users/current")
    raw = data.get("boards", []) if isinstance(data, dict) else []
    try:
        return [Board.model_validate(b) for b in raw]
    except ValidationError as exc:
        # The HTTP call succeeded (200) but a board entry was missing required
        # fields or otherwise malformed. Wrap as KanbanToolHTTPError so the
        # error surface stays consistent with 4xx/5xx flows — callers always
        # see a typed KanbanToolError, never a raw pydantic exception.
        excerpt = f"malformed boards payload: {exc!r}"[:_PAYLOAD_EXCERPT_LIMIT]
        raise KanbanToolHTTPError(
            "Kanban Tool API returned a malformed boards payload.",
            status_code=200,
            body_excerpt=excerpt,
        ) from exc


@mcp.tool
@validate_call
async def get_board(board_id: Annotated[int, Field(ge=1)]) -> Board:
    """Fetch one board with its columns, swimlanes, and custom-field definitions.

    Use this when you need column/lane ids for ``move_task`` or ``create_task``.
    Raises ``KanbanToolHTTPError(404)`` if the board id is unknown or hidden."""
    # ``validate_call`` enforces ``ge=1`` on direct callers (and tests) so a
    # bogus 0/-N never reaches the API as a confusing 404. FastMCP also
    # validates this from the wire side via the JSON schema.
    data = await _get_client().request("GET", f"boards/{board_id}")
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed board payload").
    return Board.model_validate(data)


@mcp.tool
async def get_task(task_id: int) -> Task:
    """Fetch one task by id. Returns a Task with subtask/comment counts and total tracked time.

    Use ``list_subtasks`` to drill into nested collections.
    Raises ``KanbanToolHTTPError(404)`` if the task is unknown or inaccessible."""
    data = await _get_client().request("GET", f"tasks/{task_id}")
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed task payload").
    return Task.model_validate(data)


@mcp.tool
async def recent_changes(board_id: int, since: datetime | None = None) -> list[ChangelogEntry]:
    """Fetch a board's changelog (Kanban Tool has no webhooks; poll this instead).

    Always pass ``since`` (timestamp of the last entry seen) on follow-up calls —
    omitting it returns the full history. Entries come newest-first.
    Poll sparingly: 30-120s cadence, not per-keystroke."""
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

    ``query`` is forwarded verbatim — do not URL-encode, do not wrap the whole
    expression in quotes. Quote individual values only when they contain spaces
    (e.g. ``name:"ship the thing"``). Terms combine with spaces and are AND-ed.

    Supported operators:

    - ``@username``           — assignee, e.g. ``@alice``
    - ``name:<text>``         — title contains
    - ``priority:<level>``    — e.g. ``priority:high``
    - ``tags:<tag>``          — tag match
    - ``due_date=<iso-date>`` — e.g. ``due_date=2026-05-01``
    - ``subtasks_count<N>``   — also ``>``, ``=``
    - ``archived:<bool>``     — include archived

    Unknown operators silently return zero results, so don't invent syntax for
    things the DSL doesn't cover (comment full-text, fuzzy match) — say so
    instead. ``board_id`` scopes to one board (omit to search all visible).
    ``limit`` is clamped to 50; paginate further with ``page`` (1-indexed).
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


@mcp.tool
async def create_task(
    name: str,
    board_id: int,
    description: str | None = None,
    lane_id: int | None = None,
    position: int | None = None,
    assignees: list[int] | None = None,
    due_date: str | None = None,
    priority: int | str | None = None,
    tags: str | None = None,
) -> Task:
    """Create a new task on a board. Only ``name`` and ``board_id`` are required.

    ``lane_id`` is the target column (matches ``Task.lane_id`` on fetched tasks).
    ``priority`` accepts the string enum or the raw integer; ``tags`` is a
    comma-separated string; ``due_date`` is an ISO 8601 string forwarded as-is.
    Unset kwargs are omitted from the request, never sent as explicit null."""
    payload: dict[str, Any] = {"name": name, "board_id": board_id}
    if description is not None:
        payload["description"] = description
    if lane_id is not None:
        # Caller-facing ``lane_id`` maps to the wire's ``workflow_stage_id``,
        # mirroring the inbound alias on the ``Task`` model.
        payload["workflow_stage_id"] = lane_id
    if position is not None:
        payload["position"] = position
    if assignees is not None:
        payload["assignees"] = assignees
    if due_date is not None:
        payload["due_date"] = due_date
    if priority is not None:
        payload["priority"] = priority
    if tags is not None:
        payload["tags"] = tags

    # M3: confirm against a real account that POST /tasks.json expects the
    # ``{"task": {...}}`` Rails-style envelope (vs. flat top-level fields).
    body = {"task": payload}
    data = await _get_client().request("POST", "tasks", json=body)
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed task payload").
    return Task.model_validate(data)


# Caller-facing aliases mapped to their wire names. ``lane_id`` and
# ``column_id`` are both ergonomic surfaces for the same wire field
# (``workflow_stage_id``); ``update_task`` exposes ``lane_id`` and
# ``move_task`` exposes ``column_id``, so the helper accepts either.
_PATCH_TASK_RENAMES: dict[str, str] = {
    "lane_id": "workflow_stage_id",
    "column_id": "workflow_stage_id",
}


async def _patch_task(
    task_id: int,
    fields: dict[str, Any],
    *,
    method: Literal["PUT", "PATCH"] = "PUT",
) -> Task:
    """Send a partial-update body to ``/tasks/{task_id}.json``.

    Shared between ``update_task`` (#9) and ``move_task`` (#10) — the two tools
    differ only in *which* fields they expose to the LLM, not in how the wire
    request is shaped. Centralizing the envelope/rename/empty-check logic here
    keeps both tools' bodies focused on input validation and docstrings.

    Behavior:

    - Drops keys whose value is ``None`` (treat ``None`` as "omit", not
      "clear" — Kanban Tool's partial update ignores nulls rather than
      clearing the column).
    - Renames caller-facing aliases to their wire names per
      ``_PATCH_TASK_RENAMES`` (e.g. ``lane_id``/``column_id`` →
      ``workflow_stage_id``), mirroring the inbound alias on the ``Task`` model
      and the outbound rename in ``create_task``.
    - Wraps the cleaned dict in the ``{"task": {...}}`` Rails envelope.
    - Raises ``ValueError`` if no fields remain after cleaning — issuing the
      request with an empty body would round-trip the task unchanged and
      consume a quota slot for nothing, so we reject it client-side. The
      message lists the caller's *own* field names (built from ``fields``
      before the None-skip pass), so each tool gets an actionable hint
      scoped to its surface.

    ``method`` selects between ``PUT`` (the default, used by ``update_task``)
    and ``PATCH`` (used by ``move_task``, matching the Kanban Tool API v3
    convention for partial updates). Both shapes are accepted by the API.

    Returns the updated ``Task`` parsed from the response body.
    """
    cleaned: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        wire_key = _PATCH_TASK_RENAMES.get(key, key)
        cleaned[wire_key] = value

    if not cleaned:
        # Build the message from the caller's *original* field names so the
        # error is scoped to whichever surface (update_task / move_task) the
        # LLM actually called.
        available = ", ".join(fields.keys())
        raise ValueError(f"No fields to update: pass at least one of {available}.")

    body = {"task": cleaned}
    data = await _get_client().request(method, f"tasks/{task_id}", json=body)
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed task payload").
    return Task.model_validate(data)


@mcp.tool
async def update_task(
    task_id: int,
    name: str | None = None,
    description: str | None = None,
    board_id: int | None = None,
    lane_id: int | None = None,
    swimlane_id: int | None = None,
    position: int | None = None,
    priority: int | str | None = None,
    color: str | None = None,
    due_date: str | None = None,
    start_date: str | None = None,
    tags: str | None = None,
    assignees: list[int] | None = None,
) -> Task:
    """Partially update a task's fields. Only the kwargs you pass are sent;
    ``None`` means *omit*, not *clear* (the API ignores nulls, doesn't wipe).

    Field set mirrors ``create_task``; same wire conventions for ``priority``,
    ``tags``, and date fields. For column/lane/position changes prefer
    ``move_task`` — it's the intent-revealing surface for that workflow.
    Raises ``ValueError`` if every field is ``None`` (no-op guard)."""
    return await _patch_task(
        task_id,
        {
            "name": name,
            "description": description,
            "board_id": board_id,
            "lane_id": lane_id,
            "swimlane_id": swimlane_id,
            "position": position,
            "priority": priority,
            "color": color,
            "due_date": due_date,
            "start_date": start_date,
            "tags": tags,
            "assignees": assignees,
        },
    )


@mcp.tool
async def move_task(
    task_id: int,
    column_id: int | None = None,
    swimlane_id: int | None = None,
    position: int | None = None,
) -> Task:
    """Move a task between columns, swimlanes, or positions on its board.

    At least one of ``column_id`` / ``swimlane_id`` / ``position`` must be set,
    otherwise raises ``ValueError`` before issuing HTTP. ``column_id`` matches
    the ``Task.lane_id`` on fetched tasks. Passing a column id from a different
    board raises ``KanbanToolValidationError`` with parsed ``field_errors``."""
    return await _patch_task(
        task_id,
        {
            "column_id": column_id,
            "swimlane_id": swimlane_id,
            "position": position,
        },
        method="PATCH",
    )


@mcp.tool
async def archive_task(task_id: int) -> Task:
    """Archive a task. Returns the updated ``Task`` (caller can confirm
    ``is_archived=True``).

    Idempotent: re-archiving an already-archived task succeeds. There is no
    ``unarchive_task`` yet — archiving is currently one-way from this surface.
    Raises ``KanbanToolHTTPError(404)`` if the task id is unknown."""
    # Sentinel-action family on PATCH /tasks/{id}.json: "archive",
    # "unarchive", "delete", "undelete". If we ever add unarchive_task /
    # delete_task / restore_task, copy this 2-line shape — don't generalize
    # into a helper until there's a second caller (YAGNI).
    body = {"_action": "archive"}
    data = await _get_client().request("PATCH", f"tasks/{task_id}", json=body)
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed task payload").
    return Task.model_validate(data)


@mcp.tool
async def add_comment(task_id: int, text: str) -> Comment:
    """Post a comment on a task. Returns the created ``Comment`` with id,
    text, author, and timestamps. Empty ``text`` typically raises
    ``KanbanToolValidationError`` from the API."""
    body = {"comment": {"text": text}}
    data = await _get_client().request("POST", f"tasks/{task_id}/comments", json=body)
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed comment payload").
    return Comment.model_validate(data)


@mcp.tool
async def list_subtasks(task_id: int) -> list[Subtask]:
    """List subtasks on a task — id, name, completion state, position.

    Returns an empty list when the task has none. Use ``get_task`` first if
    you only need the subtask count rather than the full list."""
    data = await _get_client().request("GET", f"tasks/{task_id}/subtasks")
    raw = data.get("subtasks", []) if isinstance(data, dict) else []
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed subtasks payload").
    return [Subtask.model_validate(s) for s in raw]


@mcp.tool
async def add_subtask(task_id: int, title: str) -> Subtask:
    """Add a subtask to a task. Returns the created ``Subtask``.

    ``title`` is the human-readable label. Empty ``title`` typically raises
    ``KanbanToolValidationError`` from the API."""
    body = {"subtask": {"name": title}}
    data = await _get_client().request("POST", f"tasks/{task_id}/subtasks", json=body)
    # Trusting the bare-object response shape (mirrors get_task on main); no
    # defensive ``{"subtask": {...}}`` unwrap. M3 can revisit if the API ever
    # surprises us.
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed subtask payload").
    return Subtask.model_validate(data)


def run() -> None:
    mcp.run()
