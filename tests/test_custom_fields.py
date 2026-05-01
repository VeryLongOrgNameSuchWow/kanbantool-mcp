"""Tests for the custom-field surface on Task + the
``list_custom_field_definitions`` discovery tool."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolHTTPError
from kanbantool_mcp.models import CustomFieldDefinition, Task
from kanbantool_mcp.server import list_custom_field_definitions

from .conftest import BASE_URL

BOARD_ID = 4711
BOARD_URL = f"{BASE_URL}boards/{BOARD_ID}.json"


def _board_payload(card_template: dict[str, Any]) -> dict[str, Any]:
    """Minimal ``GET /boards/{id}.json`` shape with a tunable card_template."""
    return {
        "id": BOARD_ID,
        "name": "Engineering",
        "card_template": card_template,
    }


def _custom_field_template(
    slot: int,
    *,
    label: str | None = None,
    type_: str = "text",
    enabled: bool = False,
    options: str = "",
    position: int | None = None,
) -> dict[str, Any]:
    """Minimal card_template entry shape — the wire form lives in
    ``Board.card_template`` and the tool reshapes it into
    ``CustomFieldDefinition``."""
    return {
        "label": label or f"Custom field #{slot}",
        "type": type_,
        "enabled": enabled,
        "options": options,
        "position": position if position is not None else 12 + slot,
        "width": "25",
    }


# ---------------------------------------------------------------------------
# Task.custom_fields collection (model_validator)
# ---------------------------------------------------------------------------


def test_task_collects_numbered_custom_fields_into_dict() -> None:
    """The wire payload spreads ``custom_field_1..15`` as 15 top-level keys.
    The before-validator lifts them into ``Task.custom_fields`` so callers
    don't have to enumerate the slots."""
    task = Task.model_validate(
        {
            "id": 1,
            "name": "with customs",
            "custom_field_1": "Acme Corp",
            "custom_field_2": None,
            "custom_field_3": 42,
            "custom_field_15": "2026-05-15",
        }
    )
    assert task.custom_fields == {
        "custom_field_1": "Acme Corp",
        "custom_field_2": None,
        "custom_field_3": 42,
        "custom_field_15": "2026-05-15",
    }


def test_task_custom_fields_default_empty_dict() -> None:
    """A task payload with NO ``custom_field_*`` keys (e.g. compact
    ``search_tasks`` shape) yields an empty dict — never ``None``."""
    task = Task.model_validate({"id": 2, "name": "compact"})
    assert task.custom_fields == {}


def test_task_custom_fields_round_trip_via_model_dump() -> None:
    """``model_dump`` round-trip: a Task built from already-collapsed
    ``custom_fields`` dict shouldn't re-process the lift validator (it
    short-circuits when ``custom_fields`` is already present)."""
    task = Task.model_validate(
        {
            "id": 1,
            "name": "round-trip",
            "custom_fields": {"custom_field_1": "preserved"},
        }
    )
    assert task.custom_fields == {"custom_field_1": "preserved"}


def test_task_extra_unknown_fields_still_dropped() -> None:
    """The ``extra="ignore"`` policy stays in force for non-custom-field
    keys — only the numbered slots are pulled into the dict."""
    task = Task.model_validate(
        {
            "id": 1,
            "name": "n",
            "custom_field_1": "kept",
            "speculative_future_field": "dropped",
            "another_unknown": [1, 2, 3],
        }
    )
    assert task.custom_fields == {"custom_field_1": "kept"}
    dump = task.model_dump()
    assert "speculative_future_field" not in dump
    assert "another_unknown" not in dump


# ---------------------------------------------------------------------------
# list_custom_field_definitions
# ---------------------------------------------------------------------------


async def test_list_custom_field_definitions_extracts_slots(
    _inject_client: KanbanToolClient,
) -> None:
    """The tool walks ``Board.card_template`` and emits a
    ``CustomFieldDefinition`` for each ``custom_field_N`` slot found."""
    template = {
        "description": {"enabled": True, "position": 1},  # NOT a custom field
        "priority": {"enabled": True, "position": 2},  # NOT a custom field
        "custom_field_1": _custom_field_template(1, label="Customer", enabled=True),
        "custom_field_2": _custom_field_template(2, label="ETA", type_="date"),
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(BOARD_URL).mock(return_value=httpx.Response(200, json=_board_payload(template)))
        result = await list_custom_field_definitions(BOARD_ID)

    assert len(result) == 2
    assert all(isinstance(d, CustomFieldDefinition) for d in result)
    first, second = result
    assert first.slot == 1
    assert first.label == "Customer"
    assert first.enabled is True
    assert first.type_ == "text"
    assert second.slot == 2
    assert second.label == "ETA"
    assert second.type_ == "date"


async def test_list_custom_field_definitions_returns_in_slot_order(
    _inject_client: KanbanToolClient,
) -> None:
    """Definitions come back sorted by slot regardless of dict iteration
    order on the wire."""
    template = {
        "custom_field_15": _custom_field_template(15),
        "custom_field_2": _custom_field_template(2),
        "custom_field_10": _custom_field_template(10),
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(BOARD_URL).mock(return_value=httpx.Response(200, json=_board_payload(template)))
        result = await list_custom_field_definitions(BOARD_ID)

    assert [d.slot for d in result] == [2, 10, 15]


async def test_list_custom_field_definitions_empty_when_no_custom_fields(
    _inject_client: KanbanToolClient,
) -> None:
    """A board whose card_template has no ``custom_field_*`` entries (or
    is itself absent) yields an empty list rather than failing."""
    template_no_customs = {
        "description": {"enabled": True, "position": 1},
        "priority": {"enabled": True, "position": 2},
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(BOARD_URL).mock(
            return_value=httpx.Response(200, json=_board_payload(template_no_customs))
        )
        result = await list_custom_field_definitions(BOARD_ID)

    assert result == []


async def test_list_custom_field_definitions_handles_missing_card_template(
    _inject_client: KanbanToolClient,
) -> None:
    """Compact list-style ``Board`` payloads omit ``card_template``
    entirely. The tool tolerates that — empty list."""
    with respx.mock(assert_all_called=True) as router:
        router.get(BOARD_URL).mock(
            return_value=httpx.Response(200, json={"id": BOARD_ID, "name": "compact"})
        )
        result = await list_custom_field_definitions(BOARD_ID)

    assert result == []


async def test_list_custom_field_definitions_skips_non_dict_entries(
    _inject_client: KanbanToolClient,
) -> None:
    """Defensive against an unexpected card_template entry shape — if a
    custom_field slot is somehow not a dict, skip it silently rather than
    fail the whole call."""
    template = {
        "custom_field_1": _custom_field_template(1, label="ok"),
        "custom_field_2": "not-a-dict-somehow",
        "custom_field_3": _custom_field_template(3, label="also-ok"),
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(BOARD_URL).mock(return_value=httpx.Response(200, json=_board_payload(template)))
        result = await list_custom_field_definitions(BOARD_ID)

    assert [d.slot for d in result] == [1, 3]


async def test_list_custom_field_definitions_rejects_non_positive_board_id(
    _inject_client: KanbanToolClient,
) -> None:
    """``validate_call`` enforces ``ge=1`` so we don't waste an API round
    trip on a clearly-invalid id."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await list_custom_field_definitions(0)


async def test_list_custom_field_definitions_404_propagates(
    _inject_client: KanbanToolClient,
) -> None:
    """An unknown board id produces a typed ``KanbanToolHTTPError(404)``
    via ``get_board``'s existing error path."""
    with respx.mock() as router:
        router.get(BOARD_URL).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await list_custom_field_definitions(BOARD_ID)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# CustomFieldDefinition model — alias serialisation
# ---------------------------------------------------------------------------


def test_custom_field_definition_type_alias_serialises_as_type() -> None:
    """``type_`` is a Python attribute (avoiding the builtin shadow); the
    serialisation alias keeps the wire / MCP-schema name as ``type``."""
    defn = CustomFieldDefinition.model_validate(
        {"slot": 1, "label": "Customer", "type": "text", "enabled": True}
    )
    assert defn.type_ == "text"
    # Default model_dump preserves the original Python attribute name.
    by_attr = defn.model_dump()
    assert "type_" in by_attr
    # Aliased dump uses the wire name, matching what the API expects on writes.
    by_alias = defn.model_dump(by_alias=True)
    assert "type" in by_alias
