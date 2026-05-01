"""Tests for the time-tracker tools: start_timer, stop_timer, delete_timer,
list_my_timers."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolHTTPError, KanbanToolPermissionError
from kanbantool_mcp.models import TimeTracker
from kanbantool_mcp.server import (
    delete_timer,
    list_my_timers,
    start_timer,
    stop_timer,
)

from .conftest import BASE_URL

TIMERS_URL = f"{BASE_URL}time_trackers.json"
USERS_CURRENT_URL = f"{BASE_URL}users/current.json"


def _timer_url(timer_id: int) -> str:
    return f"{BASE_URL}time_trackers/{timer_id}.json"


def _request_body(route: respx.Route) -> dict[str, Any]:
    return json.loads(route.calls.last.request.content)


def _timer_payload(
    *,
    id: int = 1,
    user_id: int = 7,
    board_id: int = 4711,
    task_id: int = 50000,
    started_at: str = "2026-05-01T18:00:00.000+02:00",
    ended_at: str | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Realistic-shaped TimeTracker wire payload — matches what the live
    spike returned. The nested ``task`` is included to verify the
    extra="ignore" drop."""
    payload: dict[str, Any] = {
        "id": id,
        "user_id": user_id,
        "board_id": board_id,
        "task_id": task_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "listed": True,
        "sprint_id": task_id,
        "seconds_from_resumed_sprint": 0,
        "position": 1,
        "highlighted_at": None,
        "enlist_at": None,
        "created_at": started_at,
        "updated_at": started_at,
        # Nested ``task`` object that the API includes; we drop via extra=ignore
        "task": {"id": task_id, "name": "parent task", "priority": 0},
    }
    if extras:
        payload.update(extras)
    return payload


# ---------------------------------------------------------------------------
# TimeTracker model
# ---------------------------------------------------------------------------


def test_time_tracker_model_round_trips() -> None:
    """Validate a realistic wire payload, drop the nested ``task``, surface
    the ``is_running`` computed flag."""
    timer = TimeTracker.model_validate(_timer_payload(id=42, ended_at=None))

    assert timer.id == 42
    assert timer.is_running is True
    # ``task`` is silently dropped by ``extra="ignore"``.
    dump = timer.model_dump()
    assert "task" not in dump


def test_time_tracker_is_running_false_when_ended() -> None:
    """Stopped timer has ``is_running == False``."""
    timer = TimeTracker.model_validate(_timer_payload(ended_at="2026-05-01T19:00:00.000+02:00"))
    assert timer.is_running is False


# ---------------------------------------------------------------------------
# start_timer
# ---------------------------------------------------------------------------


async def test_start_timer_happy_path(_inject_client: KanbanToolClient) -> None:
    """POST hits ``time_trackers.json`` with a flat body containing both
    ``board_id`` and ``task_id`` (live spike confirmed both required)."""
    response = _timer_payload(id=999, board_id=4711, task_id=50000)
    with respx.mock(assert_all_called=True) as router:
        route = router.post(TIMERS_URL).mock(return_value=httpx.Response(200, json=response))
        result = await start_timer(task_id=50000, board_id=4711)

    assert isinstance(result, TimeTracker)
    assert result.id == 999
    assert result.task_id == 50000
    body = _request_body(route)
    assert body == {"board_id": 4711, "task_id": 50000}


async def test_start_timer_body_has_no_envelope(_inject_client: KanbanToolClient) -> None:
    """Wire shape regression guard: NO ``{"time_tracker": {...}}`` envelope."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(TIMERS_URL).mock(
            return_value=httpx.Response(200, json=_timer_payload())
        )
        await start_timer(task_id=1, board_id=2)

    body = _request_body(route)
    assert "time_tracker" not in body
    assert "board_id" in body
    assert "task_id" in body


async def test_start_timer_rejects_non_positive_ids(_inject_client: KanbanToolClient) -> None:
    """``validate_call`` enforces ``ge=1`` on both args so we never hit the
    API with bogus ids that would 404 confusingly."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await start_timer(task_id=0, board_id=1)
    with pytest.raises(ValidationError):
        await start_timer(task_id=1, board_id=0)


async def test_start_timer_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    """API returns 404 when ``board_id`` / ``task_id`` mismatch — surface
    as the typed error."""
    with respx.mock() as router:
        router.post(TIMERS_URL).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await start_timer(task_id=1, board_id=2)
        assert exc_info.value.status_code == 404


async def test_start_timer_malformed_response_raises_http_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.post(TIMERS_URL).mock(return_value=httpx.Response(200, json={"name": "no-id"}))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await start_timer(task_id=1, board_id=2)
    assert exc_info.value.status_code == 200
    assert "malformed" in exc_info.value.body_excerpt


# ---------------------------------------------------------------------------
# stop_timer
# ---------------------------------------------------------------------------


async def test_stop_timer_with_explicit_ended_at(_inject_client: KanbanToolClient) -> None:
    """Caller-supplied ``ended_at`` is forwarded verbatim in the PUT body."""
    explicit_ts = "2026-05-01T19:30:00.000+02:00"
    response = _timer_payload(id=42, ended_at=explicit_ts)
    with respx.mock(assert_all_called=True) as router:
        route = router.put(_timer_url(42)).mock(return_value=httpx.Response(200, json=response))
        result = await stop_timer(timer_id=42, ended_at=explicit_ts)

    assert isinstance(result, TimeTracker)
    assert result.is_running is False
    body = _request_body(route)
    assert body == {"ended_at": explicit_ts}


async def test_stop_timer_defaults_ended_at_to_now(_inject_client: KanbanToolClient) -> None:
    """Omitting ``ended_at`` defaults to current UTC ISO timestamp ending in
    ``Z`` — verifying the format the API echoes back."""
    with respx.mock(assert_all_called=True) as router:
        route = router.put(_timer_url(42)).mock(
            return_value=httpx.Response(200, json=_timer_payload(id=42, ended_at="2026"))
        )
        await stop_timer(timer_id=42)

    body = _request_body(route)
    # ISO 8601 with millisecond precision + Z suffix, e.g.
    # "2026-05-01T17:32:11.123Z"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", body["ended_at"])


async def test_stop_timer_body_has_no_envelope(_inject_client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.put(_timer_url(42)).mock(
            return_value=httpx.Response(200, json=_timer_payload(id=42))
        )
        await stop_timer(timer_id=42, ended_at="2026-05-01T19:00:00.000Z")

    body = _request_body(route)
    assert "time_tracker" not in body


async def test_stop_timer_rejects_non_positive_id(_inject_client: KanbanToolClient) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await stop_timer(timer_id=0)


async def test_stop_timer_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.put(_timer_url(999)).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await stop_timer(timer_id=999, ended_at="2026-05-01T19:00:00Z")
        assert exc_info.value.status_code == 404


async def test_stop_timer_malformed_response_raises_http_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.put(_timer_url(42)).mock(return_value=httpx.Response(200, json={"name": "no-id"}))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await stop_timer(timer_id=42, ended_at="2026-05-01T19:00:00Z")
    assert exc_info.value.status_code == 200
    assert "malformed" in exc_info.value.body_excerpt


# ---------------------------------------------------------------------------
# delete_timer
# ---------------------------------------------------------------------------


async def test_delete_timer_returns_none_on_empty_response(
    _inject_client: KanbanToolClient,
) -> None:
    """The API returns 204/empty body on DELETE; the tool's typed return is
    ``None``."""
    with respx.mock(assert_all_called=True) as router:
        route = router.delete(_timer_url(42)).mock(return_value=httpx.Response(204))
        result = await delete_timer(timer_id=42)

    assert result is None
    request = route.calls.last.request
    assert request.method == "DELETE"
    assert str(request.url) == _timer_url(42)


async def test_delete_timer_rejects_non_positive_id(_inject_client: KanbanToolClient) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await delete_timer(timer_id=0)


async def test_delete_timer_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.delete(_timer_url(999)).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await delete_timer(timer_id=999)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# list_my_timers
# ---------------------------------------------------------------------------


async def test_list_my_timers_extracts_from_users_current(
    _inject_client: KanbanToolClient,
) -> None:
    """The list comes off ``GET /users/current.json``'s ``time_trackers``
    array — there's no dedicated list endpoint."""
    user_payload = {
        "id": 7,
        "name": "Test User",
        "time_trackers": [
            _timer_payload(id=1, ended_at=None),
            _timer_payload(id=2, ended_at="2026-05-01T19:00:00.000+02:00"),
        ],
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(USERS_CURRENT_URL).mock(return_value=httpx.Response(200, json=user_payload))
        result = await list_my_timers()

    assert len(result) == 2
    assert all(isinstance(t, TimeTracker) for t in result)
    assert [t.id for t in result] == [1, 2]
    assert result[0].is_running is True
    assert result[1].is_running is False


async def test_list_my_timers_empty_when_no_timers(_inject_client: KanbanToolClient) -> None:
    """A user with no timers (empty array on the wire) returns ``[]``."""
    with respx.mock(assert_all_called=True) as router:
        router.get(USERS_CURRENT_URL).mock(
            return_value=httpx.Response(200, json={"id": 7, "time_trackers": []})
        )
        result = await list_my_timers()

    assert result == []


async def test_list_my_timers_missing_field(_inject_client: KanbanToolClient) -> None:
    """If the user payload omits ``time_trackers`` entirely (e.g. on accounts
    without time tracking), surface as ``[]``."""
    with respx.mock(assert_all_called=True) as router:
        router.get(USERS_CURRENT_URL).mock(
            return_value=httpx.Response(200, json={"id": 7, "name": "no-tracking"})
        )
        result = await list_my_timers()

    assert result == []


async def test_list_my_timers_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.get(USERS_CURRENT_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await list_my_timers()


async def test_list_my_timers_malformed_entry_raises_http_error(
    _inject_client: KanbanToolClient,
) -> None:
    """A 200 with a timer entry missing ``id`` surfaces as
    ``KanbanToolHTTPError(status_code=200)`` — never raw pydantic."""
    payload = {
        "id": 7,
        "time_trackers": [
            _timer_payload(id=1),
            {"user_id": 7},  # missing id
        ],
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(USERS_CURRENT_URL).mock(return_value=httpx.Response(200, json=payload))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await list_my_timers()

    assert exc_info.value.status_code == 200
    assert "malformed" in exc_info.value.body_excerpt


# ---------------------------------------------------------------------------
# Task.time_trackers round-trip
# ---------------------------------------------------------------------------


def test_task_time_trackers_round_trips() -> None:
    """``Task.time_trackers`` validates an inline list of ``TimeTracker``
    objects; defaults to ``[]`` for compact list-style payloads that omit
    the field."""
    from kanbantool_mcp.models import Task

    full = Task.model_validate(
        {
            "id": 50000,
            "name": "parent",
            "time_trackers": [_timer_payload(id=1, task_id=50000)],
        }
    )
    assert len(full.time_trackers) == 1
    assert isinstance(full.time_trackers[0], TimeTracker)
    assert full.time_trackers[0].id == 1

    bare = Task.model_validate({"id": 50001, "name": "compact"})
    assert bare.time_trackers == []
