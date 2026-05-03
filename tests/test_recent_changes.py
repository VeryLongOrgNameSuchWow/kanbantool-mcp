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

# Boundary timestamp used as ``since=`` across most tests. Hoisted from
# the per-test repeats so the wire-side ``params["since"]`` assertion
# below matches the same identifier.
_SINCE = datetime(2026, 4, 30, 8, 0, 0, tzinfo=UTC)


def _changelog_url(board_id: int) -> str:
    return f"{BASE_URL}boards/{board_id}/changelog.json"


async def test_recent_changes_happy_path(_inject_client: KanbanToolClient) -> None:
    payload = [
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
    with respx.mock(assert_all_called=True) as router:
        router.get(_changelog_url(42)).mock(return_value=httpx.Response(200, json=payload))
        result = await recent_changes(42, since=_SINCE)

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
    with respx.mock(assert_all_called=True) as router:
        route = router.get(_changelog_url(42)).mock(return_value=httpx.Response(200, json=[]))
        await recent_changes(42, since=_SINCE)

    request = route.calls.last.request
    assert request.url.params["since"] == _SINCE.isoformat()


async def test_recent_changes_rejects_none_since(
    _inject_client: KanbanToolClient,
) -> None:
    """``since=None`` raises ValueError at the tool boundary with a
    first-poll-friendly fix-it hint, before any HTTP call fires. Locks
    both the rejection behaviour and the actionable error wording so
    callers (LLMs) see the same message that they need to act on."""
    with pytest.raises(ValueError, match=r"since.*first-poll"):
        await recent_changes(42, since=None)


async def test_recent_changes_rejects_wire_null_since(
    _inject_client: KanbanToolClient,
) -> None:
    """The wire-null path (an MCP client sending ``{"since": null}`` over
    JSON) must surface the same actionable ValueError as a Python
    ``since=None``. ``datetime | None`` keeps null type-valid so the
    runtime guard fires instead of an opaque pydantic ValidationError."""
    tool = await mcp.get_tool("recent_changes")
    assert tool is not None
    # ``tool.run`` is FastMCP's dispatch entrypoint — same path the wire
    # takes when an MCP client invokes the tool with ``since=null``.
    with pytest.raises(ValueError, match=r"since.*first-poll"):
        await tool.run({"board_id": 42, "since": None})


async def test_recent_changes_empty(_inject_client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_changelog_url(42)).mock(return_value=httpx.Response(200, json=[]))
        result = await recent_changes(42, since=_SINCE)

    assert result == []


async def test_recent_changes_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(_changelog_url(999)).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await recent_changes(999, since=_SINCE)
        assert exc_info.value.status_code == 404


async def test_recent_changes_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.get(_changelog_url(42)).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await recent_changes(42, since=_SINCE)


async def test_recent_changes_malformed_entry_raises_http_error(
    _inject_client: KanbanToolClient,
) -> None:
    """A 200 with a changelog entry missing the required ``id`` field surfaces
    as ``KanbanToolHTTPError(status_code=200)`` with a ``malformed``-tagged
    excerpt — never a raw ``pydantic.ValidationError``."""
    payload = [
        {"id": 1, "created_at": "2026-04-30T10:00:00Z"},
        {"created_at": "2026-04-30T09:00:00Z"},  # missing id
    ]
    with respx.mock(assert_all_called=True) as router:
        router.get(_changelog_url(42)).mock(return_value=httpx.Response(200, json=payload))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await recent_changes(42, since=_SINCE)

    assert exc_info.value.status_code == 200
    assert "malformed" in exc_info.value.body_excerpt


async def test_recent_changes_via_fastmcp_entrypoint_accepts_iso_string(
    _inject_client: KanbanToolClient,
) -> None:
    """The FastMCP runtime coerces a JSON-Schema ``date-time`` string into
    a ``datetime`` for us — this guards that boundary so MCP clients can
    pass ``since`` as plain ISO text."""
    iso = "2026-04-30T08:00:00+00:00"
    with respx.mock(assert_all_called=True) as router:
        route = router.get(_changelog_url(42)).mock(return_value=httpx.Response(200, json=[]))
        tool = await mcp.get_tool("recent_changes")
        assert tool is not None
        await tool.run({"board_id": 42, "since": iso})

    assert route.calls.last.request.url.params["since"] == iso
