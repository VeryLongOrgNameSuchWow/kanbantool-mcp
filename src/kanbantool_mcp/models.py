"""Pydantic models for Kanban Tool resources. Expanded in M1/M2."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
