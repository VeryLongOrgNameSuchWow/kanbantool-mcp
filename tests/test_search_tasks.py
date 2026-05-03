"""Tests for the search_tasks MCP tool."""

from __future__ import annotations

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolHTTPError, KanbanToolPermissionError
from kanbantool_mcp.models import SearchResults
from kanbantool_mcp.server import search_tasks

from .conftest import BASE_URL

SEARCH_URL = f"{BASE_URL}tasks/search.json"

# Tests that don't care about returned tasks just need a well-formed empty
# envelope — the tool always paginates so the response is always wrapped.
_EMPTY_RESPONSE = {
    "results": [],
    "pagination": {"results_count": 0, "page": 1, "pages_count": 0},
}


async def test_search_tasks_happy_path(_inject_client: KanbanToolClient) -> None:
    payload = {
        "results": [
            {
                "id": 1,
                "name": "Ship release",
                "board_id": 7,
                "workflow_stage_id": 100,
                "priority": "high",
                "tags": "release,urgent",
                "subtasks_count": 2,
                "comments_count": 3,
                "extra_unknown_field": "ignored",
            },
            {
                "id": 2,
                "name": "Write changelog",
                "board_id": 7,
                "priority": 1,
            },
        ],
        "pagination": {"results_count": 2, "page": 1, "pages_count": 1},
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
        response = await search_tasks(query="@alice priority:high")

    assert isinstance(response, SearchResults)
    assert response.total_count == 2
    assert response.page == 1
    assert response.has_more is False
    assert len(response.results) == 2
    first, second = response.results
    assert first.id == 1
    assert first.name == "Ship release"
    assert first.board_id == 7
    assert first.lane_id == 100
    assert first.priority == "high"
    assert first.tags == "release,urgent"
    assert first.subtasks_count == 2
    assert first.comments_count == 3
    assert second.id == 2
    assert second.name == "Write changelog"
    assert second.priority == 1


async def test_search_tasks_dsl_passthrough_unmodified(
    _inject_client: KanbanToolClient,
) -> None:
    """The full DSL string must reach the API verbatim — no rebuilding,
    no extra quoting, no operator splitting."""
    raw_query = '@alice priority:high tags:"bug,urgent"'
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=_EMPTY_RESPONSE))
        await search_tasks(query=raw_query)

    request = route.calls.last.request
    assert request.url.params["query"] == raw_query


async def test_search_tasks_omits_board_id_when_none(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=_EMPTY_RESPONSE))
        await search_tasks(query="name:foo")

    request = route.calls.last.request
    assert "board_id" not in request.url.params


async def test_search_tasks_forwards_board_id_when_provided(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=_EMPTY_RESPONSE))
        await search_tasks(query="name:foo", board_id=42)

    request = route.calls.last.request
    assert request.url.params["board_id"] == "42"


async def test_search_tasks_empty_results(_inject_client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=_EMPTY_RESPONSE))
        response = await search_tasks(query="name:nothing-matches")

    assert response.results == []
    assert response.total_count == 0
    assert response.has_more is False


async def test_search_tasks_pagination_forwarded(_inject_client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=_EMPTY_RESPONSE))
        await search_tasks(query="name:foo", limit=10, page=2)

    params = route.calls.last.request.url.params
    assert params["limit"] == "10"
    assert params["page"] == "2"


async def test_search_tasks_limit_is_clamped_to_50(_inject_client: KanbanToolClient) -> None:
    """Hallucinated huge limits must be capped before they hit the API."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=_EMPTY_RESPONSE))
        await search_tasks(query="name:foo", limit=200)

    assert route.calls.last.request.url.params["limit"] == "50"


async def test_search_tasks_401_raises_permission_error(
    _inject_client: KanbanToolClient,
) -> None:
    with respx.mock() as router:
        router.get(SEARCH_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await search_tasks(query="name:foo")


async def test_search_tasks_500_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(SEARCH_URL).mock(return_value=httpx.Response(500, text="boom"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await search_tasks(query="name:foo")
        assert exc_info.value.status_code == 500
        assert "boom" in exc_info.value.body_excerpt


async def test_search_tasks_malformed_result_raises_http_error(
    _inject_client: KanbanToolClient,
) -> None:
    """A 200 with a result entry missing the required ``id`` field surfaces
    as ``KanbanToolHTTPError(status_code=200)`` with a ``malformed``-tagged
    excerpt — never a raw ``pydantic.ValidationError``."""
    payload = {
        "results": [
            {"id": 1, "name": "ok"},
            {"name": "no-id"},  # missing id
        ],
        "pagination": {"results_count": 2, "page": 1, "pages_count": 1},
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await search_tasks(query="name:foo")

    assert exc_info.value.status_code == 200
    assert "malformed" in exc_info.value.body_excerpt


async def test_search_tasks_drops_unknown_task_fields(
    _inject_client: KanbanToolClient,
) -> None:
    """Confirms ``extra="ignore"`` round-trip — unknown task fields are
    silently dropped rather than failing validation."""
    payload = {
        "results": [
            {
                "id": 99,
                "name": "Round trip",
                "speculative_future_field": {"nested": True},
                "another_unknown": [1, 2, 3],
            }
        ],
        "pagination": {"results_count": 1, "page": 1, "pages_count": 1},
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
        response = await search_tasks(query="name:round-trip")

    assert len(response.results) == 1
    assert response.results[0].id == 99
    assert response.results[0].name == "Round trip"
    assert not hasattr(response.results[0], "speculative_future_field")


async def test_search_tasks_has_more_true_when_more_pages_exist(
    _inject_client: KanbanToolClient,
) -> None:
    """When the API reports ``page < pages_count``, ``has_more`` is True
    so callers know to bump ``page`` and call again."""
    payload = {
        "results": [{"id": i, "name": f"task-{i}"} for i in range(25)],
        "pagination": {"results_count": 73, "page": 1, "pages_count": 3},
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
        response = await search_tasks(query="name:task")

    assert response.total_count == 73
    assert response.page == 1
    assert response.has_more is True
    assert len(response.results) == 25


async def test_search_tasks_has_more_false_on_last_page(
    _inject_client: KanbanToolClient,
) -> None:
    """``has_more=False`` even when ``len(results) == limit`` if the API
    says we're on the last page — guards against the off-by-one bug where
    a full final page would otherwise look like 'more exist'."""
    payload = {
        # A full page worth of results...
        "results": [{"id": i, "name": f"task-{i}"} for i in range(25)],
        # ...but the pagination envelope confirms it's the last one.
        "pagination": {"results_count": 75, "page": 3, "pages_count": 3},
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
        response = await search_tasks(query="name:task", page=3)

    assert response.has_more is False
    assert response.page == 3


async def test_search_tasks_missing_pagination_envelope_is_safe(
    _inject_client: KanbanToolClient,
) -> None:
    """If the API ever omits the ``pagination`` envelope (legacy responses
    without ``limit``/``page``), the wrapper still parses cleanly with
    conservative defaults: ``total_count=None``, ``has_more=False``."""
    payload = {
        "results": [{"id": 1, "name": "ok"}],
        # No 'pagination' key.
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
        response = await search_tasks(query="name:ok")

    assert len(response.results) == 1
    assert response.total_count is None
    assert response.has_more is False
    # ``page`` falls back to the request's page (1).
    assert response.page == 1
