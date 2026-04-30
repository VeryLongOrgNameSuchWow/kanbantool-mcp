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
