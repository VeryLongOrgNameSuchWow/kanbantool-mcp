"""Tests for the list_subtasks and add_subtask MCP tools."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import (
    KanbanToolHTTPError,
    KanbanToolPermissionError,
    KanbanToolValidationError,
)
from kanbantool_mcp.server import add_subtask, list_subtasks

from .conftest import BASE_URL

TASK_ID = 42
SUBTASKS_URL = f"{BASE_URL}tasks/{TASK_ID}/subtasks.json"


def _request_body(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls.last.request.content)


# ---------------------------------------------------------------------------
# list_subtasks
# ---------------------------------------------------------------------------


async def test_list_subtasks_happy_path(_inject_client: KanbanToolClient) -> None:
    """A two-element ``subtasks`` array — one fully populated, one minimal —
    should round-trip into the typed model with optional fields defaulting to
    ``None`` on the minimal entry."""
    payload = {
        "subtasks": [
            {
                "id": 1,
                "name": "Write spec",
                "is_completed": True,
                "completed_at": "2026-04-29T10:00:00Z",
                "position": 1,
                "extra_unknown_field": "ignored",
            },
            {"id": 2, "name": "Review PR"},
        ]
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(SUBTASKS_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await list_subtasks(TASK_ID)

    assert len(result) == 2
    first, second = result
    assert first.id == 1
    assert first.name == "Write spec"
    assert first.is_completed is True
    assert first.completed_at == "2026-04-29T10:00:00Z"
    assert first.position == 1

    assert second.id == 2
    assert second.name == "Review PR"
    assert second.is_completed is None
    assert second.completed_at is None
    assert second.position is None


async def test_list_subtasks_empty(_inject_client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(SUBTASKS_URL).mock(return_value=httpx.Response(200, json={"subtasks": []}))
        result = await list_subtasks(TASK_ID)

    assert result == []


async def test_list_subtasks_missing_key(_inject_client: KanbanToolClient) -> None:
    """Defensive parse: a dict without a ``subtasks`` key returns ``[]`` rather
    than raising — mirrors ``list_boards`` so an unfamiliar wire shape doesn't
    blow up the tool."""
    with respx.mock(assert_all_called=True) as router:
        router.get(SUBTASKS_URL).mock(return_value=httpx.Response(200, json={"unrelated": "shape"}))
        result = await list_subtasks(TASK_ID)

    assert result == []


async def test_list_subtasks_url_shape(_inject_client: KanbanToolClient) -> None:
    """GET hits ``tasks/{id}/subtasks.json`` exactly — no double-suffix, no
    trailing slash, no query string."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SUBTASKS_URL).mock(
            return_value=httpx.Response(200, json={"subtasks": []})
        )
        await list_subtasks(TASK_ID)

    request = route.calls.last.request
    assert request.method == "GET"
    assert str(request.url) == SUBTASKS_URL


async def test_list_subtasks_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(SUBTASKS_URL).mock(return_value=httpx.Response(404, text="task not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await list_subtasks(TASK_ID)
    assert exc_info.value.status_code == 404


async def test_list_subtasks_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.get(SUBTASKS_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await list_subtasks(TASK_ID)


# ---------------------------------------------------------------------------
# add_subtask
# ---------------------------------------------------------------------------


async def test_add_subtask_happy_path(_inject_client: KanbanToolClient) -> None:
    """Body must be the ``{"subtask": {"name": ...}}`` Rails envelope; response
    parses straight into a ``Subtask`` (no defensive unwrap)."""
    response_payload = {
        "id": 17,
        "name": "Draft tests",
        "is_completed": False,
        "position": 3,
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.post(SUBTASKS_URL).mock(
            return_value=httpx.Response(201, json=response_payload)
        )
        subtask = await add_subtask(task_id=TASK_ID, title="Draft tests")

    assert subtask.id == 17
    assert subtask.name == "Draft tests"
    assert subtask.is_completed is False
    assert subtask.position == 3

    body = _request_body(route)
    assert body == {"subtask": {"name": "Draft tests"}}


async def test_add_subtask_minimal_response(_inject_client: KanbanToolClient) -> None:
    """A response with only ``id`` and ``name`` should still validate; optional
    fields fall back to ``None``."""
    with respx.mock(assert_all_called=True) as router:
        router.post(SUBTASKS_URL).mock(
            return_value=httpx.Response(201, json={"id": 1, "name": "x"})
        )
        subtask = await add_subtask(task_id=TASK_ID, title="x")

    assert subtask.id == 1
    assert subtask.name == "x"
    assert subtask.is_completed is None
    assert subtask.completed_at is None
    assert subtask.position is None


async def test_add_subtask_422_raises_validation_error(
    _inject_client: KanbanToolClient,
) -> None:
    """A 422 must surface as the typed ``KanbanToolValidationError`` with parsed
    ``field_errors`` — not just the base ``KanbanToolHTTPError``."""
    error_body = {"errors": {"name": ["can't be blank"]}}
    with respx.mock() as router:
        router.post(SUBTASKS_URL).mock(return_value=httpx.Response(422, json=error_body))
        with pytest.raises(KanbanToolValidationError) as exc_info:
            await add_subtask(task_id=TASK_ID, title="")

    err = exc_info.value
    assert err.status_code == 422
    assert err.field_errors == {"name": ["can't be blank"]}


async def test_add_subtask_url_shape(_inject_client: KanbanToolClient) -> None:
    """POST hits ``tasks/{id}/subtasks.json`` exactly."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(SUBTASKS_URL).mock(
            return_value=httpx.Response(201, json={"id": 1, "name": "x"})
        )
        await add_subtask(task_id=TASK_ID, title="x")

    request = route.calls.last.request
    assert request.method == "POST"
    assert str(request.url) == SUBTASKS_URL
