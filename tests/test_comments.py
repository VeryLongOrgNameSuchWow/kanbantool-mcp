"""Tests for the add_comment / delete_comment MCP tools.

Regression bait: this file asserts the wire field is ``content``, not ``text``
— pre-fix prod always 422'd because the body was sent as
``{"comment": {"text": ...}}``. Both sides of the new contract live here.
"""

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
from kanbantool_mcp.models import Comment
from kanbantool_mcp.server import add_comment, delete_comment

from .conftest import BASE_URL

TASK_ID = 4242
COMMENT_ID = 91
COMMENTS_URL = f"{BASE_URL}tasks/{TASK_ID}/comments.json"
COMMENT_URL = f"{BASE_URL}tasks/{TASK_ID}/comments/{COMMENT_ID}.json"


def _request_body(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls.last.request.content)


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


async def test_add_comment_happy_path_full_payload(_inject_client: KanbanToolClient) -> None:
    """A full server response round-trips into a Comment with id, content,
    author, and timestamps populated, and the request body uses the
    ``{"comment": {"content": ...}}`` Rails-style envelope.

    Regression bait: this asserts the wire field is ``content``, not ``text``
    — pre-fix prod always 422'd."""
    response_payload = {
        "id": COMMENT_ID,
        "content": "Looks good to me.",
        "user_id": 17,
        "created_at": "2026-04-30T12:00:00Z",
        "updated_at": "2026-04-30T12:00:00Z",
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json=response_payload),
        )
        comment = await add_comment(task_id=TASK_ID, text="Looks good to me.")

    assert comment.id == COMMENT_ID
    assert comment.content == "Looks good to me."
    assert comment.user_id == 17
    assert comment.created_at == "2026-04-30T12:00:00Z"
    assert comment.updated_at == "2026-04-30T12:00:00Z"
    assert comment.deleted_at is None

    body = _request_body(route)
    # Wire-shape regression guard: the API expects ``content``, not ``text``.
    assert body == {"comment": {"content": "Looks good to me."}}


async def test_add_comment_minimal_response_payload(_inject_client: KanbanToolClient) -> None:
    """A minimal server response (only ``id`` + ``content``) still produces a
    valid Comment; optional fields fall back to ``None``. ``content`` is now
    required on the model — the live API never returns a comment without it."""
    response_payload = {"id": 5, "content": "hi"}
    with respx.mock(assert_all_called=True) as router:
        router.post(COMMENTS_URL).mock(return_value=httpx.Response(201, json=response_payload))
        comment = await add_comment(task_id=TASK_ID, text="hi")

    assert comment.id == 5
    assert comment.content == "hi"
    assert comment.user_id is None
    assert comment.created_at is None
    assert comment.updated_at is None
    assert comment.deleted_at is None


async def test_add_comment_url_shape(_inject_client: KanbanToolClient) -> None:
    """POST hits ``tasks/{id}/comments.json`` exactly — no double ``.json``,
    no trailing slash, no query string."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"id": 1, "content": "hi"}),
        )
        await add_comment(task_id=TASK_ID, text="hi")

    request = route.calls.last.request
    assert request.method == "POST"
    assert str(request.url) == COMMENTS_URL


async def test_add_comment_body_uses_content_not_text(
    _inject_client: KanbanToolClient,
) -> None:
    """Wire-shape regression guard: the body field is ``content``. Sending
    ``text`` makes the live API 422 with ``Content can't be blank`` — this
    was a P0 bug pre-M5."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"id": 1, "content": "hi"}),
        )
        await add_comment(task_id=TASK_ID, text="hi")

    body = _request_body(route)
    assert body == {"comment": {"content": "hi"}}
    # Belt-and-braces: lock out the broken-pre-fix shape so a future refactor
    # cannot silently regress.
    inner = body["comment"]
    assert isinstance(inner, dict)
    assert "text" not in inner


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
    error_body = {"errors": {"content": ["can't be blank"]}}
    with respx.mock() as router:
        router.post(COMMENTS_URL).mock(
            return_value=httpx.Response(422, json=error_body),
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await add_comment(task_id=TASK_ID, text="")

    err = exc_info.value
    assert isinstance(err, KanbanToolValidationError)
    assert err.status_code == 422
    assert err.field_errors == {"content": ["can't be blank"]}


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


async def test_add_comment_malformed_response_raises_http_error(
    _inject_client: KanbanToolClient,
) -> None:
    """A 201 with a response body missing the required ``id`` field surfaces
    as ``KanbanToolHTTPError(status_code=200)`` with a ``malformed``-tagged
    excerpt — never a raw ``pydantic.ValidationError``."""
    with respx.mock() as router:
        router.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"content": "no-id"}),
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await add_comment(task_id=TASK_ID, text="hi")

    assert exc_info.value.status_code == 200
    assert "malformed" in exc_info.value.body_excerpt


# ---------------------------------------------------------------------------
# delete_comment
# ---------------------------------------------------------------------------


async def test_delete_comment_returns_soft_deleted_comment(
    _inject_client: KanbanToolClient,
) -> None:
    """The API soft-deletes — DELETE returns 200 with the modified ``Comment``
    and ``deleted_at`` populated. Mirrors ``delete_subtask`` and locks the
    contract end-to-end through the typed model."""
    response_body = {
        "id": COMMENT_ID,
        "content": "old comment",
        "user_id": 17,
        "created_at": "2026-04-30T12:00:00Z",
        "updated_at": "2026-04-30T12:00:00Z",
        "deleted_at": "2026-05-01T15:00:00.000+02:00",
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.delete(COMMENT_URL).mock(
            return_value=httpx.Response(200, json=response_body)
        )
        result = await delete_comment(task_id=TASK_ID, comment_id=COMMENT_ID)

    assert isinstance(result, Comment)
    assert result.id == COMMENT_ID
    # ``deleted_at`` is the soft-delete signal; the typed model surfaces it
    # so callers can confirm the operation actually took (vs. a no-op).
    assert result.deleted_at == "2026-05-01T15:00:00.000+02:00"

    request = route.calls.last.request
    assert request.method == "DELETE"
    assert str(request.url) == COMMENT_URL


async def test_comment_deleted_at_default_is_none() -> None:
    """A live (non-deleted) ``Comment`` has ``deleted_at`` defaulting to
    ``None`` — it's only populated on the soft-delete path."""
    comment = Comment.model_validate({"id": 1, "content": "live"})
    assert comment.deleted_at is None


async def test_delete_comment_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.delete(COMMENT_URL).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await delete_comment(task_id=TASK_ID, comment_id=COMMENT_ID)
    assert exc_info.value.status_code == 404
    assert not isinstance(exc_info.value, KanbanToolValidationError)


async def test_delete_comment_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.delete(COMMENT_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await delete_comment(task_id=TASK_ID, comment_id=COMMENT_ID)


async def test_delete_comment_rejects_non_positive_ids(
    _inject_client: KanbanToolClient,
) -> None:
    """``validate_call`` enforces ``ge=1`` so a bogus 0/-N never reaches the
    API as a confusing 404."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await delete_comment(task_id=0, comment_id=COMMENT_ID)
    with pytest.raises(ValidationError):
        await delete_comment(task_id=TASK_ID, comment_id=0)
