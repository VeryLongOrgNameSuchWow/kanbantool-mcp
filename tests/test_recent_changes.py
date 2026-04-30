"""Tests for the recent_changes MCP tool."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolHTTPError, KanbanToolPermissionError
from kanbantool_mcp.server import mcp, recent_changes

from .conftest import BASE_URL


def _changelog_url(board_id: int) -> str:
    return f"{BASE_URL}boards/{board_id}/changelog.json"


async def test_recent_changes_happy_path(_inject_client: KanbanToolClient) -> None:
    payload = {
        "changelog": [
            {
                "id": 1001,
                "created_at": "2026-04-30T10:15:00Z",
                "what": "created",
                "user_id": 7,
                "changed_object_type": "Task",
                "changed_object_id": 555,
                "description": "Ada Lovelace created Write spec",
                "data": {"user_initials": "AL", "task_name": "Write spec"},
                "extra_unknown_field": "ignored",
            },
            {
                "id": 1000,
                "created_at": "2026-04-30T09:00:00Z",
                "what": "moved",
                "changed_object_type": "Task",
                "changed_object_id": 555,
            },
        ]
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(_changelog_url(42)).mock(return_value=httpx.Response(200, json=payload))
        result = await recent_changes(42)

    assert len(result) == 2
    newest, older = result
    assert newest.id == 1001
    assert newest.created_at == datetime(2026, 4, 30, 10, 15, 0, tzinfo=UTC)
    assert newest.what == "created"
    assert newest.user_id == 7
    assert newest.changed_object_type == "Task"
    assert newest.changed_object_id == 555
    assert newest.description == "Ada Lovelace created Write spec"
    assert newest.data == {"user_initials": "AL", "task_name": "Write spec"}

    assert older.id == 1000
    assert older.what == "moved"
    assert older.user_id is None
    assert older.description is None
    assert older.data is None


async def test_recent_changes_passes_since_as_query_param(
    _inject_client: KanbanToolClient,
) -> None:
    since = datetime(2026, 4, 30, 8, 0, 0, tzinfo=UTC)
    with respx.mock(assert_all_called=True) as router:
        route = router.get(_changelog_url(42)).mock(
            return_value=httpx.Response(200, json={"changelog": []})
        )
        await recent_changes(42, since=since)

    request = route.calls.last.request
    assert request.url.params["since"] == since.isoformat()


async def test_recent_changes_omits_since_when_none(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(_changelog_url(42)).mock(
            return_value=httpx.Response(200, json={"changelog": []})
        )
        await recent_changes(42)

    request = route.calls.last.request
    assert "since" not in request.url.params
    # No query string at all when since is omitted.
    assert request.url.query == b""


async def test_recent_changes_empty(_inject_client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_changelog_url(42)).mock(
            return_value=httpx.Response(200, json={"changelog": []})
        )
        result = await recent_changes(42)

    assert result == []


async def test_recent_changes_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(_changelog_url(999)).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await recent_changes(999)
        assert exc_info.value.status_code == 404


async def test_recent_changes_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.get(_changelog_url(42)).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await recent_changes(42)


async def test_recent_changes_via_fastmcp_entrypoint_accepts_iso_string(
    _inject_client: KanbanToolClient,
) -> None:
    """The FastMCP runtime coerces a JSON-Schema ``date-time`` string into
    a ``datetime`` for us — this guards that boundary so MCP clients can
    pass ``since`` as plain ISO text."""
    iso = "2026-04-30T08:00:00+00:00"
    with respx.mock(assert_all_called=True) as router:
        route = router.get(_changelog_url(42)).mock(
            return_value=httpx.Response(200, json={"changelog": []})
        )
        tool = await mcp.get_tool("recent_changes")
        assert tool is not None
        await tool.run({"board_id": 42, "since": iso})

    assert route.calls.last.request.url.params["since"] == iso
