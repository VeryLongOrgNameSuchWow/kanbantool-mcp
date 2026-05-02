"""FastMCP server definition for kanbantool-mcp."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypeVar

from fastmcp import FastMCP
from pydantic import BaseModel, Field, ValidationError, validate_call

from .client import KanbanToolClient
from .config import Config
from .exceptions import KanbanToolHTTPError
from .models import (
    Board,
    ChangelogEntry,
    Collaborator,
    Comment,
    CustomFieldDefinition,
    Subtask,
    Task,
    TimeTracker,
    User,
)

# Mirrors ``client._BODY_EXCERPT_LIMIT`` so payload-shape errors surface a
# truncated repr consistent with HTTP-error excerpts. Kept inline (rather than
# imported from ``client``) because that constant is module-private — exposing
# it would widen the API for one caller.
_PAYLOAD_EXCERPT_LIMIT = 200

mcp: FastMCP = FastMCP("kanbantool-mcp")

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _decode(model: type[_ModelT], data: Any, *, label: str) -> _ModelT:
    """Validate ``data`` as ``model`` or raise ``KanbanToolHTTPError``.

    The HTTP call has already succeeded (200), but the body's shape doesn't
    match what we expect. Wrapping the raw ``pydantic.ValidationError`` keeps
    the error surface consistent with 4xx/5xx flows — callers always see a
    typed ``KanbanToolError``, never a pydantic exception leaking through.
    """
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        excerpt = f"malformed {label} payload: {exc!r}"[:_PAYLOAD_EXCERPT_LIMIT]
        raise KanbanToolHTTPError(
            f"Kanban Tool API returned a malformed {label} payload.",
            status_code=200,
            body_excerpt=excerpt,
        ) from exc


def _decode_list(model: type[_ModelT], data: Any, *, label: str) -> list[_ModelT]:
    """List counterpart of ``_decode`` — validates each element together so a
    single bad entry surfaces one wrapped error rather than a partial list."""
    try:
        return [model.model_validate(item) for item in data]
    except ValidationError as exc:
        excerpt = f"malformed {label} payload: {exc!r}"[:_PAYLOAD_EXCERPT_LIMIT]
        raise KanbanToolHTTPError(
            f"Kanban Tool API returned a malformed {label} payload.",
            status_code=200,
            body_excerpt=excerpt,
        ) from exc


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
    return _decode_list(Board, raw, label="boards")


@mcp.tool
async def whoami() -> User:
    """Fetch the authenticated user's profile.

    Returns the ``User`` you're acting as — id, name, role flags, locale,
    timezone. Use this to resolve "me" / "myself" references in user
    requests (``assign to me`` → ``assigned_user_id`` from this response)
    or to show the LLM whose perspective it's operating from."""
    data = await _get_client().request("GET", "users/current")
    return _decode(User, data, label="user")


@mcp.tool
@validate_call
async def get_user(user_id: Annotated[int, Field(ge=1)]) -> User:
    """Fetch one user by id. Useful after ``list_board_collaborators`` finds
    a candidate by name — call this to confirm role flags and active state
    before assigning. Raises ``KanbanToolHTTPError(404)`` for unknown ids."""
    data = await _get_client().request("GET", f"users/{user_id}")
    return _decode(User, data, label="user")


@mcp.tool
@validate_call
async def list_board_collaborators(
    board_id: Annotated[int, Field(ge=1)],
) -> list[Collaborator]:
    """List the users with access to ``board_id``.

    The Kanban Tool API v3 has no bulk list-users endpoint, so this is the
    canonical way to discover user IDs for ``assigned_user_id`` on tasks.
    Costs one HTTP call (the same as ``get_board`` — collaborators come
    inline on the detail payload). For richer per-user fields, follow up
    with ``get_user(id)``."""
    board = await get_board(board_id)
    return board.collaborators


@mcp.tool
@validate_call
async def list_custom_field_definitions(
    board_id: Annotated[int, Field(ge=1)],
) -> list[CustomFieldDefinition]:
    """List the per-board metadata for the 15 custom-field slots.

    Each task has up to 15 ``custom_field_N`` values surfaced as
    ``Task.custom_fields["custom_field_N"]``. The slot number alone tells
    you nothing about what's IN it on a given board — call this tool once
    per board to learn the labels, types, and enabled state, then
    interpret task values accordingly.

    Returns the 15 definitions in slot order (1..15) regardless of which
    are ``enabled``. Slots with ``enabled=False`` are usually dormant on
    that board's UI even if individual tasks happen to carry values."""
    # Sourced from ``Board.card_template[custom_field_N]`` — same one
    # HTTP call as ``get_board`` (the metadata is inline on the detail
    # payload). The tool is sugar that filters to the custom-field slots
    # and adds the explicit slot number for caller convenience.
    board = await get_board(board_id)
    template = board.card_template or {}
    definitions: list[CustomFieldDefinition] = []
    for key, defn in template.items():
        if not key.startswith("custom_field_"):
            continue
        if not isinstance(defn, dict):
            # Defensive against unexpected card_template shapes; skip
            # silently rather than fail the whole call.
            continue
        try:
            slot = int(key[len("custom_field_") :])
        except ValueError:
            continue
        # The wire entry has ``label``/``type``/``enabled``/``options``/
        # ``position`` already; we add the ``slot`` since the dict key is
        # the only place it lived.
        definitions.append(
            _decode(CustomFieldDefinition, {**defn, "slot": slot}, label="custom field")
        )
    definitions.sort(key=lambda d: d.slot)
    return definitions


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
    return _decode(Board, data, label="board")


@mcp.tool
@validate_call
async def get_task(task_id: Annotated[int, Field(ge=1)]) -> Task:
    """Fetch one task by id. Returns a Task with subtask/comment counts,
    total tracked time, and inline ``subtasks``.

    Subtasks live on ``Task.subtasks`` directly — no extra round-trip needed
    (use ``list_subtasks`` only when you want just the list and not the rest
    of the task).

    Raises ``KanbanToolHTTPError(404)`` if the task is unknown or inaccessible."""
    data = await _get_client().request("GET", f"tasks/{task_id}")
    return _decode(Task, data, label="task")


@mcp.tool
@validate_call
async def recent_changes(
    board_id: Annotated[int, Field(ge=1)], since: datetime | None = None
) -> list[ChangelogEntry]:
    """Fetch a board's changelog (Kanban Tool has no webhooks; poll this instead).

    Always pass ``since`` (timestamp of the last entry seen) on follow-up calls —
    omitting it returns the full history. Entries come newest-first.
    Poll sparingly: 30-120s cadence, not per-keystroke."""
    params = {"since": since.isoformat()} if since is not None else None
    data = await _get_client().request("GET", f"boards/{board_id}/changelog", params=params)
    return _decode_list(ChangelogEntry, data, label="changelog")


# Hard ceiling on a single page. Defends against an LLM hallucinating a huge
# limit and pulling far more than the API or the agent's context can sanely
# absorb in one round-trip. Pagination via ``page`` is the supported way to
# walk past it.
_SEARCH_TASKS_MAX_LIMIT = 50


@mcp.tool
@validate_call
async def search_tasks(
    query: str,
    board_id: Annotated[int, Field(ge=1)] | None = None,
    limit: Annotated[int, Field(ge=1)] = 25,
    page: Annotated[int, Field(ge=1)] = 1,
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
    # When ``limit``/``page`` are supplied (always, here), the API wraps
    # results in ``{"results": [...], "pagination": {...}}``. Without those
    # params it returns a bare list — but we always paginate.
    raw = data.get("results", []) if isinstance(data, dict) else []
    return _decode_list(Task, raw, label="search")


@mcp.tool
@validate_call
async def create_task(
    name: str,
    board_id: Annotated[int, Field(ge=1)],
    description: str | None = None,
    lane_id: Annotated[int, Field(ge=1)] | None = None,
    position: int | None = None,
    assigned_user_id: Annotated[int, Field(ge=1)] | None = None,
    due_date: str | None = None,
    priority: int | str | None = None,
    tags: str | None = None,
) -> Task:
    """Create a new task on a board. Only ``name`` and ``board_id`` are required.

    ``lane_id`` is the target column (matches ``Task.lane_id`` on fetched tasks).
    ``assigned_user_id`` sets the single assignee — Kanban Tool tasks have one
    assignee, not a list. (The API silently ignores a legacy ``assignees: [int]``
    payload on writes, so this kwarg is the wire field name directly.)
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
    if assigned_user_id is not None:
        payload["assigned_user_id"] = assigned_user_id
    if due_date is not None:
        payload["due_date"] = due_date
    if priority is not None:
        payload["priority"] = priority
    if tags is not None:
        payload["tags"] = tags

    # ``tasks/`` endpoints take a Rails-style envelope: ``{"task": {...}}``.
    # Confirmed via the #62 live-API spike — flat top-level fields are
    # silently ignored. (Note: this convention does NOT carry over to
    # subtasks; ``POST /subtasks.json`` is flat per ``add_subtask``.)
    body = {"task": payload}
    data = await _get_client().request("POST", "tasks", json=body)
    return _decode(Task, data, label="task")


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
        raise ValueError(
            f"No fields to update: pass at least one of {available}. "
            "Note: passing None means 'omit', not 'clear' — the API ignores nulls."
        )

    body = {"task": cleaned}
    data = await _get_client().request(method, f"tasks/{task_id}", json=body)
    return _decode(Task, data, label="task")


@mcp.tool
@validate_call
async def update_task(
    task_id: Annotated[int, Field(ge=1)],
    name: str | None = None,
    description: str | None = None,
    board_id: Annotated[int, Field(ge=1)] | None = None,
    lane_id: Annotated[int, Field(ge=1)] | None = None,
    swimlane_id: Annotated[int, Field(ge=1)] | None = None,
    position: int | None = None,
    priority: int | str | None = None,
    color: str | None = None,
    due_date: str | None = None,
    start_date: str | None = None,
    tags: str | None = None,
    assigned_user_id: Annotated[int, Field(ge=1)] | None = None,
) -> Task:
    """Partially update a task's fields. Only the kwargs you pass are sent;
    ``None`` means *omit*, not *clear* (the API ignores nulls, doesn't wipe).

    Field set mirrors ``create_task``; same wire conventions for ``priority``,
    ``tags``, and date fields. ``assigned_user_id`` sets the single assignee —
    Kanban Tool tasks have one assignee, not a list. For column/lane/position
    changes prefer ``move_task`` — it's the intent-revealing surface for that
    workflow.
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
            "assigned_user_id": assigned_user_id,
        },
    )


@mcp.tool
@validate_call
async def move_task(
    task_id: Annotated[int, Field(ge=1)],
    column_id: Annotated[int, Field(ge=1)] | None = None,
    swimlane_id: Annotated[int, Field(ge=1)] | None = None,
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
@validate_call
async def archive_task(task_id: Annotated[int, Field(ge=1)]) -> Task:
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
    return _decode(Task, data, label="task")


@mcp.tool
@validate_call
async def set_custom_field(
    task_id: Annotated[int, Field(ge=1)],
    slot: Annotated[int, Field(ge=1, le=15)],
    value: str | int | float | bool | None,
) -> Task:
    """Set or clear one of the 15 ``custom_field_*`` slots on a task.

    ``slot`` selects which numbered slot to write (1..15 inclusive — the
    Kanban Tool API exposes exactly 15). Pair with
    ``list_custom_field_definitions(board_id)`` to learn each slot's label
    and type on a given board before writing.

    ``value`` is sent verbatim — strings, numbers, and booleans all work.
    Pass ``value=None`` to **clear** the slot: the wire body explicitly sends
    ``null`` (not omits the key), and a subsequent ``get_task`` will see
    ``custom_field_N: null``. This differs from ``update_task`` semantics
    where ``None`` means *omit*; for custom fields ``None`` means *clear*.

    Type mismatches (e.g. writing ``"hi"`` into a numeric slot) surface as
    ``KanbanToolValidationError`` (422) from the API, with parsed
    ``field_errors`` populated."""
    # NOTE: Deliberately does NOT route through ``_patch_task``. That helper
    # has None-skip semantics ("omit, don't clear"); here ``None`` MUST land
    # on the wire as a literal ``null`` to clear the field. Build the body
    # inline.
    body = {"task": {f"custom_field_{slot}": value}}
    data = await _get_client().request("PUT", f"tasks/{task_id}", json=body)
    return _decode(Task, data, label="task")


@mcp.tool
@validate_call
async def add_comment(task_id: Annotated[int, Field(ge=1)], content: str) -> Comment:
    """Post a comment on a task. Returns the created ``Comment`` with id,
    content, author, and timestamps. Empty ``content`` typically raises
    ``KanbanToolValidationError`` from the API; the ``field_errors`` key
    matches the parameter name (``"content"``)."""
    body = {"comment": {"content": content}}
    data = await _get_client().request("POST", f"tasks/{task_id}/comments", json=body)
    return _decode(Comment, data, label="comment")


@mcp.tool
@validate_call
async def delete_comment(
    task_id: Annotated[int, Field(ge=1)],
    comment_id: Annotated[int, Field(ge=1)],
) -> Comment:
    """Delete a comment (soft-delete). Returns the deleted ``Comment`` with
    ``deleted_at`` populated.

    Like subtasks, the Kanban Tool API soft-deletes — the comment record is
    retained server-side with a ``deleted_at`` timestamp and stops appearing
    on the parent task's comments. The MCP-visible effect is "the comment is
    gone." There is no edit endpoint on the API; if you need to "fix" a
    comment, delete it and post a replacement.

    Raises ``KanbanToolHTTPError(404)`` if either id is unknown."""
    data = await _get_client().request("DELETE", f"tasks/{task_id}/comments/{comment_id}")
    return _decode(Comment, data, label="comment")


@mcp.tool
@validate_call
async def list_subtasks(task_id: Annotated[int, Field(ge=1)]) -> list[Subtask]:
    """List subtasks on a task — id, name, completion state, position.

    Subtasks are returned inline on ``Task.subtasks`` whenever you fetch a
    task; this tool is sugar for callers that only want the list. Costs one
    HTTP call (the same as ``get_task``) — the Kanban Tool API has no
    dedicated list-subtasks endpoint."""
    task = await get_task(task_id)
    return task.subtasks


@mcp.tool
@validate_call
async def add_subtask(task_id: Annotated[int, Field(ge=1)], name: str) -> Subtask:
    """Add a subtask to a task. Returns the created ``Subtask``.

    ``name`` is the human-readable label. Empty ``name`` typically raises
    ``KanbanToolValidationError`` from the API. Mirrors the parameter name
    used by ``update_subtask`` and the wire/model field on ``Subtask.name``."""
    # Wire quirk: ``POST /subtasks.json`` takes a flat top-level body —
    # ``{"name": ..., "task_id": ...}`` — NOT the Rails-style ``{"subtask": {...}}``
    # envelope used by ``POST /tasks.json`` and friends. A spike against the
    # live API confirmed that wrapping in an envelope makes the API drop the
    # parent linkage (the response's ``task_id`` came back null and the
    # subtask never appeared on the parent task). Don't copy ``create_task``'s
    # shape here.
    body = {"name": name, "task_id": task_id}
    data = await _get_client().request("POST", "subtasks", json=body)
    return _decode(Subtask, data, label="subtask")


@mcp.tool
@validate_call
async def update_subtask(
    subtask_id: Annotated[int, Field(ge=1)],
    *,
    name: str | None = None,
    is_completed: bool | None = None,
    assigned_user_id: Annotated[int, Field(ge=1)] | None = None,
) -> Subtask:
    """Partial update of an existing subtask. Returns the updated ``Subtask``.

    Only kwargs the caller passes are sent — ``None`` means *omit*, not
    *clear*. Use this to mark complete (``is_completed=True``), rename
    (``name="..."``), or change the assignee (``assigned_user_id=42``).
    The ``position`` field is read-only on this endpoint; use
    ``reorder_subtasks`` to change ordering."""
    # Same flat-body convention as ``add_subtask`` — ``PATCH /subtasks/{id}.json``
    # does NOT take a ``{"subtask": {...}}`` envelope. Confirmed via live spike.
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if is_completed is not None:
        payload["is_completed"] = is_completed
    if assigned_user_id is not None:
        payload["assigned_user_id"] = assigned_user_id
    if not payload:
        raise ValueError(
            "No fields to update: pass at least one of name, is_completed, assigned_user_id. "
            "Note: passing None means 'omit', not 'clear' — the API ignores nulls."
        )
    data = await _get_client().request("PATCH", f"subtasks/{subtask_id}", json=payload)
    return _decode(Subtask, data, label="subtask")


@mcp.tool
@validate_call
async def delete_subtask(subtask_id: Annotated[int, Field(ge=1)]) -> Subtask:
    """Delete a subtask (soft-delete). Returns the deleted ``Subtask`` with
    ``deleted_at`` populated.

    The Kanban Tool API soft-deletes — the subtask record is retained
    server-side with a ``deleted_at`` timestamp and stops appearing on the
    parent task's ``subtasks`` array. This operation is not strictly
    irreversible from an audit perspective, but the MCP-visible effect is
    "the subtask is gone."

    Raises ``KanbanToolHTTPError(404)`` if the subtask id is unknown."""
    data = await _get_client().request("DELETE", f"subtasks/{subtask_id}")
    return _decode(Subtask, data, label="subtask")


@mcp.tool
@validate_call
async def reorder_subtasks(
    task_id: Annotated[int, Field(ge=1)],
    ids: list[int],
) -> list[Subtask]:
    """Reorder subtasks under a task. Returns the subtasks in the new order.

    ``ids`` must be the full set of subtask ids on ``task_id`` in the
    desired order. Passing a partial set, or ids belonging to a different
    task, raises ``KanbanToolValidationError`` from the API."""
    if not ids:
        raise ValueError(
            "reorder_subtasks requires at least one subtask id in `ids`. "
            "Use list_subtasks(task_id) to discover the current ids, "
            "then pass them in the desired order."
        )
    if len(set(ids)) != len(ids):
        # The API would 422 on duplicate ids; refusing client-side mirrors
        # the empty-list ``ValueError`` and surfaces the intent ("did you
        # mean to repeat that id?") instead of a typed-validation round-trip.
        raise ValueError(
            f"reorder_subtasks `ids` must be unique; got duplicates in {ids!r}. "
            "Each subtask id should appear exactly once in the desired order."
        )
    # Wire shape: ``PUT /subtasks/reorder.json`` takes ``{"task_id": int,
    # "ids": "comma,separated,string"}`` — the API expects a literal string
    # joined with commas, not a JSON array. Tool surface keeps the LLM-friendly
    # ``list[int]`` and joins on the way out.
    body = {"task_id": task_id, "ids": ",".join(str(i) for i in ids)}
    data = await _get_client().request("PUT", "subtasks/reorder", json=body)
    return _decode_list(Subtask, data, label="subtasks")


@mcp.tool
@validate_call
async def start_timer(
    task_id: Annotated[int, Field(ge=1)],
    board_id: Annotated[int, Field(ge=1)],
) -> TimeTracker:
    """Start a new time tracker on a task for the authenticated user.

    Returns the created ``TimeTracker``. Both ``task_id`` AND ``board_id``
    are required by the API — passing only one returns 404. The new timer
    starts in the running state (``ended_at`` is ``None``); call
    ``stop_timer`` when work pauses or ends.

    Note: timers are per-user — starting one creates a record for the
    authenticated user only. Use ``whoami`` if you need to know whose
    timer it is."""
    body = {"board_id": board_id, "task_id": task_id}
    data = await _get_client().request("POST", "time_trackers", json=body)
    return _decode(TimeTracker, data, label="time tracker")


@mcp.tool
@validate_call
async def stop_timer(
    timer_id: Annotated[int, Field(ge=1)],
    ended_at: str | None = None,
) -> TimeTracker:
    """Stop a running time tracker. Returns the stopped ``TimeTracker``.

    ``ended_at`` is an ISO 8601 timestamp; defaults to the current UTC
    time if not provided. Stopping an already-stopped timer is harmless —
    the API just updates the ``ended_at`` to the new value.

    Wire shape: ``PUT /time_trackers/{id}.json`` with a flat
    ``{"ended_at": ...}`` body. Same flat-body convention as the
    subtask endpoints — no ``{"time_tracker": {...}}`` envelope.

    Raises ``KanbanToolHTTPError(404)`` if the timer id is unknown or
    belongs to another user."""
    if ended_at is None:
        # Default to "now" so the LLM doesn't have to thread datetime
        # values through every stop call. ISO format with millisecond
        # precision + Z suffix matches what the API echoes on responses.
        ended_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    data = await _get_client().request(
        "PUT",
        f"time_trackers/{timer_id}",
        json={"ended_at": ended_at},
    )
    return _decode(TimeTracker, data, label="time tracker")


@mcp.tool
@validate_call
async def delete_timer(timer_id: Annotated[int, Field(ge=1)]) -> None:
    """Delete a time tracker entirely (e.g. cancel a mistakenly-started
    one). Unlike subtasks, timer deletion is hard — the record is gone,
    not soft-flagged.

    Returns ``None`` because the API responds with an empty body to
    ``DELETE /time_trackers/{id}.json``. The caller should treat the
    timer id as invalidated after this call.

    Raises ``KanbanToolHTTPError(404)`` if the timer id is unknown or
    belongs to another user."""
    # The API returns ``204 No Content`` (or sometimes empty 200) — there
    # is no body to decode. Just propagate any HTTP error and otherwise
    # return None.
    await _get_client().request("DELETE", f"time_trackers/{timer_id}")


@mcp.tool
async def list_my_timers() -> list[TimeTracker]:
    """List the authenticated user's time trackers across all tasks.

    Returns one ``TimeTracker`` per active or finished timer the current
    user owns; the wire data lives on the ``time_trackers`` field of the
    ``/users/current.json`` response (no dedicated list endpoint).

    Use ``Task.time_trackers`` instead when you only want one task's
    timers across all users."""
    data = await _get_client().request("GET", "users/current")
    raw = data.get("time_trackers", []) if isinstance(data, dict) else []
    return _decode_list(TimeTracker, raw, label="time trackers")


def run() -> None:
    mcp.run()
