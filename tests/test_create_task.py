"""Tests for the create_task MCP tool."""

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
from kanbantool_mcp.server import create_task

from .conftest import BASE_URL

TASKS_URL = f"{BASE_URL}tasks.json"


def _request_body(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls.last.request.content)


async def test_create_task_happy_path_minimal_body(_inject_client: KanbanToolClient) -> None:
    """A minimal call (name + board_id) should produce a ``{"task": {...}}``
    envelope with exactly those two fields and round-trip into a ``Task``."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(TASKS_URL).mock(
            return_value=httpx.Response(201, json={"id": 99, "name": "Tiny", "board_id": 7})
        )
        task = await create_task(name="Tiny", board_id=7)

    assert task.id == 99
    assert task.name == "Tiny"
    assert task.board_id == 7

    body = _request_body(route)
    assert body == {"task": {"name": "Tiny", "board_id": 7}}


async def test_create_task_full_payload_renames_lane_id(_inject_client: KanbanToolClient) -> None:
    """Every optional field set; ``lane_id`` must serialize as
    ``workflow_stage_id`` on the wire, matching the inbound alias."""
    response_payload = {
        "id": 4242,
        "name": "Ship the thing",
        "description": "Cut a release once CI is green.",
        "board_id": 7,
        "workflow_stage_id": 100,
        "position": 3,
        "priority": "high",
        "due_date": "2026-05-15T00:00:00Z",
        "tags": "release,urgent",
        "assigned_user_id": 11,
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.post(TASKS_URL).mock(return_value=httpx.Response(201, json=response_payload))
        task = await create_task(
            name="Ship the thing",
            board_id=7,
            description="Cut a release once CI is green.",
            lane_id=100,
            position=3,
            assignees=[11, 22],
            due_date="2026-05-15T00:00:00Z",
            priority="high",
            tags="release,urgent",
        )

    body = _request_body(route)
    inner = body["task"]
    assert isinstance(inner, dict)
    assert inner == {
        "name": "Ship the thing",
        "board_id": 7,
        "description": "Cut a release once CI is green.",
        "workflow_stage_id": 100,
        "position": 3,
        "assignees": [11, 22],
        "due_date": "2026-05-15T00:00:00Z",
        "priority": "high",
        "tags": "release,urgent",
    }
    assert "lane_id" not in inner

    # Round-trip: the response is parsed back into a Task with the same alias.
    assert task.id == 4242
    assert task.name == "Ship the thing"
    assert task.lane_id == 100
    assert task.board_id == 7
    assert task.priority == "high"
    assert task.due_date == "2026-05-15T00:00:00Z"
    assert task.tags == "release,urgent"
    assert task.assigned_user_id == 11


async def test_create_task_unset_optionals_omitted_not_nulled(
    _inject_client: KanbanToolClient,
) -> None:
    """Fields the caller didn't pass must be absent from the body, not sent
    as ``null`` (Kanban Tool may interpret null as an explicit clear)."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(TASKS_URL).mock(
            return_value=httpx.Response(201, json={"id": 1, "name": "x", "board_id": 2})
        )
        await create_task(name="x", board_id=2)

    inner = _request_body(route)["task"]
    assert isinstance(inner, dict)
    for absent_key in (
        "description",
        "workflow_stage_id",
        "lane_id",
        "position",
        "assignees",
        "due_date",
        "priority",
        "tags",
    ):
        assert absent_key not in inner


async def test_create_task_422_field_errors_parsed(_inject_client: KanbanToolClient) -> None:
    error_body = {"errors": {"name": ["can't be blank"], "board_id": ["not found"]}}
    with respx.mock() as router:
        router.post(TASKS_URL).mock(
            return_value=httpx.Response(422, json=error_body),
        )
        with pytest.raises(KanbanToolValidationError) as exc_info:
            await create_task(name="", board_id=7)

    err = exc_info.value
    assert err.status_code == 422
    assert err.field_errors == {
        "name": ["can't be blank"],
        "board_id": ["not found"],
    }
    assert err.body_excerpt
    # __str__ renders compactly so callers logging the exception get the gist.
    rendered = str(err)
    assert "name: can't be blank" in rendered
    assert "board_id: not found" in rendered


async def test_create_task_422_is_also_caught_as_http_error(
    _inject_client: KanbanToolClient,
) -> None:
    """Subclassing ``KanbanToolHTTPError`` keeps existing handlers working."""
    with respx.mock() as router:
        router.post(TASKS_URL).mock(
            return_value=httpx.Response(422, json={"errors": {"name": ["blank"]}}),
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await create_task(name="", board_id=7)
    # Specifically a validation error, not the bare base class.
    assert isinstance(exc_info.value, KanbanToolValidationError)


async def test_create_task_422_non_json_body_falls_back(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.post(TASKS_URL).mock(
            return_value=httpx.Response(422, text="<html>nope</html>"),
        )
        with pytest.raises(KanbanToolValidationError) as exc_info:
            await create_task(name="", board_id=7)

    err = exc_info.value
    assert err.status_code == 422
    assert err.field_errors == {}
    assert "<html>" in err.body_excerpt


async def test_create_task_422_body_scrubs_bearer_token(
    _inject_client: KanbanToolClient,
) -> None:
    """Even on a validation error, the surfaced excerpt must not echo a
    leaked bearer token from an upstream proxy/WAF."""
    leak = "Authorization: Bearer leaked-token-secret-xyz failed"
    with respx.mock() as router:
        router.post(TASKS_URL).mock(return_value=httpx.Response(422, text=leak))
        with pytest.raises(KanbanToolValidationError) as exc_info:
            await create_task(name="", board_id=7)
    assert "leaked-token-secret-xyz" not in exc_info.value.body_excerpt
    assert "Bearer ***" in exc_info.value.body_excerpt


async def test_create_task_422_field_errors_scrub_bearer_token(
    _inject_client: KanbanToolClient,
) -> None:
    """A bearer token leaked inside a 422 JSON field-error message must be
    scrubbed out of ``field_errors`` and the rendered ``__str__`` — covers the
    JSON-decoded path, not just the raw ``body_excerpt`` fallback."""
    leaked_token = "leaked-token-foo-xyz"
    error_body = {
        "errors": {
            "name": [f"can't be blank (Authorization: Bearer {leaked_token})"],
        }
    }
    with respx.mock() as router:
        router.post(TASKS_URL).mock(
            return_value=httpx.Response(422, json=error_body),
        )
        with pytest.raises(KanbanToolValidationError) as exc_info:
            await create_task(name="", board_id=7)

    err = exc_info.value
    rendered_field_errors = json.dumps(err.field_errors)
    assert leaked_token not in rendered_field_errors
    assert "Bearer ***" in rendered_field_errors

    rendered = str(err)
    assert leaked_token not in rendered
    assert "Bearer ***" in rendered


async def test_create_task_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.post(TASKS_URL).mock(
            return_value=httpx.Response(404, text="board not found"),
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await create_task(name="x", board_id=999999)
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.body_excerpt
    # 404 must NOT be promoted to a validation error.
    assert not isinstance(exc_info.value, KanbanToolValidationError)


async def test_create_task_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.post(TASKS_URL).mock(
            return_value=httpx.Response(401, text="unauthorized"),
        )
        with pytest.raises(KanbanToolPermissionError):
            await create_task(name="x", board_id=7)


async def test_create_task_403_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    """403 (token valid but not authorized to write to this board) surfaces as
    ``KanbanToolPermissionError`` — distinct from a 422 validation failure."""
    with respx.mock() as router:
        router.post(TASKS_URL).mock(
            return_value=httpx.Response(403, text="forbidden"),
        )
        with pytest.raises(KanbanToolPermissionError):
            await create_task(name="x", board_id=7)


async def test_create_task_500_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.post(TASKS_URL).mock(return_value=httpx.Response(500, text="server exploded"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await create_task(name="x", board_id=7)
    assert exc_info.value.status_code == 500


async def test_create_task_url_shape(_inject_client: KanbanToolClient) -> None:
    """POST hits ``tasks.json`` exactly — no ``tasks.json.json`` double-suffix,
    no ``tasks/.json``, no query string."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(TASKS_URL).mock(
            return_value=httpx.Response(201, json={"id": 1, "name": "x", "board_id": 2})
        )
        await create_task(name="x", board_id=2)

    request = route.calls.last.request
    assert request.method == "POST"
    assert str(request.url) == TASKS_URL
