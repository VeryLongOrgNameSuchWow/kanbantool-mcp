"""Tests for the get_board MCP tool."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import ValidationError

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolHTTPError
from kanbantool_mcp.models import Column
from kanbantool_mcp.server import get_board

from .conftest import BASE_URL


def _board_url(board_id: int) -> str:
    return f"{BASE_URL}boards/{board_id}.json"


async def test_get_board_happy_path(_inject_client: KanbanToolClient) -> None:
    payload = {
        "id": 42,
        "name": "Engineering",
        "description": "Eng board",
        "slug": "engineering",
        "use_swimlanes": True,
        "is_archived": False,
        "user_role": "admin",
        "extra_unknown_field": "ignored",
        "workflow_stages": [
            {
                "id": 100,
                "name": "Backlog",
                "position": 1,
                "parent_id": None,
                "wip_limit": None,
                "type": "queue",
                "lane_type": "backlog_inventory",
                "extra_stage_field": "ignored",
            },
            {
                "id": 101,
                "name": "In Progress",
                "position": 2,
                "wip_limit": 5,
                "type": "in-progress",
                "lane_type": "in_progress",
            },
        ],
        "swimlanes": [
            {"id": 200, "name": "Default", "position": 1, "extra_lane_field": "ignored"},
            {"id": 201, "name": "Expedite", "position": 2},
        ],
        "card_template": {
            "description": {"enabled": True, "position": 1},
            "priority": {"enabled": True, "position": 2},
            "custom_field_1": {
                "enabled": True,
                "position": 3,
                "label": "Story Points",
                "type": "number",
            },
        },
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(_board_url(42)).mock(return_value=httpx.Response(200, json=payload))
        board = await get_board(42)

    assert board.id == 42
    assert board.name == "Engineering"
    assert board.description == "Eng board"
    assert board.slug == "engineering"
    assert board.use_swimlanes is True
    assert board.is_archived is False
    assert board.user_role == "admin"

    assert len(board.columns) == 2
    backlog, in_progress = board.columns
    assert backlog.id == 100
    assert backlog.name == "Backlog"
    assert backlog.position == 1
    assert backlog.parent_id is None
    assert backlog.wip_limit is None
    assert backlog.type_ == "queue"
    assert backlog.lane_type == "backlog_inventory"
    assert in_progress.wip_limit == 5
    assert in_progress.type_ == "in-progress"
    assert in_progress.lane_type == "in_progress"

    assert len(board.swimlanes) == 2
    default_lane, expedite_lane = board.swimlanes
    assert default_lane.id == 200
    assert default_lane.name == "Default"
    assert default_lane.position == 1
    assert expedite_lane.name == "Expedite"

    # ``card_template`` is exposed verbatim as a dict — the API's per-board
    # config of which card fields are shown.
    assert board.card_template is not None
    assert board.card_template["description"] == {"enabled": True, "position": 1}
    assert board.card_template["custom_field_1"]["label"] == "Story Points"
    assert board.card_template["custom_field_1"]["type"] == "number"


async def test_get_board_minimal_payload_defaults_collections(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_board_url(7)).mock(
            return_value=httpx.Response(200, json={"id": 7, "name": "Tiny"})
        )
        board = await get_board(7)

    assert board.id == 7
    assert board.name == "Tiny"
    assert board.columns == []
    assert board.swimlanes == []
    assert board.card_template is None


async def test_get_board_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(_board_url(999)).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await get_board(999)
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.body_excerpt


def test_column_serializes_type_alias_not_underscore() -> None:
    """A JSON dump of a Column uses key ``type``, not ``type_``."""
    column = Column.model_validate(
        {"id": 1, "name": "Backlog", "type": "queue"},
    )
    dumped = column.model_dump()
    assert "type" in dumped
    assert "type_" not in dumped
    assert dumped["type"] == "queue"
    # JSON-mode dump must agree (this is what FastMCP renders to the client).
    json_dumped = column.model_dump(mode="json")
    assert "type" in json_dumped
    assert "type_" not in json_dumped


async def test_get_board_rejects_zero_board_id_before_http() -> None:
    """``board_id=0`` raises a pydantic ValidationError before any HTTP call."""
    with respx.mock(assert_all_called=False) as router:
        # Register a route that would explode the test if it were ever hit.
        route = router.get(_board_url(0)).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(ValidationError):
            await get_board(0)
        assert not route.called


async def test_get_board_rejects_negative_board_id_before_http() -> None:
    """Negative ``board_id`` is also rejected before any HTTP call."""
    with respx.mock(assert_all_called=False) as router:
        route = router.get(_board_url(-5)).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(ValidationError):
            await get_board(-5)
        assert not route.called
