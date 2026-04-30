"""Pydantic models for Kanban Tool resources. Expanded in M1/M2."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Column(BaseModel):
    """A workflow stage on a board (the API calls these ``workflow_stages``)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str
    position: int | None = None
    parent_id: int | None = None
    wip_limit: int | None = None
    # ``type`` shadows a Python builtin; accept the raw API key via alias.
    type_: str | None = Field(default=None, alias="type")


class Swimlane(BaseModel):
    """A horizontal lane on a board."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str
    position: int | None = None


class CustomField(BaseModel):
    """A custom field definition from the board's card template."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    label: str | None = None
    position: int | None = None
    options: str | None = None
    # ``type`` shadows a Python builtin; accept the raw API key via alias.
    type_: str | None = Field(default=None, alias="type")


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
