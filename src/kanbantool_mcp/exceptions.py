"""Exception hierarchy for the Kanban Tool client."""

from __future__ import annotations


class KanbanToolError(Exception):
    """Base class for all Kanban Tool client errors."""


class KanbanToolPermissionError(KanbanToolError):
    """Raised on 401/403 responses from the Kanban Tool API."""


class KanbanToolHTTPError(KanbanToolError):
    """Raised on non-2xx responses other than 401/403."""

    def __init__(self, message: str, *, status_code: int, body_excerpt: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body_excerpt = body_excerpt


class KanbanToolTransportError(KanbanToolError):
    """Raised when the underlying httpx transport fails after a retry."""
