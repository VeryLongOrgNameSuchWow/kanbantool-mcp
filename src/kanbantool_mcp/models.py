"""Pydantic models for Kanban Tool resources. Expanded in M1/M2."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Board(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    description: str | None = None
    slug: str | None = None
    use_swimlanes: bool | None = None
    is_archived: bool | None = None
    user_role: str | None = None
