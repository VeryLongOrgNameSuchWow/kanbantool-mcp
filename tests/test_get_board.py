"""Tests for the get_board MCP tool."""

from __future__ import annotations

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolHTTPError
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
                "extra_stage_field": "ignored",
            },
            {
                "id": 101,
                "name": "In Progress",
                "position": 2,
                "wip_limit": 5,
                "type": "in-progress",
            },
        ],
        "swimlanes": [
            {"id": 200, "name": "Default", "position": 1, "extra_lane_field": "ignored"},
            {"id": 201, "name": "Expedite", "position": 2},
        ],
        "card_template": [
            {
                "label": "Story Points",
                "type": "number",
                "position": 1,
                "extra_field_meta": "ignored",
            },
            {
                "label": "Owner",
                "type": "user",
                "position": 2,
            },
        ],
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
    assert in_progress.wip_limit == 5
    assert in_progress.type_ == "in-progress"

    assert len(board.swimlanes) == 2
    default_lane, expedite_lane = board.swimlanes
    assert default_lane.id == 200
    assert default_lane.name == "Default"
    assert default_lane.position == 1
    assert expedite_lane.name == "Expedite"

    assert len(board.custom_fields) == 2
    story_points, owner = board.custom_fields
    assert story_points.label == "Story Points"
    assert story_points.type_ == "number"
    assert story_points.position == 1
    assert owner.label == "Owner"
    assert owner.type_ == "user"


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
    assert board.custom_fields == []


async def test_get_board_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(_board_url(999)).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await get_board(999)
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.body_excerpt
