"""Live integration tests for the M5 ``set_custom_field`` write tool.

Coverage scope: the write counterpart to ``list_custom_field_definitions``
end-to-end against the real Kanban Tool API. The interesting bits worth
locking in live (beyond the offline mock):

- A simple string write round-trips through ``Task.custom_fields``.
- ``value=None`` actually CLEARS the slot (the wire sends literal ``null``
  rather than the ``update_task`` "omit" semantic). This is the bit most
  likely to drift if a future refactor accidentally routes through
  ``_patch_task``.

Cleanup: each test creates a throwaway task, writes/clears the field,
asserts, and archives the task before returning. No orphans — the task is
gone after each test even if assertions fail.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import pytest

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.models import Task
from kanbantool_mcp.server import (
    archive_task,
    create_task,
    list_boards,
    set_custom_field,
)


@pytest.fixture
async def throwaway_task_id(_inject_live_client: KanbanToolClient) -> AsyncIterator[int]:
    """Create a single-use task on the first available board, yield its id,
    archive on teardown.

    Custom fields are per-task state; we use a fresh task per test rather
    than mutating a shared one so test ordering and account state don't
    cross-contaminate."""
    boards = await list_boards()
    if not boards:
        pytest.skip("test account has no boards; live tests need at least one.")
    board_id = boards[0].id
    task = await create_task(
        name="kanbantool-mcp live integration: custom-field scratch",
        board_id=board_id,
        description="Throwaway task created by tests/integration/test_custom_field_writes_live.py.",
    )
    try:
        yield task.id
    finally:
        with contextlib.suppress(Exception):
            await archive_task(task.id)


async def test_set_custom_field_writes_string_value(
    _inject_live_client: KanbanToolClient,
    throwaway_task_id: int,
) -> None:
    """A string write lands on the wire and the response surfaces it on
    ``Task.custom_fields["custom_field_N"]``. Slot 1 is used because every
    Kanban Tool board exposes 15 slots regardless of board template."""
    sentinel = "kanbantool-mcp live integration probe"

    task = await set_custom_field(
        task_id=throwaway_task_id,
        slot=1,
        value=sentinel,
    )

    assert isinstance(task, Task)
    assert task.id == throwaway_task_id
    assert isinstance(task.custom_fields, dict)
    assert task.custom_fields.get("custom_field_1") == sentinel


async def test_set_custom_field_none_clears_value(
    _inject_live_client: KanbanToolClient,
    throwaway_task_id: int,
) -> None:
    """``value=None`` round-trips: writing then clearing leaves the slot
    empty on the next response. This is the contract bit that distinguishes
    ``set_custom_field`` from ``update_task`` — None means *clear*, not
    *omit*. Pre-fix routing through ``_patch_task`` would silently drop
    the key and the slot would retain its prior value."""
    # Seed the slot with a value first so the clear is observable.
    seeded = await set_custom_field(
        task_id=throwaway_task_id,
        slot=2,
        value="will be cleared",
    )
    assert seeded.custom_fields.get("custom_field_2") == "will be cleared"

    # Clearing: the response should now show the slot as None / missing.
    cleared = await set_custom_field(
        task_id=throwaway_task_id,
        slot=2,
        value=None,
    )

    assert isinstance(cleared, Task)
    # Either an explicit ``None`` value or the key absent from the dict —
    # both are valid "cleared" representations on the wire. Lock the
    # not-still-set semantic rather than the exact null/missing shape.
    assert cleared.custom_fields.get("custom_field_2") is None
