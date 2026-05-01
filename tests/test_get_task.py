"""Tests for the get_task MCP tool."""

from __future__ import annotations

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolHTTPError, KanbanToolPermissionError
from kanbantool_mcp.models import Task
from kanbantool_mcp.server import get_task

from .conftest import BASE_URL


def _task_url(task_id: int) -> str:
    return f"{BASE_URL}tasks/{task_id}.json"


async def test_get_task_happy_path(_inject_client: KanbanToolClient) -> None:
    payload = {
        "id": 4242,
        "name": "Ship the thing",
        "description": "Cut a release once CI is green.",
        "board_id": 7,
        "workflow_stage_id": 100,
        "swimlane_id": 200,
        "position": 3,
        "priority": "high",
        "color": "#ff0000",
        "due_date": "2026-05-15T00:00:00Z",
        "start_date": "2026-04-30T00:00:00Z",
        "tags": "release,urgent",
        "assigned_user_id": 11,
        "archived_at": None,
        "block_reason": "Waiting on review",
        "subtasks_count": 4,
        "comments_count": 12,
        "timers_total": 3600,
        "created_at": "2026-04-01T09:30:00Z",
        "updated_at": "2026-04-29T17:45:00Z",
        "extra_unknown_field": "ignored",
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(_task_url(4242)).mock(return_value=httpx.Response(200, json=payload))
        task = await get_task(4242)

    assert task.id == 4242
    assert task.name == "Ship the thing"
    assert task.description == "Cut a release once CI is green."
    assert task.board_id == 7
    assert task.lane_id == 100
    assert task.swimlane_id == 200
    assert task.position == 3
    assert task.priority == "high"
    assert task.color == "#ff0000"
    assert task.due_date == "2026-05-15T00:00:00Z"
    assert task.start_date == "2026-04-30T00:00:00Z"
    assert task.tags == "release,urgent"
    assert task.assigned_user_id == 11
    assert task.archived_at is None
    assert task.is_archived is False
    assert task.is_blocked is True
    assert task.block_reason == "Waiting on review"
    assert task.subtasks_count == 4
    assert task.comments_count == 12
    assert task.timers_total == 3600
    assert task.created_at == "2026-04-01T09:30:00Z"
    assert task.updated_at == "2026-04-29T17:45:00Z"


async def test_get_task_accepts_integer_priority(_inject_client: KanbanToolClient) -> None:
    """Some accounts return ``priority`` as an int rather than an enum string."""
    with respx.mock(assert_all_called=True) as router:
        router.get(_task_url(7)).mock(
            return_value=httpx.Response(200, json={"id": 7, "name": "x", "priority": 2})
        )
        task = await get_task(7)

    assert task.priority == 2


async def test_get_task_minimal_payload(_inject_client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_task_url(1)).mock(
            return_value=httpx.Response(200, json={"id": 1, "name": "Tiny"})
        )
        task = await get_task(1)

    assert task.id == 1
    assert task.name == "Tiny"
    assert task.description is None
    assert task.board_id is None
    assert task.lane_id is None
    assert task.swimlane_id is None
    assert task.assigned_user_id is None
    assert task.subtasks_count is None
    assert task.comments_count is None
    assert task.timers_total is None
    # Computed flags default to False when their backing fields are unset.
    assert task.is_archived is False
    assert task.is_blocked is False


async def test_get_task_404_raises_http_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(_task_url(999)).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await get_task(999)
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.body_excerpt


async def test_get_task_401_raises_permission_error(_inject_client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(_task_url(1)).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(KanbanToolPermissionError):
            await get_task(1)


async def test_get_task_url_shape(_inject_client: KanbanToolClient) -> None:
    """Verify the tool hits exactly ``GET tasks/{id}.json`` (no query string,
    no double-suffix, correct base URL)."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(_task_url(123)).mock(
            return_value=httpx.Response(200, json={"id": 123, "name": "Probe"})
        )
        await get_task(123)

    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == "GET"
    assert str(request.url) == _task_url(123)


def test_task_is_archived_property_tracks_archived_at() -> None:
    """``is_archived`` is True iff ``archived_at`` is set; absence is False."""
    unarchived = Task.model_validate({"id": 1, "name": "live"})
    assert unarchived.is_archived is False

    explicit_null = Task.model_validate({"id": 2, "name": "live", "archived_at": None})
    assert explicit_null.is_archived is False

    archived = Task.model_validate({"id": 3, "name": "old", "archived_at": "2026-04-30T00:00:00Z"})
    assert archived.is_archived is True


def test_task_is_blocked_property_tracks_block_reason() -> None:
    """``is_blocked`` is True iff ``block_reason`` is set; absence is False."""
    unblocked = Task.model_validate({"id": 1, "name": "free"})
    assert unblocked.is_blocked is False

    blocked = Task.model_validate({"id": 2, "name": "stuck", "block_reason": "Waiting on review"})
    assert blocked.is_blocked is True


def test_task_model_dump_includes_computed_flags() -> None:
    """``is_archived`` / ``is_blocked`` must appear in ``model_dump()`` so that
    FastMCP serialises them onto the wire. A bare ``@property`` is invisible
    to pydantic v2's serialiser; only ``@computed_field`` is picked up."""
    archived_and_blocked = Task.model_validate(
        {
            "id": 1,
            "name": "stuck-and-archived",
            "archived_at": "2026-04-30T12:00:00Z",
            "block_reason": "Waiting on review",
        }
    )
    dumped = archived_and_blocked.model_dump()
    assert "is_archived" in dumped
    assert "is_blocked" in dumped
    assert dumped["is_archived"] is True
    assert dumped["is_blocked"] is True

    live_and_unblocked = Task.model_validate({"id": 2, "name": "fresh"})
    dumped = live_and_unblocked.model_dump()
    assert "is_archived" in dumped
    assert "is_blocked" in dumped
    assert dumped["is_archived"] is False
    assert dumped["is_blocked"] is False


def test_task_model_json_schema_advertises_computed_flags() -> None:
    """The serialization JSON schema (the one FastMCP shows the LLM for the
    *output* of a tool) must list both computed flags. Computed fields are
    output-only, so they live in the serialization schema, not the default
    validation one."""
    schema = Task.model_json_schema(mode="serialization")
    assert "is_archived" in schema["properties"]
    assert "is_blocked" in schema["properties"]


# --- Additive fields surfaced from the v3 wire payload (#38 / #59) ----------
#
# These three tests lock the additive-field contract: every new field
# round-trips when present, defaults to None / [] when absent, and the
# explicitly-deferred fields (custom_field_*, changelogs) stay dropped via
# ``extra="ignore"``.


def test_task_round_trips_additive_fields() -> None:
    """Every additive field surfaced in #38 / #59 must round-trip via
    ``Task.model_validate(...).model_dump()``."""
    payload = {
        "id": 1,
        "name": "Loaded",
        # Sizing & estimation
        "size_estimate": 5,
        "size_estimate_description": "five points",
        "time_estimate": 7200,
        # Search / discoverability
        "search_tags": ["alpha", "beta"],
        # Visual markers
        "card_color": "red",
        "card_color_in_rgb": "#ff0000",
        "card_color_invert": True,
        "card_type_id": 9,
        # Schedule fields (raw dicts)
        "recurring_schedule": {"every": "week", "weekday": "mon"},
        "reminders_schedule": {"offsets": [60, 1440]},
        # Relationships
        "linked_tasks": [{"id": 11, "name": "linked-a"}],
        "linked_tasks_status": "blocked",
        "task_dependencies": [{"id": 22, "type": "blocks"}],
        "collaborators": [{"user_id": 33}, {"user_id": 44}],
        # Attachments
        "attachments": [{"id": 55, "filename": "spec.pdf"}],
        "attachments_count": 1,
        # Provenance & state
        "created_by_id": 100,
        "moved_at": "2026-04-30T09:00:00Z",
        "postponed_until": "2026-05-10T00:00:00Z",
        "subtasks_completed_count": 2,
        "external_id": "JIRA-42",
        "external_link": "https://example.test/JIRA-42",
    }
    task = Task.model_validate(payload)
    dumped = task.model_dump()

    for key, value in payload.items():
        assert dumped[key] == value, f"{key} did not round-trip"


def test_task_additive_fields_default_when_absent() -> None:
    """A minimal payload defaults every new field — ``None`` for scalars,
    empty list for collection fields."""
    task = Task.model_validate({"id": 1, "name": "Bare"})

    # Scalar defaults
    assert task.size_estimate is None
    assert task.size_estimate_description is None
    assert task.time_estimate is None
    assert task.card_color is None
    assert task.card_color_in_rgb is None
    assert task.card_color_invert is None
    assert task.card_type_id is None
    assert task.recurring_schedule is None
    assert task.reminders_schedule is None
    assert task.linked_tasks_status is None
    assert task.attachments_count is None
    assert task.created_by_id is None
    assert task.moved_at is None
    assert task.postponed_until is None
    assert task.subtasks_completed_count is None
    assert task.external_id is None
    assert task.external_link is None

    # Collection defaults
    assert task.search_tags == []
    assert task.linked_tasks == []
    assert task.task_dependencies == []
    assert task.collaborators == []
    assert task.attachments == []


def test_task_collection_fields_coerce_null_to_empty_list() -> None:
    """The Kanban Tool v3 API serialises empty collections as JSON ``null``
    for several detail-only fields (live spike confirmed for ``linked_tasks``).
    The model must coerce ``None`` → ``[]`` so callers see a consistent
    list-typed surface."""
    task = Task.model_validate(
        {
            "id": 1,
            "name": "Null collections",
            "linked_tasks": None,
            "task_dependencies": None,
            "collaborators": None,
            "attachments": None,
            "search_tags": None,
        }
    )
    assert task.linked_tasks == []
    assert task.task_dependencies == []
    assert task.collaborators == []
    assert task.attachments == []
    assert task.search_tags == []


def test_task_still_drops_deferred_fields() -> None:
    """``custom_field_*`` and ``changelogs`` are explicitly deferred — they
    must remain dropped via ``extra="ignore"`` so the additive change stays
    pure (no accidental surface for fields we haven't designed)."""
    task = Task.model_validate(
        {
            "id": 1,
            "name": "Deferred",
            "custom_field_1": "should not surface",
            "custom_field_15": "also dropped",
            "changelogs": [{"id": 999, "what": "noisy"}],
        }
    )
    dumped = task.model_dump()
    assert "custom_field_1" not in dumped
    assert "custom_field_15" not in dumped
    assert "changelogs" not in dumped
