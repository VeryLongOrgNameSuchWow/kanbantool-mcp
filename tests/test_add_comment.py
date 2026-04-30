"""Tests for the add_comment MCP tool."""

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
from kanbantool_mcp.server import add_comment

from .conftest import BASE_URL

TASK_ID = 4242
COMMENTS_URL = f"{BASE_URL}tasks/{TASK_ID}/comments.json"


def _request_body(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls.last.request.content)


async def test_add_comment_happy_path_full_payload(_inject_client: KanbanToolClient) -> None:
    """A full server response round-trips into a Comment with id, text,
    author, and timestamps populated, and the request body uses the
    ``{"comment": {"text": ...}}`` Rails-style envelope."""
    response_payload = {
        "id": 91,
        "text": "Looks good to me.",
        "user_id": 17,
        "created_at": "2026-04-30T12:00:00Z",
        "updated_at": "2026-04-30T12:00:00Z",
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json=response_payload),
        )
        comment = await add_comment(task_id=TASK_ID, text="Looks good to me.")

    assert comment.id == 91
    assert comment.text == "Looks good to me."
    assert comment.user_id == 17
    assert comment.created_at == "2026-04-30T12:00:00Z"
    assert comment.updated_at == "2026-04-30T12:00:00Z"

    body = _request_body(route)
    assert body == {"comment": {"text": "Looks good to me."}}


async def test_add_comment_minimal_response_payload(_inject_client: KanbanToolClient) -> None:
    """A minimal server response (only ``id``) still produces a valid Comment;
    optional fields fall back to ``None``."""
    with respx.mock(assert_all_called=True) as router:
        router.post(COMMENTS_URL).mock(return_value=httpx.Response(201, json={"id": 5}))
        comment = await add_comment(task_id=TASK_ID, text="hi")

    assert comment.id == 5
    assert comment.text is None
    assert comment.user_id is None
    assert comment.created_at is None
    assert comment.updated_at is None


async def test_add_comment_url_shape(_inject_client: KanbanToolClient) -> None:
    """POST hits ``tasks/{id}/comments.json`` exactly — no double ``.json``,
    no trailing slash, no query string."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"id": 1}),
        )
        await add_comment(task_id=TASK_ID, text="hi")

    request = route.calls.last.request
    assert request.method == "POST"
    assert str(request.url) == COMMENTS_URL


async def test_add_comment_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.post(COMMENTS_URL).mock(
            return_value=httpx.Response(401, text="unauthorized"),
        )
        with pytest.raises(KanbanToolPermissionError):
            await add_comment(task_id=TASK_ID, text="hi")


async def test_add_comment_422_raises_validation_error(
    _inject_client: KanbanToolClient,
) -> None:
    """A 422 must surface as the typed ``KanbanToolValidationError`` subclass
    (not just the bare ``KanbanToolHTTPError``) and carry parsed
    ``field_errors`` so callers can branch on field-level detail."""
    error_body = {"errors": {"text": ["can't be blank"]}}
    with respx.mock() as router:
        router.post(COMMENTS_URL).mock(
            return_value=httpx.Response(422, json=error_body),
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await add_comment(task_id=TASK_ID, text="")

    err = exc_info.value
    assert isinstance(err, KanbanToolValidationError)
    assert err.status_code == 422
    assert err.field_errors == {"text": ["can't be blank"]}


async def test_add_comment_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.post(COMMENTS_URL).mock(return_value=httpx.Response(404, text="task not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await add_comment(task_id=TASK_ID, text="hi")
    assert exc_info.value.status_code == 404
    assert not isinstance(exc_info.value, KanbanToolValidationError)


async def test_add_comment_500_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.post(COMMENTS_URL).mock(return_value=httpx.Response(500, text="server exploded"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await add_comment(task_id=TASK_ID, text="hi")
    assert exc_info.value.status_code == 500
