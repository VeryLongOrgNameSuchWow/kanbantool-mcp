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
    KanbanToolValidationError,
)

_BODY_EXCERPT_LIMIT = 200
_RETRY_BACKOFF_SECONDS = 0.5

# Retry policy for transient HTTP responses on idempotent reads. GET-only on
# purpose — POST/PUT/PATCH/DELETE are NOT retried even on 429/5xx, because the
# Kanban Tool API has no idempotency-key support, so a transient 503 from a
# write that *did* land server-side would, on retry, double-create the
# resource. At-most-once write semantics are the right default; the agent
# (LLM) can decide whether to re-issue a failed write itself.
_RETRY_5XX_DELAY_SECONDS = 0.5
_RETRY_429_DEFAULT_DELAY_SECONDS = 1.0
# ``Retry-After`` is honored on 429, but capped — a 60s server response would
# block the LLM for a minute, which is a worse experience than surfacing the
# typed error and letting the agent decide. Anything longer than this surfaces
# as ``KanbanToolHTTPError(429)`` immediately.
_RETRY_AFTER_CAP_SECONDS = 5.0
_RETRYABLE_STATUS_CODES = frozenset({429, *range(500, 600)})

_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+\S+")


def _retry_delay_for(response: httpx.Response) -> float | None:
    """Return the delay (seconds) before retrying ``response``, or ``None`` if
    it should NOT be retried.

    Caller is responsible for the GET-only / one-retry-per-class policy; this
    helper only inspects the response itself. Returns ``None`` for any 429
    where ``Retry-After`` exceeds the cap, so the caller surfaces the typed
    error rather than blocking the LLM for a long server-suggested wait."""
    status = response.status_code
    if status not in _RETRYABLE_STATUS_CODES:
        return None
    if status == 429:
        retry_after_raw = response.headers.get("Retry-After")
        if retry_after_raw is None:
            return _RETRY_429_DEFAULT_DELAY_SECONDS
        try:
            requested = float(retry_after_raw)
        except ValueError:
            # ``Retry-After`` may legally be an HTTP-date; we don't parse those
            # (Kanban Tool's API uses delta-seconds). Fall back to the default
            # rather than waiting forever.
            return _RETRY_429_DEFAULT_DELAY_SECONDS
        if requested > _RETRY_AFTER_CAP_SECONDS:
            return None
        return max(0.0, requested)
    # 5xx
    return _RETRY_5XX_DELAY_SECONDS


def _scrub_secrets(text: str) -> str:
    """Replace any 'Bearer <token>' sequence with 'Bearer ***' before storing
    or surfacing user-visible response excerpts. Single-pattern scrub —
    Kanban Tool API uses bearer auth exclusively, so this covers the realistic
    leak path (upstream proxy/WAF echoing the Authorization header)."""
    return _BEARER_PATTERN.sub("Bearer ***", text)


def _parse_field_errors(body: str) -> dict[str, list[str]]:
    """Parse a 422 body's error envelope. The Kanban Tool API uses two shapes
    interchangeably depending on the endpoint and the failure mode:

    1. Rails-idiomatic, per-field: ``{"errors": {field: [msg, ...]}}`` —
       e.g. ``add_comment`` returns ``{"errors": {"content": ["can't be blank"]}}``.
    2. Flat ``{"status": 422, "message": [msg, ...]}`` — e.g. ``create_task``
       with a malformed enum like ``priority="high"`` returns
       ``{"status": 422, "message": ["Priority is not a number"]}``. The flat
       shape carries no field attribution; surfaced under a synthetic
       ``"message"`` key so callers can still render it.

    Returns an empty dict if the body is not JSON, neither envelope is
    present, or the values are not the expected list-of-strings shape — the
    caller still surfaces the raw (scrubbed) excerpt in that case.

    Both message strings and field keys are run through ``_scrub_secrets`` so
    a token leaked inside the JSON envelope (e.g. an upstream proxy echoing
    ``Authorization: Bearer X`` into a field message) cannot survive into
    ``KanbanToolValidationError.field_errors`` or its ``__str__`` output."""
    try:
        decoded = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    errors = decoded.get("errors")
    if isinstance(errors, dict):
        parsed: dict[str, list[str]] = {}
        for field, messages in errors.items():
            if not isinstance(field, str):
                continue
            scrubbed_field = _scrub_secrets(field)
            if isinstance(messages, list):
                parsed[scrubbed_field] = [_scrub_secrets(str(m)) for m in messages]
            else:
                parsed[scrubbed_field] = [_scrub_secrets(str(messages))]
        if parsed:
            return parsed
    # Flat ``{"status": 422, "message": [...]}`` shape — coerce ``message`` into
    # a single-key dict so the renderer gives the LLM something concrete to
    # read, even without per-field attribution.
    flat_messages = decoded.get("message")
    if isinstance(flat_messages, list) and flat_messages:
        return {"message": [_scrub_secrets(str(m)) for m in flat_messages]}
    if isinstance(flat_messages, str) and flat_messages:
        return {"message": [_scrub_secrets(flat_messages)]}
    return {}


def _raise_for_status(response: httpx.Response, method: str, path: str) -> None:
    """Translate a non-2xx ``httpx.Response`` into the appropriate Kanban Tool
    exception. Returns silently for 2xx so the caller can proceed to JSON
    decoding. Splitting this out keeps ``request()`` focused on transport
    concerns while error-shape policy lives in one place."""
    status = response.status_code
    if status < 400:
        return
    if status == 401:
        raise KanbanToolPermissionError(
            "Kanban Tool API rejected the request as unauthorized (401). "
            "Check that the KANBANTOOL_API_TOKEN env var is set to a valid, "
            "non-expired token, and that KANBANTOOL_DOMAIN matches the account "
            "that issued the token."
        )
    if status == 403:
        raise KanbanToolPermissionError(
            "Kanban Tool API denied the request as forbidden (403). "
            "The token is valid but lacks permission for this resource. "
            "Try list_boards or whoami to confirm what this token can see, "
            "or ask an admin to grant access."
        )
    body_text = response.text
    body_excerpt = _scrub_secrets(body_text)[:_BODY_EXCERPT_LIMIT]
    if status == 422:
        raise KanbanToolValidationError(
            f"Kanban Tool API rejected {method} {path} as invalid (422).",
            status_code=422,
            body_excerpt=body_excerpt,
            field_errors=_parse_field_errors(body_text),
        )
    generic_message = f"Kanban Tool API returned HTTP {status} for {method} {path}"
    if status == 404:
        message = (
            f"no such task/board (or you lack access). {generic_message}. "
            "Try list_boards to discover ids you can see, or check the id "
            "argument against a recent get_task / get_board response."
        )
    elif status >= 500:
        message = f"Kanban Tool API is having issues; retry shortly. {generic_message}"
    else:
        message = generic_message
    raise KanbanToolHTTPError(
        message,
        status_code=status,
        body_excerpt=body_excerpt,
    )


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
                follow_redirects=True,
            )
        )

    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send an HTTP request to the Kanban Tool API and return the parsed JSON response.

        Return type is ``Any`` because the Kanban Tool API surfaces both
        envelope-shaped dicts (``GET /tasks/search.json`` → ``{"results": [...]}``)
        and bare lists (``GET /boards/{id}/changelog.json`` → ``[...]``). Callers
        narrow via ``model_validate`` on the items they actually expect.

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

        # GET-only retry on transient HTTP responses (429 + 5xx). Writes
        # (POST/PUT/PATCH/DELETE) are intentionally NOT retried — see the
        # _RETRYABLE_STATUS_CODES rationale at module scope. One retry per
        # error class: if the second attempt also returns 429/5xx (or any
        # other error), it propagates through ``_raise_for_status`` below.
        if method.upper() == "GET":
            retry_delay = _retry_delay_for(response)
            if retry_delay is not None:
                await asyncio.sleep(retry_delay)
                try:
                    response = await self._http.request(method, normalized, **kwargs)
                except httpx.TransportError as transport_error:
                    raise KanbanToolTransportError(
                        f"Transport error contacting Kanban Tool API: {transport_error}"
                    ) from transport_error

        _raise_for_status(response, method, normalized)

        # DELETE endpoints conventionally return ``204 No Content`` (or 200
        # with an empty body) — there's nothing to JSON-decode. Returning
        # ``None`` lets callers like ``delete_timer`` propagate cleanly
        # without forcing them to bypass this method. Other verbs that
        # legitimately return empty bodies hit the same path.
        if response.status_code == 204 or not response.content:
            return None

        try:
            return response.json()
        except json.JSONDecodeError:
            raise KanbanToolHTTPError(
                f"Kanban Tool API returned non-JSON body for {method} {normalized}",
                status_code=response.status_code,
                body_excerpt=_scrub_secrets(response.text)[:_BODY_EXCERPT_LIMIT],
            ) from None
