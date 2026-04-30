"""Pydantic models for Kanban Tool resources. Expanded in M1/M2."""

from __future__ import annotations

from pydantic import BaseModel


class Board(BaseModel):
    id: int
    name: str
