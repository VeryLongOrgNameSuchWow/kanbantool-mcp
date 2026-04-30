"""Pydantic models for Kanban Tool resources. Expanded in M1/M2.

Abstraction layer — internal name vs. wire name. The Kanban Tool API exposes
several fields under names we deliberately rename for ergonomics on the MCP
surface; the mapping is centralised here so the next maintainer doesn't have
to grep for it:

- ``Board.columns`` ↔ API ``workflow_stages``
- ``Board.custom_fields`` ↔ API ``card_template``
- ``Task.lane_id`` ↔ API ``workflow_stage_id``
- ``Column.type_`` / ``CustomField.type_`` ↔ API ``type`` (avoids shadowing
  the Python builtin internally; serialised back as ``type`` via alias).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Column(BaseModel):
    """A workflow stage on a board (the API calls these ``workflow_stages``)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, serialize_by_alias=True)

    id: int
    name: str
    position: int | None = None
    parent_id: int | None = None
    wip_limit: int | None = None
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


class CustomField(BaseModel):
    """A custom field definition from the board's card template."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, serialize_by_alias=True)

    label: str | None = None
    position: int | None = None
    # Kanban Tool returns a bare string for simple fields and a structured
    # list for select-style fields; accept either rather than dropping the
    # field on a type mismatch.
    options: list[str] | str | None = None
    # See ``Column.type_`` for the alias rationale.
    type_: str | None = Field(default=None, alias="type", serialization_alias="type")


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
    custom_fields: list[CustomField] = Field(default_factory=list, alias="card_template")


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
    # User ids only. The full Assignee submodel can land alongside a tool that
    # actually needs it.
    assignees: list[int] | None = None
    is_archived: bool | None = None
    is_blocked: bool | None = None
    block_reason: str | None = None
    subtasks_count: int | None = None
    comment_count: int | None = None
    # Flat seconds. The wrapped ``{ "total": ..., "by_user": ... }`` shape is a
    # separate concern — exposed via a dedicated time-tracker tool later.
    time_tracker_total: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


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

    Fields beyond ``id`` and ``created_at`` are optional — the API's exact
    shape varies by event type, and we keep this model permissive so the
    poller never blows up on an unfamiliar action."""

    model_config = ConfigDict(extra="ignore")

    id: int
    created_at: datetime
    action: str | None = None
    actor_id: int | None = None
    actor_name: str | None = None
    target_type: str | None = None
    target_id: int | None = None
    details: dict[str, Any] | None = None
