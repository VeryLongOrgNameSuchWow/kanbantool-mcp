"""Exception hierarchy for the Kanban Tool client."""

from __future__ import annotations

import re

_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+\S+")


class KanbanToolError(Exception):
    """Base class for all Kanban Tool client errors."""


class KanbanToolPermissionError(KanbanToolError):
    """Raised on 401/403 responses from the Kanban Tool API."""


class KanbanToolHTTPError(KanbanToolError):
    """Raised on non-2xx responses other than 401/403."""

    def __init__(self, message: str, *, status_code: int, body_excerpt: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        # Centralised secret scrub. Callers in ``client.py`` already scrub on
        # the way in for 4xx/5xx; the new ``server._decode`` / ``_decode_list``
        # helpers fold a ``ValidationError.__repr__`` (which embeds the input
        # value) into the excerpt without going through ``client._scrub_secrets``.
        # Doing the scrub here closes that gap and makes the exception
        # constructor authoritative — every code path that builds a
        # ``KanbanToolHTTPError`` ends up with a scrubbed excerpt regardless
        # of whether the caller remembered.
        self.body_excerpt = _BEARER_PATTERN.sub("Bearer ***", body_excerpt)


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
        if self.field_errors:
            rendered = "; ".join(
                f"{field}: {', '.join(messages)}" for field, messages in self.field_errors.items()
            )
            # Belt-and-suspenders: scrub again here so direct construction of
            # the exception with un-scrubbed input cannot leak a bearer token
            # through the rendered string. The client construction path scrubs
            # on the way in; this is a second line of defense for callers that
            # bypass it.
            return _BEARER_PATTERN.sub("Bearer ***", f"{base} {rendered}")
        # Fall back to the (already-scrubbed) excerpt when the API returned a
        # 422 body whose shape ``_parse_field_errors`` doesn't recognise. The
        # excerpt is the LLM's only signal in that case — surfacing just the
        # bare "rejected as invalid (422)" message strands the caller with no
        # information about what went wrong.
        if self.body_excerpt:
            return _BEARER_PATTERN.sub("Bearer ***", f"{base} body: {self.body_excerpt}")
        return base


class KanbanToolTransportError(KanbanToolError):
    """Raised when the underlying httpx transport fails after a retry."""
