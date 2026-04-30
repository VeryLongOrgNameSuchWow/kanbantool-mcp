"""Tests for the list_boards MCP tool."""

from __future__ import annotations

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolHTTPError, KanbanToolPermissionError
from kanbantool_mcp.server import list_boards

from .conftest import BASE_URL

USERS_CURRENT_URL = f"{BASE_URL}users/current.json"


async def test_list_boards_happy_path(_inject_client: KanbanToolClient) -> None:
    payload = {
        "boards": [
            {
                "id": 1,
                "name": "Engineering",
                "description": "Eng work",
                "slug": "engineering",
                "use_swimlanes": True,
                "is_archived": False,
                "user_role": "admin",
                "extra_unknown_field": "ignored",
            },
            {"id": 2, "name": "Marketing"},
        ]
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(USERS_CURRENT_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await list_boards()

    assert len(result) == 2
    first, second = result
    assert first.id == 1
    assert first.name == "Engineering"
    assert first.description == "Eng work"
    assert first.slug == "engineering"
    assert first.use_swimlanes is True
    assert first.is_archived is False
    assert first.user_role == "admin"

    assert second.id == 2
    assert second.name == "Marketing"
    assert second.description is None
    assert second.slug is None
    assert second.use_swimlanes is None
    assert second.is_archived is None
    assert second.user_role is None

    # The compact /users/current payload omits detail-only collections.
    assert first.columns == []
    assert first.swimlanes == []
    assert first.custom_fields == []


async def test_list_boards_empty(_inject_client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(USERS_CURRENT_URL).mock(return_value=httpx.Response(200, json={"boards": []}))
        result = await list_boards()

    assert result == []


async def test_list_boards_missing_key(_inject_client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(USERS_CURRENT_URL).mock(
            return_value=httpx.Response(200, json={"id": 7, "email": "a@b.c"})
        )
        result = await list_boards()

    assert result == []


async def test_list_boards_propagates_permission_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(USERS_CURRENT_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await list_boards()


async def test_list_boards_propagates_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(USERS_CURRENT_URL).mock(return_value=httpx.Response(500, text="boom"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await list_boards()
        assert exc_info.value.status_code == 500
