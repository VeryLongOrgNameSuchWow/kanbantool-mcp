"""Tests for the user-discovery tools: whoami, get_user, list_board_collaborators."""

from __future__ import annotations

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolHTTPError, KanbanToolPermissionError
from kanbantool_mcp.models import Collaborator, User
from kanbantool_mcp.server import get_user, list_board_collaborators, whoami

from .conftest import BASE_URL


def _user_url(user_id: int | str) -> str:
    return f"{BASE_URL}users/{user_id}.json"


def _board_url(board_id: int) -> str:
    return f"{BASE_URL}boards/{board_id}.json"


# ---------- whoami ----------


async def test_whoami_returns_typed_user(_inject_client: KanbanToolClient) -> None:
    payload = {
        "id": 1383923,
        "name": "riohno",
        "initials": "R",
        "is_account_admin": True,
        "is_account_owner": False,
        "is_project_manager": True,
        "is_suspended": False,
        "last_activity_on": "2026-05-01",
        "last_login_at": "2026-05-01T08:00:00+00:00",
        "created_at": "2026-04-30T17:00:00+00:00",
        "timezone": "Europe/Warsaw",
        "locale": "en",
        # Heavy nested fields the API surfaces but the model drops via
        # ``extra="ignore"`` — keep one in the fixture so the test locks it.
        "account": {"id": 99, "name": "VeryLong"},
        "settings": {"theme": "dark"},
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(_user_url("current")).mock(return_value=httpx.Response(200, json=payload))
        result = await whoami()

    assert isinstance(result, User)
    assert result.id == 1383923
    assert result.name == "riohno"
    assert result.is_account_admin is True
    assert result.is_account_owner is False
    assert result.timezone == "Europe/Warsaw"
    # ``extra="ignore"`` drops the unknown nested fields.
    dump = result.model_dump()
    assert "account" not in dump
    assert "settings" not in dump


async def test_whoami_401_raises_permission_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(_user_url("current")).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await whoami()


async def test_whoami_malformed_payload_raises_http_error(
    _inject_client: KanbanToolClient,
) -> None:
    """A 200 with a payload missing the required ``id`` field surfaces as
    ``KanbanToolHTTPError(status_code=200)`` with a ``malformed``-tagged
    excerpt — never a raw ``pydantic.ValidationError``."""
    with respx.mock(assert_all_called=True) as router:
        router.get(_user_url("current")).mock(
            return_value=httpx.Response(200, json={"name": "no-id"})
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await whoami()

    assert exc_info.value.status_code == 200
    assert "malformed" in exc_info.value.body_excerpt


# ---------- get_user ----------


async def test_get_user_returns_typed_user(_inject_client: KanbanToolClient) -> None:
    payload = {
        "id": 1383921,
        "name": "Magdalena Bartczak",
        "initials": "MB",
        "is_account_admin": True,
        "is_account_owner": True,
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(_user_url(1383921)).mock(return_value=httpx.Response(200, json=payload))
        result = await get_user(1383921)

    assert isinstance(result, User)
    assert result.id == 1383921
    assert result.name == "Magdalena Bartczak"
    assert result.is_account_owner is True


async def test_get_user_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(_user_url(999)).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await get_user(999)
        assert exc_info.value.status_code == 404


async def test_get_user_rejects_non_positive_id(_inject_client: KanbanToolClient) -> None:
    """``validate_call`` enforces ``ge=1`` so we don't hit the API with a
    bogus 0/-N that would produce a confusing 404."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await get_user(0)


# ---------- list_board_collaborators ----------


async def test_list_board_collaborators_returns_typed_list(
    _inject_client: KanbanToolClient,
) -> None:
    payload = {
        "id": 42,
        "name": "Engineering",
        "workflow_stages": [],
        "swimlanes": [],
        "collaborators": [
            {"id": 1, "name": "Alice", "initials": "A", "active": True},
            {"id": 2, "name": "Bob", "initials": "B", "active": False},
        ],
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(_board_url(42)).mock(return_value=httpx.Response(200, json=payload))
        result = await list_board_collaborators(42)

    assert len(result) == 2
    assert all(isinstance(c, Collaborator) for c in result)
    assert result[0].id == 1
    assert result[0].name == "Alice"
    assert result[0].active is True
    assert result[1].active is False


async def test_list_board_collaborators_empty_when_absent(
    _inject_client: KanbanToolClient,
) -> None:
    """Boards may legitimately omit ``collaborators`` (compact list-style
    payloads). Default to ``[]`` rather than failing validation."""
    payload = {"id": 42, "name": "Engineering"}
    with respx.mock(assert_all_called=True) as router:
        router.get(_board_url(42)).mock(return_value=httpx.Response(200, json=payload))
        result = await list_board_collaborators(42)

    assert result == []


async def test_list_board_collaborators_404_propagates(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.get(_board_url(999)).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError):
            await list_board_collaborators(999)


# ---------- Board.collaborators round-trip ----------


def test_board_collaborators_field_round_trip() -> None:
    """The ``Board`` model exposes a typed ``collaborators`` list; verify
    the round-trip preserves shape end-to-end."""
    from kanbantool_mcp.models import Board

    board = Board.model_validate(
        {
            "id": 1,
            "name": "B",
            "collaborators": [
                {"id": 7, "name": "Eve", "initials": "E", "active": True},
            ],
        }
    )
    assert len(board.collaborators) == 1
    assert isinstance(board.collaborators[0], Collaborator)
    assert board.collaborators[0].id == 7
    assert board.collaborators[0].name == "Eve"


def test_board_collaborators_default_empty() -> None:
    """When the API omits ``collaborators`` (e.g. compact list-style
    payloads), the field defaults to ``[]``."""
    from kanbantool_mcp.models import Board

    board = Board.model_validate({"id": 1, "name": "B"})
    assert board.collaborators == []
