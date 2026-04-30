"""Tests for the update_task MCP tool."""

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
from kanbantool_mcp.server import update_task

from .conftest import BASE_URL

TASK_ID = 4242
TASK_URL = f"{BASE_URL}tasks/{TASK_ID}.json"


def _request_body(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls.last.request.content)


async def test_update_task_happy_path_single_field(_inject_client: KanbanToolClient) -> None:
    """A single-field update should produce a ``{"task": {...}}`` envelope
    containing only that field, hit PUT ``/tasks/{id}.json``, and round-trip
    the response into a ``Task``."""
    response_payload = {"id": TASK_ID, "name": "Renamed", "board_id": 7}
    with respx.mock(assert_all_called=True) as router:
        route = router.put(TASK_URL).mock(return_value=httpx.Response(200, json=response_payload))
        task = await update_task(task_id=TASK_ID, name="Renamed")

    assert task.id == TASK_ID
    assert task.name == "Renamed"

    request = route.calls.last.request
    assert request.method == "PUT"
    body = _request_body(route)
    assert body == {"task": {"name": "Renamed"}}


async def test_update_task_multi_field_renames_lane_id(_inject_client: KanbanToolClient) -> None:
    """Multi-field update: ``lane_id`` must serialize as ``workflow_stage_id``
    on the wire, matching the inbound alias and ``create_task``'s outbound
    rename."""
    response_payload = {
        "id": TASK_ID,
        "name": "Reshuffled",
        "board_id": 7,
        "workflow_stage_id": 200,
        "position": 5,
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.put(TASK_URL).mock(return_value=httpx.Response(200, json=response_payload))
        task = await update_task(
            task_id=TASK_ID,
            name="Reshuffled",
            lane_id=200,
            position=5,
            priority="high",
            tags="release,urgent",
        )

    inner = _request_body(route)["task"]
    assert isinstance(inner, dict)
    assert inner == {
        "name": "Reshuffled",
        "workflow_stage_id": 200,
        "position": 5,
        "priority": "high",
        "tags": "release,urgent",
    }
    # The caller-facing key must not leak onto the wire.
    assert "lane_id" not in inner

    # Round-trip: the response is parsed back via the same alias.
    assert task.lane_id == 200
    assert task.position == 5


async def test_update_task_unset_optionals_omitted_not_nulled(
    _inject_client: KanbanToolClient,
) -> None:
    """Fields the caller didn't pass must be absent from the body, not sent
    as ``null``. ``None`` means *omit*, not *clear* — see docstring."""
    with respx.mock(assert_all_called=True) as router:
        route = router.put(TASK_URL).mock(
            return_value=httpx.Response(200, json={"id": TASK_ID, "name": "x", "board_id": 2})
        )
        await update_task(task_id=TASK_ID, name="x")

    inner = _request_body(route)["task"]
    assert isinstance(inner, dict)
    assert inner == {"name": "x"}
    for absent_key in (
        "description",
        "board_id",
        "lane_id",
        "workflow_stage_id",
        "swimlane_id",
        "position",
        "priority",
        "color",
        "due_date",
        "start_date",
        "tags",
        "assignees",
    ):
        assert absent_key not in inner


async def test_update_task_no_args_raises_value_error_without_http(
    monkeypatch: pytest.MonkeyPatch,
    _inject_client: KanbanToolClient,
) -> None:
    """A call with no updatable kwargs is a client-side error — the LLM gets
    a ``ValueError`` naming the available fields, and no HTTP request is
    issued."""
    # Patch the underlying client.request to make sure it's never called.
    sentinel = AsyncMock(side_effect=AssertionError("update_task issued an HTTP call for a no-op"))
    monkeypatch.setattr(_inject_client, "request", sentinel)

    with pytest.raises(ValueError) as exc_info:
        await update_task(task_id=TASK_ID)

    # Message should name some of the available fields so the LLM can self-correct.
    msg = str(exc_info.value)
    assert "name" in msg
    assert "lane_id" in msg
    sentinel.assert_not_awaited()


async def test_update_task_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.put(TASK_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await update_task(task_id=TASK_ID, name="x")


async def test_update_task_422_raises_validation_error_with_field_errors(
    _inject_client: KanbanToolClient,
) -> None:
    """422 must surface as the typed ``KanbanToolValidationError`` subclass
    (not the bare base) with parsed ``field_errors`` populated."""
    error_body = {"errors": {"name": ["can't be blank"], "due_date": ["is invalid"]}}
    with respx.mock() as router:
        router.put(TASK_URL).mock(return_value=httpx.Response(422, json=error_body))
        with pytest.raises(KanbanToolValidationError) as exc_info:
            await update_task(task_id=TASK_ID, name="", due_date="not-a-date")

    err = exc_info.value
    # Specifically the typed subclass, not the bare KanbanToolHTTPError.
    assert isinstance(err, KanbanToolValidationError)
    assert err.status_code == 422
    assert err.field_errors == {
        "name": ["can't be blank"],
        "due_date": ["is invalid"],
    }
    assert err.body_excerpt


async def test_update_task_500_raises_http_error(_inject_client: KanbanToolClient) -> None:
    """Generic 5xx surfaces as the base ``KanbanToolHTTPError`` — not promoted
    to the validation subclass."""
    with respx.mock() as router:
        router.put(TASK_URL).mock(return_value=httpx.Response(500, text="server exploded"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await update_task(task_id=TASK_ID, name="x")

    err = exc_info.value
    assert err.status_code == 500
    assert "server exploded" in err.body_excerpt
    assert not isinstance(err, KanbanToolValidationError)


async def test_update_task_url_shape(_inject_client: KanbanToolClient) -> None:
    """PUT hits ``tasks/{id}.json`` exactly — no double ``.json`` suffix, no
    query string, no trailing slash artifact."""
    with respx.mock(assert_all_called=True) as router:
        route = router.put(TASK_URL).mock(
            return_value=httpx.Response(200, json={"id": TASK_ID, "name": "x", "board_id": 2})
        )
        await update_task(task_id=TASK_ID, name="x")

    request = route.calls.last.request
    assert request.method == "PUT"
    assert str(request.url) == TASK_URL
