"""HTTP client for the Kanban Tool API v3."""

from __future__ import annotations

import asyncio
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
        normalized = path.lstrip("/")
        if not normalized.endswith(".json"):
            normalized = f"{normalized}.json"

        try:
            response = await self._http.request(method, normalized, **kwargs)
        except httpx.TransportError as first_error:
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
            try:
                response = await self._http.request(method, normalized, **kwargs)
            except httpx.TransportError as second_error:
                raise KanbanToolTransportError(
                    f"Transport error contacting Kanban Tool API: {second_error}"
                ) from second_error
            del first_error

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
            body_excerpt = response.text[:_BODY_EXCERPT_LIMIT]
            raise KanbanToolHTTPError(
                f"Kanban Tool API returned HTTP {status} for {method} {normalized}",
                status_code=status,
                body_excerpt=body_excerpt,
            )

        return response.json()
