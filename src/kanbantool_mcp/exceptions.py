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


class KanbanToolValidationError(KanbanToolHTTPError):
    """Raised on a 422 Unprocessable Entity response.

    Subclasses ``KanbanToolHTTPError`` so existing ``except KanbanToolHTTPError``
    callers continue to catch validation failures, while callers that want
    field-level detail can branch on this subclass and read ``field_errors``.
    """

    def __init__(
        self,
        message: str = "Kanban Tool API rejected the request as invalid (422).",
        *,
        status_code: int = 422,
        body_excerpt: str,
        field_errors: dict[str, list[str]],
    ) -> None:
        super().__init__(message, status_code=status_code, body_excerpt=body_excerpt)
        self.field_errors = field_errors

    def __str__(self) -> str:
        base = super().__str__()
        if not self.field_errors:
            return base
        rendered = "; ".join(
            f"{field}: {', '.join(messages)}" for field, messages in self.field_errors.items()
        )
        return f"{base} {rendered}"


class KanbanToolTransportError(KanbanToolError):
    """Raised when the underlying httpx transport fails after a retry."""
