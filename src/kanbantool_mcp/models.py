"""Pydantic models for Kanban Tool resources. Expanded in M1/M2.

Abstraction layer — internal name vs. wire name. The Kanban Tool API exposes
several fields under names we deliberately rename for ergonomics on the MCP
surface; the mapping is centralised here so the next maintainer doesn't have
to grep for it:

- ``Board.columns`` ↔ API ``workflow_stages``
- ``Task.lane_id`` ↔ API ``workflow_stage_id``
- ``Column.type_`` ↔ API ``type`` (avoids shadowing the Python builtin
  internally; serialised back as ``type`` via alias).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class Column(BaseModel):
    """A workflow stage on a board (the API calls these ``workflow_stages``)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, serialize_by_alias=True)

    id: int
    # Live API returns ``null`` for the synthetic root stage that parents the
    # real columns (the one whose ``parent_id is None`` and ``lane_type is None``).
    # Keep the field nullable so the full tree validates; consumers filter
    # leaves via ``parent_id`` if they want only display columns.
    name: str | None = None
    position: int | None = None
    parent_id: int | None = None
    wip_limit: int | None = None
    # Semantic role string from the API ("backlog_inventory", "in_progress",
    # "completed", ...). The LLM uses this to distinguish columns by intent
    # rather than by display name.
    lane_type: str | None = None
    # ``type`` shadows a Python builtin internally; ``serialization_alias``
    # keeps the wire/MCP-schema name as ``type`` while the Python attribute
    # stays ``type_``.
    type_: str | None = Field(default=None, alias="type", serialization_alias="type")


class Swimlane(BaseModel):
    """A horizontal lane on a board."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str
    position: int | None = None


class Board(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str
    description: str | None = None
    slug: str | None = None
    use_swimlanes: bool | None = None
    is_archived: bool | None = None
    user_role: str | None = None
    # Detail-only collections; absent from list_boards' compact payload, hence defaults.
    columns: list[Column] = Field(default_factory=list, alias="workflow_stages")
    swimlanes: list[Swimlane] = Field(default_factory=list)
    # ``card_template`` is the API's per-board "which card fields are shown"
    # config — a dict keyed by field name (``description``, ``priority``,
    # ``custom_field_1``, ...) whose values describe each field's enabled
    # state, position, and (for ``custom_field_*``) label/type/options.
    # Exposed verbatim as a dict; a typed wrapper can come in v0.2.0 once
    # we have a use case beyond "show the LLM the config".
    card_template: dict[str, Any] | None = None
    # v0.2.0: embedded ``tasks: list[Task]`` (live-API spike showed the API
    # returns these on the detail endpoint — could remove a round-trip for
    # many flows). Pending design call.


class Task(BaseModel):
    """A task (card) on a board.

    Mirrors the GET ``/tasks/{id}.json`` response. Heavier nested resources
    (full subtasks, comments, time-tracker entries) are intentionally elided
    here — only their counts/totals are surfaced. Dedicated tools fetch the
    deep objects when an agent actually needs them.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str
    description: str | None = None
    board_id: int | None = None
    # ``workflow_stage_id`` is the on-the-wire name; expose the column id under
    # the terser ``lane_id`` alias that matches how callers reason about a
    # card's location (column/lane).
    lane_id: int | None = Field(default=None, alias="workflow_stage_id")
    swimlane_id: int | None = None
    position: int | None = None
    # ``priority`` is a small enum on most accounts but some installs return
    # raw integers; accept either rather than guessing.
    priority: int | str | None = None
    color: str | None = None
    # ISO 8601 strings as returned by the API; no native datetime parsing here.
    due_date: str | None = None
    start_date: str | None = None
    tags: str | None = None
    # The API surfaces a single user id, not a list. The richer Assignee
    # submodel (and any multi-user collaborators) can land alongside a tool
    # that actually needs it.
    assigned_user_id: int | None = None
    # Archival is timestamped on the wire; ``is_archived`` is a derived
    # convenience exposed via a property below.
    archived_at: str | None = None
    # ``block_reason`` is the only block-related field the API actually
    # returns — ``is_blocked`` is derived from "reason is set".
    block_reason: str | None = None
    subtasks_count: int | None = None
    comments_count: int | None = None
    # Flat seconds. The wrapped ``{ "total": ..., "by_user": ... }`` shape is a
    # separate concern — exposed via a dedicated time-tracker tool later.
    timers_total: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # v0.2.0: additive fields confirmed in the live-API spike — ``size_estimate``,
    # ``card_color``, ``search_tags``, ``collaborators``, ``card_type_id``,
    # ``custom_field_1..15``, ``recurring_schedule``, ``reminders_schedule``,
    # ``linked_tasks``, ``task_dependencies``. Tracked under the High-Value
    # tier of #38; the 15 numbered custom fields need a dict-shaped design call.

    # ``@computed_field`` is required so pydantic v2 includes the derived
    # boolean in ``model_dump()`` and ``model_json_schema()`` — a bare
    # ``@property`` is invisible to the serialiser, which means FastMCP would
    # never surface the flag to the LLM. The ``prop-decorator`` ignore quiets
    # the pydantic v2 ``@computed_field`` + ``@property`` decorator-order quirk.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_archived(self) -> bool:
        """True iff the task has an archival timestamp."""
        return self.archived_at is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_blocked(self) -> bool:
        """True iff the task has a non-empty block reason."""
        return self.block_reason is not None


class Comment(BaseModel):
    """A comment on a task. Mirrors the POST ``/tasks/{id}/comments.json``
    response. Field names follow the wire format directly — no aliasing —
    since the API's keys are already pleasant Python identifiers."""

    model_config = ConfigDict(extra="ignore")

    id: int
    text: str | None = None
    user_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class Subtask(BaseModel):
    """A subtask attached to a task.

    Mirrors the GET/POST ``/tasks/{id}/subtasks.json`` payload. ``name`` is
    the API-native field — kept verbatim rather than aliased to ``title`` so
    there's no rename to maintain on either edge.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    is_completed: bool | None = None
    completed_at: str | None = None
    position: int | None = None
    # M3: consider wrapping ValidationError as KanbanToolHTTPError("malformed subtask payload").


class ChangelogEntry(BaseModel):
    """A single entry from a board's changelog feed.

    Field names mirror the API verbatim (no aliasing) — the wire keys are
    already pleasant Python identifiers. Fields beyond ``id`` and
    ``created_at`` are optional: the API's exact shape varies by event type,
    and ``data`` is the catch-all for action-specific context (e.g. for
    ``what="created"`` it carries ``user_initials``, ``task_name``,
    ``workflow_stage_name``, ...)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    created_at: datetime
    # Action verb (``"created"``, ``"updated"``, ``"moved"``, ...).
    what: str | None = None
    user_id: int | None = None
    # Object the action targeted — ``"Task"``, ``"Board"``, etc.
    changed_object_type: str | None = None
    changed_object_id: int | None = None
    # Pre-rendered human-readable summary. Useful for LLM consumers that just
    # want a one-line "what happened" string without re-templating ``data``.
    description: str | None = None
    # Action-specific payload (e.g. ``user_initials``, ``task_name``,
    # ``workflow_stage_name``). Shape varies by ``what``.
    data: dict[str, Any] | None = None
