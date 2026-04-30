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
    """Fetch a board with its columns, swimlanes, and custom-field definitions."""
    # ``validate_call`` enforces ``ge=1`` on direct callers (and tests) so a
    # bogus 0/-N never reaches the API as a confusing 404. FastMCP also
    # validates this from the wire side via the JSON schema.
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
    """Create a new task on a board.

    ``name`` and ``board_id`` are required; everything else is optional and
    omitted from the request when left unset (the API may treat an explicit
    ``null`` as a clear, which is rarely what a caller wants on create).

    ``lane_id`` is the column / workflow stage the card lands in — pass the
    same id you'd see on a fetched ``Task.lane_id``. ``due_date`` is an ISO
    8601 string forwarded verbatim. ``priority`` accepts either the string
    enum or the raw integer some accounts use. ``tags`` is a comma-separated
    string per the API's wire format.

    Raises ``KanbanToolValidationError`` (a subclass of ``KanbanToolHTTPError``)
    on a 422 with parsed ``field_errors``; ``KanbanToolHTTPError`` on other
    4xx/5xx; ``KanbanToolPermissionError`` on 401/403.
    """
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
    """Update an existing task's fields. Partial — only the kwargs the caller
    passes are sent; everything else is left untouched.

    Field set mirrors ``create_task``. ``lane_id`` is the column / workflow
    stage id; on the wire it maps to ``workflow_stage_id`` (matching the
    inbound alias on ``Task``). ``priority`` accepts either the string enum or
    the raw integer some accounts use. ``tags`` is a comma-separated string
    per the API's wire format. Date fields are ISO 8601 strings forwarded
    verbatim.

    ``None`` means *omit*, not *clear*. Kanban Tool's PUT is Rails-style
    strong params and ignores ``null`` rather than wiping a field, so this
    tool deliberately drops unset kwargs from the body. A future PR can add
    an explicit ``clear_fields`` mechanism if/when callers actually need to
    null fields out.

    For moving a card between columns or swimlanes, prefer ``move_task`` —
    it's the dedicated, intent-revealing surface for that workflow even
    though the wire call overlaps.

    Raises ``ValueError`` if no updatable field is provided (so the LLM gets
    an actionable error instead of a no-op round trip). Raises
    ``KanbanToolValidationError`` (a subclass of ``KanbanToolHTTPError``) on
    a 422 with parsed ``field_errors``; ``KanbanToolHTTPError`` on other
    4xx/5xx; ``KanbanToolPermissionError`` on 401/403.
    """
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

    At least one of ``column_id`` / ``swimlane_id`` / ``position`` must be
    provided; calling with all three unset raises ``ValueError`` before any
    HTTP is issued.

    There is no dedicated ``/move`` endpoint in Kanban Tool API v3 — column
    and lane transitions are expressed as a partial update on the task. This
    tool exists as a separate, intent-revealing surface so the LLM picks the
    right tool for "move this card", and the implementation rides on the
    same patch helper as ``update_task``.

    ``column_id`` is the caller-facing alias for the wire's
    ``workflow_stage_id`` (mirroring how ``lane_id`` is exposed elsewhere) —
    pass the same id you'd see on a fetched ``Task.lane_id``.

    Raises ``KanbanToolValidationError`` (a subclass of ``KanbanToolHTTPError``)
    on a 422 with parsed ``field_errors`` — the typical case is a column id
    that doesn't belong to this task's board, surfaced via the existing error
    pipeline. Raises ``KanbanToolHTTPError`` on other 4xx/5xx;
    ``KanbanToolPermissionError`` on 401/403.
    """
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
    """Archive a task.

    Archiving on Kanban Tool v3 is a sentinel-action call rather than a
    field update: ``PATCH /tasks/{id}.json`` with the flat top-level body
    ``{"_action": "archive"}``. It does NOT go through the standard
    ``{"task": {...}}`` partial-update envelope, so this tool intentionally
    bypasses ``_patch_task`` and shapes the request itself. The same
    endpoint serves the symmetric ``unarchive``/``delete``/``undelete``
    actions (not currently exposed as tools).

    Idempotent by design: re-archiving an already-archived task issues
    the same PATCH and returns the task in its archived state. The wire
    is assumed to respond 200 with the archived ``Task`` regardless of
    prior state — verify M3: confirm against a real account that
    re-archiving returns 200 (not 4xx). If it does 4xx, add a status-only
    fallback (e.g. 409/422 → ``GET /tasks/{id}.json``) — never branch on
    response body wording, which is locale- and version-fragile.

    Returns the archived ``Task`` so the caller can confirm
    ``is_archived=True``.

    Raises ``KanbanToolHTTPError`` on 4xx/5xx (including a 404 if the task
    id is unknown), and ``KanbanToolPermissionError`` on 401/403.
    """
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
    """Add a comment to a task.

    Posts ``text`` as a new comment on the task identified by ``task_id`` and
    returns the created ``Comment`` (id, text, author, timestamps).

    Raises ``KanbanToolValidationError`` (a subclass of ``KanbanToolHTTPError``)
    on a 422 with parsed ``field_errors``; ``KanbanToolHTTPError`` on other
    4xx/5xx; ``KanbanToolPermissionError`` on 401/403.
    """
    body = {"comment": {"text": text}}
    data = await _get_client().request("POST", f"tasks/{task_id}/comments", json=body)
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed comment payload").
    return Comment.model_validate(data)


@mcp.tool
async def list_subtasks(task_id: int) -> list[Subtask]:
    """List subtasks attached to a task.

    Returns each subtask's id, name, completion state, and position. Returns
    an empty list if the task has no subtasks (or if the API returns a body
    without a ``subtasks`` key — defensive parse mirroring ``list_boards``).
    """
    data = await _get_client().request("GET", f"tasks/{task_id}/subtasks")
    raw = data.get("subtasks", []) if isinstance(data, dict) else []
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed subtasks payload").
    return [Subtask.model_validate(s) for s in raw]


@mcp.tool
async def add_subtask(task_id: int, title: str) -> Subtask:
    """Add a subtask to a task.

    ``title`` is the human-readable label for the subtask; it maps to the
    API's ``name`` field on the wire. The kwarg is exposed as ``title`` to
    keep the LLM-facing surface consistent regardless of upstream naming.

    Raises ``KanbanToolValidationError`` (a subclass of ``KanbanToolHTTPError``)
    on a 422 with parsed ``field_errors``; ``KanbanToolHTTPError`` on other
    4xx/5xx; ``KanbanToolPermissionError`` on 401/403.
    """
    body = {"subtask": {"name": title}}
    data = await _get_client().request("POST", f"tasks/{task_id}/subtasks", json=body)
    # Trusting the bare-object response shape (mirrors get_task on main); no
    # defensive ``{"subtask": {...}}`` unwrap. M3 can revisit if the API ever
    # surprises us.
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed subtask payload").
    return Subtask.model_validate(data)


def run() -> None:
    mcp.run()
