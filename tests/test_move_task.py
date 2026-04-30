"""Tests for the move_task MCP tool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import (
    KanbanToolHTTPError,
    KanbanToolPermissionError,
    KanbanToolValidationError,
)
from kanbantool_mcp.server import move_task

from .conftest import BASE_URL

TASK_ID = 4242
TASK_URL = f"{BASE_URL}tasks/{TASK_ID}.json"


def _request_body(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls.last.request.content)


async def test_move_task_column_only_uses_patch(_inject_client: KanbanToolClient) -> None:
    """A column-only move issues PATCH (not PUT) with a body containing only
    ``workflow_stage_id`` — the caller-facing ``column_id`` alias is renamed
    on the wire, mirroring the ``lane_id`` alias used by ``update_task``."""
    response_payload = {"id": TASK_ID, "name": "Card", "workflow_stage_id": 100}
    with respx.mock(assert_all_called=True) as router:
        route = router.patch(TASK_URL).mock(return_value=httpx.Response(200, json=response_payload))
        task = await move_task(task_id=TASK_ID, column_id=100)

    assert task.id == TASK_ID
    assert task.lane_id == 100

    request = route.calls.last.request
    assert request.method == "PATCH"
    body = _request_body(route)
    assert body == {"task": {"workflow_stage_id": 100}}
    inner = body["task"]
    assert isinstance(inner, dict)
    # Caller-facing alias must not leak onto the wire.
    assert "column_id" not in inner


async def test_move_task_all_three_fields(_inject_client: KanbanToolClient) -> None:
    """Passing column, swimlane, and position together produces a single
    PATCH whose body carries all three (column_id renamed)."""
    response_payload = {
        "id": TASK_ID,
        "name": "Card",
        "workflow_stage_id": 200,
        "swimlane_id": 9,
        "position": 3,
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.patch(TASK_URL).mock(return_value=httpx.Response(200, json=response_payload))
        task = await move_task(task_id=TASK_ID, column_id=200, swimlane_id=9, position=3)

    inner = _request_body(route)["task"]
    assert isinstance(inner, dict)
    assert inner == {
        "workflow_stage_id": 200,
        "swimlane_id": 9,
        "position": 3,
    }
    assert task.lane_id == 200
    assert task.swimlane_id == 9
    assert task.position == 3


async def test_move_task_unset_fields_omitted(_inject_client: KanbanToolClient) -> None:
    """Fields the caller didn't pass must be absent from the body, not sent
    as ``null``. ``None`` means *omit* — the helper drops them before the
    envelope is built."""
    with respx.mock(assert_all_called=True) as router:
        route = router.patch(TASK_URL).mock(
            return_value=httpx.Response(200, json={"id": TASK_ID, "name": "Card", "position": 7})
        )
        await move_task(task_id=TASK_ID, position=7)

    inner = _request_body(route)["task"]
    assert isinstance(inner, dict)
    assert inner == {"position": 7}
    for absent_key in ("column_id", "workflow_stage_id", "swimlane_id"):
        assert absent_key not in inner


async def test_move_task_no_args_raises_value_error_without_http(
    monkeypatch: pytest.MonkeyPatch,
    _inject_client: KanbanToolClient,
) -> None:
    """All-None call is a client-side error — no HTTP issued, message names
    *this tool's* fields (not update_task's full list), so the LLM gets an
    actionable hint scoped to the move surface."""
    sentinel = AsyncMock(side_effect=AssertionError("move_task issued an HTTP call for a no-op"))
    monkeypatch.setattr(_inject_client, "request", sentinel)

    with pytest.raises(ValueError) as exc_info:
        await move_task(task_id=TASK_ID)

    msg = str(exc_info.value)
    # Each of move_task's caller-facing fields should be named.
    assert "column_id" in msg
    assert "swimlane_id" in msg
    assert "position" in msg
    # Fields that belong to update_task only must not bleed into this message.
    # Match against the field-list tokens (split on commas) so substring noise
    # from neighboring names — e.g. ``lane_id`` occurring inside
    # ``swimlane_id`` — doesn't trigger a false positive.
    listed = {token.strip().rstrip(".") for token in msg.split(":", 1)[1].split(",")}
    assert "name" not in listed
    assert "lane_id" not in listed
    assert "description" not in listed
    sentinel.assert_not_awaited()


async def test_move_task_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    """404 (task does not exist) surfaces as the base ``KanbanToolHTTPError``,
    not the validation subclass."""
    with respx.mock() as router:
        router.patch(TASK_URL).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await move_task(task_id=TASK_ID, column_id=100)

    err = exc_info.value
    assert err.status_code == 404
    assert not isinstance(err, KanbanToolValidationError)


async def test_move_task_422_raises_validation_error(_inject_client: KanbanToolClient) -> None:
    """422 (typically: target column doesn't belong to this task's board) must
    surface as the typed ``KanbanToolValidationError`` subclass with parsed
    ``field_errors``."""
    error_body = {"errors": {"workflow_stage_id": ["is not a valid stage on this board"]}}
    with respx.mock() as router:
        router.patch(TASK_URL).mock(return_value=httpx.Response(422, json=error_body))
        with pytest.raises(KanbanToolValidationError) as exc_info:
            await move_task(task_id=TASK_ID, column_id=999999)

    err = exc_info.value
    assert isinstance(err, KanbanToolValidationError)
    assert err.status_code == 422
    assert err.field_errors == {
        "workflow_stage_id": ["is not a valid stage on this board"],
    }


async def test_move_task_401_raises_permission_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.patch(TASK_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await move_task(task_id=TASK_ID, column_id=100)


async def test_move_task_url_shape(_inject_client: KanbanToolClient) -> None:
    """PATCH hits ``tasks/{id}.json`` exactly — no double ``.json`` suffix, no
    query string, no trailing slash artifact."""
    with respx.mock(assert_all_called=True) as router:
        route = router.patch(TASK_URL).mock(
            return_value=httpx.Response(200, json={"id": TASK_ID, "name": "Card"})
        )
        await move_task(task_id=TASK_ID, column_id=100)

    request = route.calls.last.request
    assert request.method == "PATCH"
    assert str(request.url) == TASK_URL
