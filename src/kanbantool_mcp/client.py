"""HTTP client for the Kanban Tool API v3."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from .config import Config
from .exceptions import (
    KanbanToolHTTPError,
    KanbanToolPermissionError,
    KanbanToolTransportError,
)

_BODY_EXCERPT_LIMIT = 200
_RETRY_BACKOFF_SECONDS = 0.5

_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+\S+")


def _scrub_secrets(text: str) -> str:
    """Replace any 'Bearer <token>' sequence with 'Bearer ***' before storing
    or surfacing user-visible response excerpts. Single-pattern scrub —
    Kanban Tool API uses bearer auth exclusively, so this covers the realistic
    leak path (upstream proxy/WAF echoing the Authorization header)."""
    return _BEARER_PATTERN.sub("Bearer ***", text)


class KanbanToolClient:
    def __init__(self, config: Config, http: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._http = (
            http
            if http is not None
            else httpx.AsyncClient(
                base_url=config.base_url,
                headers={"Authorization": f"Bearer {config.api_token}"},
                timeout=30.0,
            )
        )

    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Send an HTTP request to the Kanban Tool API and return the parsed JSON response.

        Pass query parameters via the `params=` keyword, not embedded in `path` —
        the `.json` suffix logic doesn't account for query strings. Authorization
        is set client-wide; do not override it via `headers=`."""
        normalized = path.lstrip("/")
        if not normalized.endswith(".json"):
            normalized = f"{normalized}.json"

        try:
            response = await self._http.request(method, normalized, **kwargs)
        except httpx.TransportError:
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
            try:
                response = await self._http.request(method, normalized, **kwargs)
            except httpx.TransportError as second_error:
                raise KanbanToolTransportError(
                    f"Transport error contacting Kanban Tool API: {second_error}"
                ) from second_error

        status = response.status_code
        if status == 401:
            raise KanbanToolPermissionError(
                "Kanban Tool API rejected the request as unauthorized (401). "
                "Check that the KANBANTOOL_API_TOKEN env var is set to a valid token."
            )
        if status == 403:
            raise KanbanToolPermissionError(
                "Kanban Tool API denied the request as forbidden (403). "
                "The token does not have permission for this resource."
            )
        if status >= 400:
            body_excerpt = _scrub_secrets(response.text)[:_BODY_EXCERPT_LIMIT]
            raise KanbanToolHTTPError(
                f"Kanban Tool API returned HTTP {status} for {method} {normalized}",
                status_code=status,
                body_excerpt=body_excerpt,
            )

        try:
            return response.json()
        except json.JSONDecodeError:
            raise KanbanToolHTTPError(
                f"Kanban Tool API returned non-JSON body for {method} {normalized}",
                status_code=status,
                body_excerpt=_scrub_secrets(response.text)[:_BODY_EXCERPT_LIMIT],
            ) from None
