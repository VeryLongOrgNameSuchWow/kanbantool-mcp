"""Tests for the archive_task MCP tool."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolHTTPError, KanbanToolPermissionError
from kanbantool_mcp.server import archive_task

from .conftest import BASE_URL

TASK_ID = 4242
TASK_URL = f"{BASE_URL}tasks/{TASK_ID}.json"


def _request_body(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls.last.request.content)


async def test_archive_task_happy_path(_inject_client: KanbanToolClient) -> None:
    """Archiving a task issues PATCH /tasks/{id}.json with the flat
    ``{"_action": "archive"}`` sentinel body — NOT the ``{"task": {...}}``
    envelope used by update_task — and round-trips the response into a
    ``Task`` whose computed ``is_archived`` reflects the wire ``archived_at``."""
    response_payload = {
        "id": TASK_ID,
        "name": "Wrap up Q2 release",
        "board_id": 7,
        "archived_at": "2026-04-30T18:00:00Z",
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.patch(TASK_URL).mock(return_value=httpx.Response(200, json=response_payload))
        task = await archive_task(task_id=TASK_ID)

    assert task.id == TASK_ID
    assert task.archived_at == "2026-04-30T18:00:00Z"
    assert task.is_archived is True

    request = route.calls.last.request
    assert request.method == "PATCH"
    body = _request_body(route)
    # Flat top-level sentinel — no "task" envelope, no "archived_at" field.
    assert body == {"_action": "archive"}
    assert "task" not in body


async def test_archive_task_already_archived_is_idempotent(
    _inject_client: KanbanToolClient,
) -> None:
    """Re-archiving an already-archived task must not error. The API is
    assumed to return 200 with the task in its archived state regardless
    of prior state, so the call shape is identical to the happy path."""
    response_payload = {
        "id": TASK_ID,
        "name": "Already done",
        "board_id": 7,
        "archived_at": "2026-04-30T18:00:00Z",
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.patch(TASK_URL).mock(return_value=httpx.Response(200, json=response_payload))
        # Two consecutive archives — neither raises.
        first = await archive_task(task_id=TASK_ID)
        second = await archive_task(task_id=TASK_ID)

    assert first.is_archived is True
    assert second.is_archived is True
    assert len(route.calls) == 2
    # Second call body is identical to the first — idempotent on the wire.
    assert _request_body(route) == {"_action": "archive"}


async def test_archive_task_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    """A 404 (unknown task id) surfaces as the base ``KanbanToolHTTPError``."""
    with respx.mock() as router:
        router.patch(TASK_URL).mock(return_value=httpx.Response(404, text="task not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await archive_task(task_id=TASK_ID)

    assert exc_info.value.status_code == 404
    assert "task not found" in exc_info.value.body_excerpt


async def test_archive_task_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.patch(TASK_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await archive_task(task_id=TASK_ID)


async def test_archive_task_500_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.patch(TASK_URL).mock(return_value=httpx.Response(500, text="server exploded"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await archive_task(task_id=TASK_ID)
    assert exc_info.value.status_code == 500


async def test_archive_task_url_shape(_inject_client: KanbanToolClient) -> None:
    """PATCH hits ``tasks/{id}.json`` exactly — no ``/archive`` path segment,
    no double ``.json``, no query string, no trailing-slash artifact."""
    with respx.mock(assert_all_called=True) as router:
        route = router.patch(TASK_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": TASK_ID,
                    "name": "x",
                    "board_id": 2,
                    "archived_at": "2026-04-30T18:00:00Z",
                },
            )
        )
        await archive_task(task_id=TASK_ID)

    request = route.calls.last.request
    assert request.method == "PATCH"
    assert str(request.url) == TASK_URL
