"""FastMCP server definition for kanbantool-mcp."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from fastmcp import FastMCP
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, validate_call

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

# Server-level mental model surfaced to MCP clients. FastMCP forwards this to
# the LLM as the always-loaded "how to think about this server" preamble, so
# every tool call benefits from it without paying a per-tool docstring tax.
#
# Kept short (~135 words, hard cap ~150) on purpose: the LLM may compress or
# silently truncate longer instructions, so we earn budget by saying only
# what isn't already obvious from individual tool docstrings — discovery
# order, the "me/whoami" handshake, the polling stand-in for webhooks, the
# one-and-only ``set_custom_field`` exception to the omit-vs-clear rule, and
# the typed exception ladder. Any longer-form quirks (Rails envelope vs
# flat-body wire shapes, soft-delete semantics, the search DSL) live in
# ``llms.txt`` and the per-tool docstrings.
#
# CONTRIBUTING: when you add or remove a tool, or change its semantics in a
# way that contradicts this preamble, update both this string AND
# ``llms.txt``. The two surfaces must stay in sync.
_SERVER_INSTRUCTIONS = """\
Kanban Tool MCP server. Boards hold columns, lanes, tasks, comments, \
subtasks, time trackers, and 15 custom-field slots per task.

Discovery: start with `list_boards` to find board IDs. To resolve \
"me"/"myself", call `whoami` (returns the authenticated user). To match a \
name like "Alice", call `list_board_collaborators(board_id)` then \
`get_user(id)` to confirm. Use `list_custom_field_definitions(board_id)` \
to learn what each `custom_field_N` slot means on that board.

Change tracking: Kanban Tool has no webhooks. Poll \
`recent_changes(board_id, since=...)` sparingly (30-120s cadence). Always \
pass `since` after the first call.

`None` means "omit, don't clear" on `update_task` / `move_task` / \
`update_subtask` — the API ignores nulls. The one exception: \
`set_custom_field(value=None)` sends a literal null and CLEARS the slot.

Destructive tools (`delete_*`, `archive_task`) carry \
`destructiveHint: True`. Errors flow through typed `KanbanToolError` \
subclasses (`KanbanToolPermissionError` for 401/403, \
`KanbanToolValidationError` for 422 with `field_errors`, \
`KanbanToolHTTPError` otherwise, `KanbanToolTransportError` on transport \
failure)."""

mcp: FastMCP = FastMCP("kanbantool-mcp", instructions=_SERVER_INSTRUCTIONS)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


# --- Output schema helpers --------------------------------------------------
#
# Each ``@mcp.tool`` below pins ``output_schema=`` explicitly to the tool's
# return type so the wire-side contract is locked at the decorator (rather
# than re-derived by FastMCP from the type hint on every server start).
# Stable schemas matter because clients can cache them, the JSON Schema is
# part of the user-visible tool surface, and any future drift in FastMCP's
# auto-derivation would silently change what callers see.
#
# Pydantic ``mode="serialization"`` is required for ``@computed_field`` props
# (``Task.is_archived``, ``Task.is_blocked``, ``TimeTracker.is_running``) to
# appear in the generated schema — the default validation-mode schema omits
# them and the LLM never learns the flags exist.
#
# For non-object return types (``list[T]``) MCP requires the top-level
# ``output_schema`` be an object, so we wrap inside a ``{"result": [...]}``
# envelope marked with ``x-fastmcp-wrap-result: True``. FastMCP recognises
# that marker at runtime and unwraps the value before serialising the
# tool's structured content — so the *transport* shape stays consistent
# with what FastMCP would have generated automatically.

_T = TypeVar("_T")


@dataclass
class _WrappedResult(Generic[_T]):
    """Sentinel envelope mirroring FastMCP's internal ``_WrappedResult``.

    Generated only via ``TypeAdapter`` to produce the wrapped-output JSON
    schema for ``list[T]``-returning tools. Never instantiated at runtime —
    FastMCP's tool dispatcher does the actual wrapping using the same
    marker key (``x-fastmcp-wrap-result``).
    """

    result: _T


def _output_schema(return_type: Any) -> dict[str, Any]:
    """Build the JSON-schema dict to pass as ``output_schema=`` on a tool.

    Always uses pydantic's serialization-mode schema so ``@computed_field``s
    surface. Wraps non-object types in a ``{"result": ...}`` envelope tagged
    with ``x-fastmcp-wrap-result: True`` so FastMCP's runtime unwraps it
    transparently — same shape as FastMCP's own auto-derivation.
    """
    base = TypeAdapter(return_type).json_schema(mode="serialization")
    if base.get("type") == "object" or "properties" in base:
        return base
    wrapped = TypeAdapter(_WrappedResult[return_type]).json_schema(mode="serialization")
    wrapped["x-fastmcp-wrap-result"] = True
    return wrapped


# --- Annotation hint constants ----------------------------------------------
#
# MCP's ``ToolAnnotations`` lets a server declare invariants the LLM can use
# to plan safely (a read tool needs no confirmation; a destructive write
# does). Each tool below picks one of these constants on its decorator.
# Constants — not inline dicts — so the classification is grep-able and a
# future audit can re-verify by looking at the call site alone.

# Read tools — no side effects, safe to call freely. Per the MCP spec,
# ``destructiveHint`` and ``idempotentHint`` are meaningless when
# ``readOnlyHint=True``, so leave them unset rather than asserting defaults.
_READ_ONLY: dict[str, Any] = {"readOnlyHint": True}

# Mutating writes that change real state but don't delete records, and
# whose repeat invocation would do something different (e.g. creating a
# second comment, adding a second subtask).
_WRITE: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
}

# Mutating writes whose repeat invocation lands on the same end state
# (re-archiving an archived task, re-reordering with the same id list).
_WRITE_IDEMPOTENT: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
}

# Hard-deletes / archives — call out as destructive so the LLM treats the
# action as confirmation-worthy. ``archive_task`` is also idempotent
# (overlay below).
_WRITE_DESTRUCTIVE: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
}

# Archive: destructive AND idempotent (re-archiving an archived task is
# a harmless no-op server-side).
_WRITE_DESTRUCTIVE_IDEMPOTENT: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
}


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


@mcp.tool(annotations=_READ_ONLY)
def ping() -> str:
    """Smoke-test the MCP transport. Returns the literal string ``pong``."""
    return "pong"


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(list[Board]))
async def list_boards() -> list[Board]:
    """List boards visible to the authenticated user. Use this to discover
    ``board_id`` values for the other tools."""
    data = await _get_client().request("GET", "users/current")
    raw = data.get("boards", []) if isinstance(data, dict) else []
    return _decode_list(Board, raw, label="boards")


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(User))
async def whoami() -> User:
    """Fetch the authenticated user's profile.

    Returns the ``User`` you're acting as — id, name, role flags, locale,
    timezone. Use this to resolve "me" / "myself" references in user
    requests (``assign to me`` → ``assigned_user_id`` from this response)
    or to show the LLM whose perspective it's operating from."""
    data = await _get_client().request("GET", "users/current")
    return _decode(User, data, label="user")


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(User))
@validate_call
async def get_user(user_id: Annotated[int, Field(ge=1)]) -> User:
    """Fetch one user by id. Useful after ``list_board_collaborators`` finds
    a candidate by name — call this to confirm role flags and active state
    before assigning. Raises ``KanbanToolHTTPError(404)`` for unknown ids."""
    data = await _get_client().request("GET", f"users/{user_id}")
    return _decode(User, data, label="user")


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(list[Collaborator]))
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


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(list[CustomFieldDefinition]))
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


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(Board))
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


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(Task))
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


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(list[ChangelogEntry]))
@validate_call
async def recent_changes(
    board_id: Annotated[int, Field(ge=1)], since: datetime | None
) -> list[ChangelogEntry]:
    """Fetch a board's changelog (Kanban Tool has no webhooks; poll this instead).

    ``since`` is **required**. Pass the ``created_at`` of the newest entry you've
    already seen; on the first poll, use ``datetime.now(UTC) - timedelta(hours=1)``
    (or whichever lookback window matches your use case). Entries come
    newest-first. Poll sparingly: 30-120s cadence, not per-keystroke.

    Why required: omitting ``since`` would fetch the entire board history,
    which is cheap server-side but pulls hundreds of irrelevant entries into
    the LLM's context. Forcing the caller to think about the time window
    keeps responses bounded.

    Raises ``ValueError`` if ``since`` is ``None``."""
    if since is None:
        # Reject ``None`` at the tool boundary so callers see an actionable
        # message instead of a 200 OK with an unbounded result set. Hits
        # both the explicit ``recent_changes(board_id, since=None)`` call
        # and the wire shape where an MCP client sends ``"since": null``.
        raise ValueError(
            "recent_changes requires 'since'. Pass a datetime or ISO-8601 string "
            "of the most recent entry you've seen; for first-poll, use "
            "(datetime.now(UTC) - timedelta(hours=1))."
        )
    params = {"since": since.isoformat()}
    data = await _get_client().request("GET", f"boards/{board_id}/changelog", params=params)
    return _decode_list(ChangelogEntry, data, label="changelog")


# Hard ceiling on a single page. Defends against an LLM hallucinating a huge
# limit and pulling far more than the API or the agent's context can sanely
# absorb in one round-trip. Pagination via ``page`` is the supported way to
# walk past it.
_SEARCH_TASKS_MAX_LIMIT = 50


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(list[Task]))
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


@mcp.tool(annotations=_WRITE, output_schema=_output_schema(Task))
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
    Unset kwargs are omitted from the request, never sent as explicit null.

    Common 422s (``KanbanToolValidationError`` with parsed ``field_errors``):
    ``name`` empty/missing → fix by passing a non-empty string;
    ``lane_id`` belongs to a different board → resolve column ids on the
    target board first via ``get_board(board_id).columns``;
    ``assigned_user_id`` not a board collaborator → check via
    ``list_board_collaborators(board_id)``."""
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


@mcp.tool(annotations=_WRITE, output_schema=_output_schema(Task))
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

    Raises ``ValueError`` if every field is ``None`` (no-op guard).

    Common 422s (``KanbanToolValidationError`` with parsed ``field_errors``):
    ``lane_id`` from a different board → use ``get_board(board_id).columns``
    on the destination board first; ``assigned_user_id`` not a board
    collaborator → ``list_board_collaborators(board_id)`` to confirm."""
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


@mcp.tool(annotations=_WRITE, output_schema=_output_schema(Task))
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
    the ``Task.lane_id`` on fetched tasks. Moves are scoped to the task's
    current board — there is no cross-board move surface.

    Common 422s (``KanbanToolValidationError`` with parsed ``field_errors``):
    ``column_id`` doesn't belong to the task's board → fetch valid column
    ids via ``get_board(get_task(task_id).board_id).columns`` and pick from
    those; ``swimlane_id`` doesn't exist on the task's board → same fix via
    ``.swimlanes`` on the same Board."""
    return await _patch_task(
        task_id,
        {
            "column_id": column_id,
            "swimlane_id": swimlane_id,
            "position": position,
        },
        method="PATCH",
    )


@mcp.tool(annotations=_WRITE_DESTRUCTIVE_IDEMPOTENT, output_schema=_output_schema(Task))
@validate_call
async def archive_task(task_id: Annotated[int, Field(ge=1)]) -> Task:
    """Archive a task. Returns the updated ``Task`` (caller can confirm
    ``is_archived=True``).

    Idempotent: re-archiving an already-archived task succeeds. There is no
    ``unarchive_task`` yet — archiving is currently one-way from this surface.

    Failure modes: ``KanbanToolHTTPError(404)`` when the task id is unknown
    (verify via ``get_task(task_id)`` first if you're unsure);
    ``KanbanToolPermissionError(403)`` when the authenticated user lacks
    write access to the task's board."""
    # Sentinel-action family on PATCH /tasks/{id}.json: "archive",
    # "unarchive", "delete", "undelete". If we ever add unarchive_task /
    # delete_task / restore_task, copy this 2-line shape — don't generalize
    # into a helper until there's a second caller (YAGNI).
    body = {"_action": "archive"}
    data = await _get_client().request("PATCH", f"tasks/{task_id}", json=body)
    return _decode(Task, data, label="task")


@mcp.tool(annotations=_WRITE_IDEMPOTENT, output_schema=_output_schema(Task))
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

    Common 422 (``KanbanToolValidationError`` with parsed ``field_errors``):
    type mismatch — e.g. writing ``"hi"`` into a numeric slot, or a value
    not in ``options`` for a dropdown-typed slot. Inspect the slot's
    ``type``/``options`` via ``list_custom_field_definitions(board_id)``
    before writing if the slot purpose is uncertain."""
    # NOTE: Deliberately does NOT route through ``_patch_task``. That helper
    # has None-skip semantics ("omit, don't clear"); here ``None`` MUST land
    # on the wire as a literal ``null`` to clear the field. Build the body
    # inline.
    body = {"task": {f"custom_field_{slot}": value}}
    data = await _get_client().request("PUT", f"tasks/{task_id}", json=body)
    return _decode(Task, data, label="task")


@mcp.tool(annotations=_WRITE, output_schema=_output_schema(Comment))
@validate_call
async def add_comment(task_id: Annotated[int, Field(ge=1)], content: str) -> Comment:
    """Post a comment on a task. Returns the created ``Comment`` with id,
    content, author, and timestamps.

    Common 422 (``KanbanToolValidationError`` with parsed ``field_errors``):
    ``content`` empty or whitespace-only — fix by passing a non-empty
    string; the ``field_errors`` key matches the parameter name."""
    body = {"comment": {"content": content}}
    data = await _get_client().request("POST", f"tasks/{task_id}/comments", json=body)
    return _decode(Comment, data, label="comment")


@mcp.tool(annotations=_WRITE_DESTRUCTIVE, output_schema=_output_schema(Comment))
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

    Failure modes: ``KanbanToolHTTPError(404)`` when either id is unknown
    or the comment isn't on that task — common cause is reusing a stale
    ``comment_id`` from a previous list. Re-fetch the task's current
    comments before retrying."""
    data = await _get_client().request("DELETE", f"tasks/{task_id}/comments/{comment_id}")
    return _decode(Comment, data, label="comment")


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(list[Subtask]))
@validate_call
async def list_subtasks(task_id: Annotated[int, Field(ge=1)]) -> list[Subtask]:
    """List subtasks on a task — id, name, completion state, position.

    Subtasks are returned inline on ``Task.subtasks`` whenever you fetch a
    task; this tool is sugar for callers that only want the list. Costs one
    HTTP call (the same as ``get_task``) — the Kanban Tool API has no
    dedicated list-subtasks endpoint."""
    task = await get_task(task_id)
    return task.subtasks


@mcp.tool(annotations=_WRITE, output_schema=_output_schema(Subtask))
@validate_call
async def add_subtask(task_id: Annotated[int, Field(ge=1)], name: str) -> Subtask:
    """Add a subtask to a task. Returns the created ``Subtask``.

    ``name`` is the human-readable label. Mirrors the parameter name used
    by ``update_subtask`` and the wire/model field on ``Subtask.name``.

    Common 422 (``KanbanToolValidationError`` with parsed ``field_errors``):
    ``name`` empty or whitespace-only — fix by passing a non-empty string."""
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


@mcp.tool(annotations=_WRITE, output_schema=_output_schema(Subtask))
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
    ``reorder_subtasks`` to change ordering.

    Common 422 (``KanbanToolValidationError`` with parsed ``field_errors``):
    ``assigned_user_id`` not a collaborator on the parent task's board —
    use ``list_board_collaborators(board_id)`` to confirm before retrying."""
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


@mcp.tool(annotations=_WRITE_DESTRUCTIVE, output_schema=_output_schema(Subtask))
@validate_call
async def delete_subtask(subtask_id: Annotated[int, Field(ge=1)]) -> Subtask:
    """Delete a subtask (soft-delete). Returns the deleted ``Subtask`` with
    ``deleted_at`` populated.

    The Kanban Tool API soft-deletes — the subtask record is retained
    server-side with a ``deleted_at`` timestamp and stops appearing on the
    parent task's ``subtasks`` array. This operation is not strictly
    irreversible from an audit perspective, but the MCP-visible effect is
    "the subtask is gone."

    Failure modes: ``KanbanToolHTTPError(404)`` when the subtask id is
    unknown OR was already soft-deleted in a previous call (the API
    returns 404 in both cases). Re-fetch the parent task's
    ``subtasks`` list to confirm the current state before retrying."""
    data = await _get_client().request("DELETE", f"subtasks/{subtask_id}")
    return _decode(Subtask, data, label="subtask")


@mcp.tool(annotations=_WRITE_IDEMPOTENT, output_schema=_output_schema(list[Subtask]))
@validate_call
async def reorder_subtasks(
    task_id: Annotated[int, Field(ge=1)],
    ids: list[Annotated[int, Field(ge=1)]],
) -> list[Subtask]:
    """Reorder subtasks under a task. Returns the subtasks in the new order.

    ``ids`` must be the full set of subtask ids on ``task_id`` in the
    desired order.

    Common 422s (``KanbanToolValidationError`` with parsed ``field_errors``):
    ``ids`` is a partial set (missing some of the task's current subtask
    ids) → list current ids via ``list_subtasks(task_id)`` and pass them
    all in the new order; one or more ids belong to a different task →
    same fix, since the API rejects cross-task references."""
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


@mcp.tool(annotations=_WRITE, output_schema=_output_schema(TimeTracker))
@validate_call
async def start_timer(
    task_id: Annotated[int, Field(ge=1)],
    board_id: Annotated[int, Field(ge=1)] | None = None,
) -> TimeTracker:
    """Start a new time tracker on a task for the authenticated user.

    Returns the created ``TimeTracker``. The new timer starts in the
    running state (``ended_at`` is ``None``); call ``stop_timer`` when
    work pauses or ends.

    ``board_id`` is required by the Kanban Tool API but may be omitted
    here — when not supplied the tool resolves it via an internal
    ``get_task(task_id)`` call (one extra HTTP round-trip). Pass it
    explicitly when you already have it (e.g. you just listed tasks for a
    board) to avoid the second request. Either way, the resulting wire
    body sends both ids.

    Note: timers are per-user — starting one creates a record for the
    authenticated user only. Use ``whoami`` if you need to know whose
    timer it is.

    Common 422: the API rejects starting a timer on a task whose board
    you don't have access to with a typed ``KanbanToolValidationError``.
    Verify the task is on a board you can list via ``list_boards``."""
    if board_id is None:
        # Same shape as ``list_subtasks`` / ``list_board_collaborators``
        # — read-side sugar that costs one extra request to keep the
        # tool surface ergonomic. The resolved id is then sent to the
        # API the same way an explicit ``board_id`` argument would be.
        task = await get_task(task_id)
        if task.board_id is None:
            # Defensive: a Task with no board_id can't start a timer
            # because the API requires one. ``Task.board_id`` is
            # ``Optional`` in the model to match the wire shape (compact
            # search results may omit it), but ``get_task`` returns the
            # detail payload, so a missing board_id here means the API
            # itself failed to populate the field — surface that clearly
            # rather than send a degenerate request.
            raise ValueError(
                f"start_timer could not resolve board_id for task_id={task_id}: "
                "the task detail response had no board_id. Pass board_id explicitly."
            )
        board_id = task.board_id
    body = {"board_id": board_id, "task_id": task_id}
    data = await _get_client().request("POST", "time_trackers", json=body)
    return _decode(TimeTracker, data, label="time tracker")


@mcp.tool(annotations=_WRITE, output_schema=_output_schema(TimeTracker))
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


@mcp.tool(annotations=_WRITE_DESTRUCTIVE)
@validate_call
async def delete_timer(timer_id: Annotated[int, Field(ge=1)]) -> None:
    """Delete a time tracker entirely (e.g. cancel a mistakenly-started
    one). Unlike subtasks, timer deletion is hard — the record is gone,
    not soft-flagged.

    Returns ``None`` because the API responds with an empty body to
    ``DELETE /time_trackers/{id}.json``. The caller should treat the
    timer id as invalidated after this call.

    Failure modes: ``KanbanToolHTTPError(404)`` when the timer id is
    unknown, was already deleted, or belongs to another user (the API
    scopes timer ids to the authenticated user). Use ``list_my_timers()``
    to confirm the current set of timer ids you own before retrying."""
    # No explicit ``output_schema=`` on this decorator: FastMCP's auto
    # derivation from the ``-> None`` return type produces the correct
    # ``{"result": null}`` wrapped envelope already, and there's no
    # ``models.py`` type to pin it to. The API returns 204 No Content
    # (or sometimes empty 200) — there is no body to decode. Just
    # propagate any HTTP error and otherwise return None.
    await _get_client().request("DELETE", f"time_trackers/{timer_id}")


@mcp.tool(annotations=_READ_ONLY, output_schema=_output_schema(list[TimeTracker]))
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
