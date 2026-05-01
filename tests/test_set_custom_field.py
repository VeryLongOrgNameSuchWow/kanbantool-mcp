"""Tests for the ``set_custom_field`` MCP tool.

This tool is the *write* counterpart to ``list_custom_field_definitions`` —
it targets one of the 15 ``custom_field_N`` slots on a task and either sets
its value or clears it. Two behaviours warrant the dedicated tool (rather
than routing through ``update_task``):

- ``None`` means **clear** here, not **omit**: the request must put a literal
  ``null`` on the wire so the API drops the stored value. ``update_task``'s
  shared ``_patch_task`` helper has None-skip semantics, so this tool builds
  its body inline.
- Slot range is hard-bounded to ``1..15`` client-side; ``validate_call``
  accepts any int otherwise.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.exceptions import KanbanToolValidationError
from kanbantool_mcp.server import set_custom_field

from .conftest import BASE_URL

TASK_ID = 42
TASK_URL = f"{BASE_URL}tasks/{TASK_ID}.json"


def _request_body(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls.last.request.content)


async def test_set_custom_field_writes_string_value(_inject_client: KanbanToolClient) -> None:
    """A simple string write lands as ``{"task": {"custom_field_3": "hello"}}``
    on the wire, hits PUT ``/tasks/{id}.json``, and round-trips into a
    ``Task`` carrying the same value on ``custom_fields``."""
    response_payload = {
        "id": TASK_ID,
        "name": "with custom",
        "board_id": 7,
        "custom_field_3": "hello",
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.put(TASK_URL).mock(return_value=httpx.Response(200, json=response_payload))
        task = await set_custom_field(task_id=TASK_ID, slot=3, value="hello")

    request = route.calls.last.request
    assert request.method == "PUT"
    assert str(request.url) == TASK_URL
    assert _request_body(route) == {"task": {"custom_field_3": "hello"}}

    assert task.id == TASK_ID
    assert task.custom_fields == {"custom_field_3": "hello"}


async def test_set_custom_field_none_clears_via_explicit_null(
    _inject_client: KanbanToolClient,
) -> None:
    """``value=None`` MUST land as a literal JSON ``null`` on the wire (the API
    treats null as "clear"). It must NOT be silently omitted — that's the
    ``update_task`` semantic and the bug this dedicated tool exists to avoid."""
    response_payload = {
        "id": TASK_ID,
        "name": "cleared",
        "board_id": 7,
        "custom_field_1": None,
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.put(TASK_URL).mock(return_value=httpx.Response(200, json=response_payload))
        task = await set_custom_field(task_id=TASK_ID, slot=1, value=None)

    body = _request_body(route)
    assert body == {"task": {"custom_field_1": None}}

    # The raw request bytes must contain literal ``"custom_field_1":null`` —
    # defensive against any future serializer change that decides ``None``
    # should drop the key. Substring is tighter than ``"null" in raw`` (which
    # would silently match a payload like ``{"...": "nullable"}``). Note no
    # space between the colon and ``null``: httpx's default JSON encoder uses
    # the compact (separators=(",", ":")) form. If a future tweak switches to
    # indented JSON this assertion will need ``": null"``.
    raw = route.calls.last.request.content.decode()
    assert '"custom_field_1":null' in raw

    # The response shows the cleared value carried back through
    # ``Task.custom_fields``.
    assert task.custom_fields == {"custom_field_1": None}


@pytest.mark.parametrize("bad_slot", [0, -1, 16, 100])
async def test_set_custom_field_rejects_out_of_range_slot(
    monkeypatch: pytest.MonkeyPatch,
    _inject_client: KanbanToolClient,
    bad_slot: int,
) -> None:
    """Slots outside 1..15 must reject *before* any HTTP call — the API has
    exactly 15 fixed slots, and round-tripping a bogus number just to get a
    422 wastes quota. ``validate_call`` enforces ``Field(ge=1, le=15)`` so the
    raised error is a ``pydantic.ValidationError`` (a ``ValueError`` subclass)."""
    sentinel = AsyncMock(
        side_effect=AssertionError("set_custom_field issued an HTTP call for an out-of-range slot")
    )
    monkeypatch.setattr(_inject_client, "request", sentinel)

    with pytest.raises(ValueError) as exc_info:
        await set_custom_field(task_id=TASK_ID, slot=bad_slot, value="anything")
    assert "slot" in str(exc_info.value)
    sentinel.assert_not_awaited()


@pytest.mark.parametrize("bad_value", [{"key": "v"}, ["a", "b"], object()])
async def test_set_custom_field_rejects_unsupported_value_type(
    monkeypatch: pytest.MonkeyPatch,
    _inject_client: KanbanToolClient,
    bad_value: Any,
) -> None:
    """The narrowed ``value`` type (``str | int | float | bool | None``)
    rejects dicts, lists, and arbitrary objects at the ``validate_call``
    boundary — keeping the typed-error contract instead of letting an
    untyped ``TypeError`` escape from ``json.dumps`` deep inside httpx.

    ``bad_value`` is typed ``Any`` so the type checker does not flag the
    intentionally-mistyped call below; the runtime is what we are exercising."""
    sentinel = AsyncMock(
        side_effect=AssertionError("set_custom_field accepted an unsupported value type")
    )
    monkeypatch.setattr(_inject_client, "request", sentinel)

    with pytest.raises(ValueError):
        await set_custom_field(task_id=TASK_ID, slot=1, value=bad_value)
    sentinel.assert_not_awaited()


async def test_set_custom_field_422_raises_validation_error(
    _inject_client: KanbanToolClient,
) -> None:
    """A 422 from the API (e.g. wrong type for the slot — writing a string
    into a numeric field) propagates as the typed ``KanbanToolValidationError``
    subclass with parsed ``field_errors``."""
    error_body = {"errors": {"custom_field_2": ["is not a number"]}}
    with respx.mock() as router:
        router.put(TASK_URL).mock(return_value=httpx.Response(422, json=error_body))
        with pytest.raises(KanbanToolValidationError) as exc_info:
            await set_custom_field(task_id=TASK_ID, slot=2, value="not-a-number")

    err = exc_info.value
    assert err.status_code == 422
    assert err.field_errors == {"custom_field_2": ["is not a number"]}


async def test_set_custom_field_accepts_non_string_values(
    _inject_client: KanbanToolClient,
) -> None:
    """The signature is ``Any | None``; ints, floats, and bools must all be
    forwarded verbatim into the wire body without coercion."""
    response_payload = {
        "id": TASK_ID,
        "name": "n",
        "board_id": 7,
        "custom_field_5": 42,
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.put(TASK_URL).mock(return_value=httpx.Response(200, json=response_payload))
        await set_custom_field(task_id=TASK_ID, slot=5, value=42)

    assert _request_body(route) == {"task": {"custom_field_5": 42}}
