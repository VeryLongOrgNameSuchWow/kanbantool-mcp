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
from kanbantool_mcp.models import Subtask, Task
from kanbantool_mcp.server import add_subtask, get_task, list_subtasks

from .conftest import BASE_URL

TASK_ID = 42
# ``list_subtasks`` rides on top of ``get_task`` because the Kanban Tool API has
# no dedicated list-subtasks endpoint — subtasks are inlined on the task detail.
TASK_URL = f"{BASE_URL}tasks/{TASK_ID}.json"
# ``add_subtask`` POSTs to the top-level ``/subtasks.json`` collection (NOT
# nested under ``tasks/{id}/...``); the parent linkage is in the body.
SUBTASKS_URL = f"{BASE_URL}subtasks.json"


def _request_body(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls.last.request.content)


def _task_payload(subtasks: list[dict[str, object]]) -> dict[str, object]:
    """Minimal ``GET /tasks/{id}.json`` shape with an inline ``subtasks`` array."""
    return {"id": TASK_ID, "name": "parent task", "subtasks": subtasks}


# ---------------------------------------------------------------------------
# Task.subtasks model round-trip
# ---------------------------------------------------------------------------


def test_task_subtasks_round_trips() -> None:
    """``Task.subtasks`` validates an inline list of ``Subtask`` objects and
    defaults to an empty list when the wire payload omits the field (compact
    list shape on ``search_tasks``)."""
    full = Task.model_validate(
        {
            "id": 1,
            "name": "parent",
            "subtasks": [
                {
                    "id": 11,
                    "name": "step",
                    "is_completed": False,
                    "position": 1,
                    "task_id": 1,
                    "assigned_user_id": 99,
                }
            ],
        }
    )
    assert len(full.subtasks) == 1
    sub = full.subtasks[0]
    assert isinstance(sub, Subtask)
    assert sub.id == 11
    assert sub.name == "step"
    assert sub.task_id == 1
    assert sub.assigned_user_id == 99

    bare = Task.model_validate({"id": 2, "name": "compact"})
    assert bare.subtasks == []


# ---------------------------------------------------------------------------
# list_subtasks (sugar over get_task)
# ---------------------------------------------------------------------------


async def test_list_subtasks_happy_path(_inject_client: KanbanToolClient) -> None:
    """A two-element inline ``subtasks`` array — one fully populated, one
    minimal — should round-trip into the typed model with optional fields
    defaulting to ``None`` on the minimal entry."""
    payload = _task_payload(
        [
            {
                "id": 1,
                "name": "Write spec",
                "is_completed": True,
                "position": 1,
                "task_id": TASK_ID,
                "assigned_user_id": 7,
                "extra_unknown_field": "ignored",
            },
            {"id": 2, "name": "Review PR"},
        ]
    )
    with respx.mock(assert_all_called=True) as router:
        router.get(TASK_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await list_subtasks(TASK_ID)

    assert len(result) == 2
    first, second = result
    assert first.id == 1
    assert first.name == "Write spec"
    assert first.is_completed is True
    assert first.position == 1
    assert first.task_id == TASK_ID
    assert first.assigned_user_id == 7

    assert second.id == 2
    assert second.name == "Review PR"
    assert second.is_completed is None
    assert second.position is None
    assert second.task_id is None
    assert second.assigned_user_id is None


async def test_list_subtasks_empty(_inject_client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(TASK_URL).mock(return_value=httpx.Response(200, json=_task_payload([])))
        result = await list_subtasks(TASK_ID)

    assert result == []


async def test_list_subtasks_missing_field(_inject_client: KanbanToolClient) -> None:
    """A task payload that omits the ``subtasks`` key entirely (e.g. a forward-compat
    shape change) should yield ``[]`` via the ``Task.subtasks`` default."""
    payload = {"id": TASK_ID, "name": "no subtasks key"}
    with respx.mock(assert_all_called=True) as router:
        router.get(TASK_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await list_subtasks(TASK_ID)

    assert result == []


async def test_list_subtasks_url_shape(_inject_client: KanbanToolClient) -> None:
    """GET hits ``tasks/{id}.json`` exactly — the route through ``get_task``
    means we must NOT hit any nested ``/subtasks`` path (no such endpoint)."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(TASK_URL).mock(return_value=httpx.Response(200, json=_task_payload([])))
        await list_subtasks(TASK_ID)

    request = route.calls.last.request
    assert request.method == "GET"
    assert str(request.url) == TASK_URL


async def test_list_subtasks_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(TASK_URL).mock(return_value=httpx.Response(404, text="task not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await list_subtasks(TASK_ID)
    assert exc_info.value.status_code == 404


async def test_list_subtasks_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.get(TASK_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await list_subtasks(TASK_ID)


async def test_get_task_surfaces_inline_subtasks(_inject_client: KanbanToolClient) -> None:
    """Sanity check that ``get_task`` parses the inline ``subtasks`` array —
    proves ``list_subtasks`` and ``Task.subtasks`` agree on the same wire path."""
    payload = _task_payload([{"id": 5, "name": "inline"}])
    with respx.mock(assert_all_called=True) as router:
        router.get(TASK_URL).mock(return_value=httpx.Response(200, json=payload))
        task = await get_task(TASK_ID)

    assert len(task.subtasks) == 1
    assert task.subtasks[0].id == 5
    assert task.subtasks[0].name == "inline"


# ---------------------------------------------------------------------------
# add_subtask
# ---------------------------------------------------------------------------


async def test_add_subtask_happy_path(_inject_client: KanbanToolClient) -> None:
    """Body must be the FLAT top-level ``{"name": ..., "task_id": ...}`` —
    NOT the Rails-style ``{"subtask": {...}}`` envelope used by ``tasks``
    POSTs. The live spike confirmed that the envelope shape makes the API
    drop the parent linkage."""
    response_payload = {
        "id": 17,
        "name": "Draft tests",
        "is_completed": False,
        "position": 3,
        "task_id": TASK_ID,
        "assigned_user_id": 99,
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
    assert subtask.task_id == TASK_ID
    assert subtask.assigned_user_id == 99

    body = _request_body(route)
    assert body == {"name": "Draft tests", "task_id": TASK_ID}


async def test_add_subtask_body_has_no_envelope(_inject_client: KanbanToolClient) -> None:
    """Regression guard: the top-level ``subtask`` envelope key must NOT appear.
    Live API silently ignores ``task_id`` if the body is wrapped, breaking the
    parent linkage — see the wire-quirk note on ``add_subtask``."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(SUBTASKS_URL).mock(
            return_value=httpx.Response(201, json={"id": 1, "name": "x", "task_id": TASK_ID})
        )
        await add_subtask(task_id=TASK_ID, title="x")

    body = _request_body(route)
    assert "subtask" not in body
    assert body["name"] == "x"
    assert body["task_id"] == TASK_ID


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
    assert subtask.position is None
    assert subtask.task_id is None
    assert subtask.assigned_user_id is None


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
    """POST hits the top-level ``subtasks.json`` collection — NOT
    ``tasks/{id}/subtasks.json`` (no such endpoint)."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(SUBTASKS_URL).mock(
            return_value=httpx.Response(201, json={"id": 1, "name": "x"})
        )
        await add_subtask(task_id=TASK_ID, title="x")

    request = route.calls.last.request
    assert request.method == "POST"
    assert str(request.url) == SUBTASKS_URL


async def test_add_subtask_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.post(SUBTASKS_URL).mock(return_value=httpx.Response(404, text="task not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await add_subtask(task_id=TASK_ID, title="x")
    assert exc_info.value.status_code == 404
    assert not isinstance(exc_info.value, KanbanToolValidationError)


async def test_add_subtask_500_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.post(SUBTASKS_URL).mock(return_value=httpx.Response(500, text="server exploded"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await add_subtask(task_id=TASK_ID, title="x")
    assert exc_info.value.status_code == 500
