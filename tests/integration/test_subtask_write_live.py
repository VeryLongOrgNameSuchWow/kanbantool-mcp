"""Live integration tests for the M2 subtask write tools.

Coverage scope: ``add_subtask``, ``update_subtask``, ``delete_subtask``,
``reorder_subtasks`` end-to-end against the real Kanban Tool API. The
offline suite mocks these against ``respx`` recordings; this file locks
the wire-shape contract against an actual account.

Subtasks live under a parent task — every test creates its own throwaway
parent task on the first available board, performs the assertion, and
archives the parent on teardown. Subtasks die with the parent (soft-deleted
along with the archive), so cleanup is one ``archive_task`` per test.

Wire quirks worth noting (already in ``server.py`` docstrings — this is
just the test-author's lens):

- ``POST /subtasks.json`` takes a flat top-level body, NOT a Rails-style
  envelope. Live spike confirmed wrapping breaks the parent linkage.
- ``PUT /subtasks/reorder.json`` takes ``ids`` as a comma-joined string,
  not a JSON array. The tool accepts ``list[int]`` and joins on the way out.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import pytest

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.models import Subtask
from kanbantool_mcp.server import (
    add_subtask,
    archive_task,
    create_task,
    delete_subtask,
    list_boards,
    list_subtasks,
    reorder_subtasks,
    update_subtask,
)


@pytest.fixture
async def throwaway_task_id(_inject_live_client: KanbanToolClient) -> AsyncIterator[int]:
    """Create a single-use parent task on the first available board, yield
    its id, archive on teardown.

    Subtasks attach to a real parent — there is no "standalone subtask"
    concept on the API. Per-test fresh parents keep state contained and
    avoid cross-test ordering coupling on subtask ids."""
    boards = await list_boards()
    if not boards:
        pytest.skip("test account has no boards; live tests need at least one.")
    board_id = boards[0].id
    task = await create_task(
        name="kanbantool-mcp live integration: subtask scratch",
        board_id=board_id,
        description="Throwaway task created by tests/integration/test_subtask_write_live.py.",
    )
    try:
        yield task.id
    finally:
        with contextlib.suppress(Exception):
            await archive_task(task.id)


async def test_add_subtask_returns_subtask(
    _inject_live_client: KanbanToolClient,
    throwaway_task_id: int,
) -> None:
    """``add_subtask`` POSTs and returns a ``Subtask`` whose ``name`` echoes
    the request and whose ``task_id`` links back to the parent. The flat-body
    convention (no Rails envelope) is exercised implicitly — if it broke,
    ``task_id`` would come back ``None`` and this assertion would fail."""
    sentinel = "kanbantool-mcp live integration: add_subtask probe"
    sub = await add_subtask(task_id=throwaway_task_id, name=sentinel)

    assert isinstance(sub, Subtask)
    assert sub.id > 0
    assert sub.name == sentinel
    # Parent linkage — the bit the flat-body convention guards.
    assert sub.task_id == throwaway_task_id
    # A freshly-created subtask isn't deleted; lock that so soft-delete
    # tests below have a meaningful before/after.
    assert sub.deleted_at is None


async def test_update_subtask_marks_completed(
    _inject_live_client: KanbanToolClient,
    throwaway_task_id: int,
) -> None:
    """``update_subtask`` PATCHes a partial body and returns the updated
    ``Subtask``. Marking complete is the cheapest mutation that has a
    type-stable, observable effect on the response."""
    seeded = await add_subtask(
        task_id=throwaway_task_id,
        name="kanbantool-mcp live integration: about to be completed",
    )
    assert seeded.is_completed is False or seeded.is_completed is None

    updated = await update_subtask(subtask_id=seeded.id, is_completed=True)

    assert isinstance(updated, Subtask)
    assert updated.id == seeded.id
    assert updated.is_completed is True


async def test_delete_subtask_returns_soft_deleted_subtask(
    _inject_live_client: KanbanToolClient,
    throwaway_task_id: int,
) -> None:
    """``delete_subtask`` returns the deleted ``Subtask`` with ``deleted_at``
    populated — soft-delete semantics, mirroring ``delete_comment``. The
    record stays server-side; the MCP-visible effect is "the subtask is
    gone" because it stops appearing on the parent's ``subtasks`` array."""
    seeded = await add_subtask(
        task_id=throwaway_task_id,
        name="kanbantool-mcp live integration: about to be deleted",
    )
    assert seeded.deleted_at is None  # sanity

    deleted = await delete_subtask(subtask_id=seeded.id)

    assert isinstance(deleted, Subtask)
    assert deleted.id == seeded.id
    # The soft-delete signal: ``deleted_at`` is now populated.
    assert deleted.deleted_at is not None
    assert isinstance(deleted.deleted_at, str)


async def test_reorder_subtasks_returns_reordered_list(
    _inject_live_client: KanbanToolClient,
    throwaway_task_id: int,
) -> None:
    """``reorder_subtasks`` PUTs ``{task_id, ids: "comma,joined"}`` and
    returns ``list[Subtask]`` in the new order. Seeds two subtasks, reverses
    the order, asserts the response reflects it.

    The wire-quirk worth guarding: the ``ids`` parameter is sent as a
    comma-joined string — the tool joins on the way out. The integration
    happy path is the safety net if that ever silently regresses to a JSON
    array (the API would 422)."""
    first = await add_subtask(
        task_id=throwaway_task_id,
        name="kanbantool-mcp live integration: reorder probe A",
    )
    second = await add_subtask(
        task_id=throwaway_task_id,
        name="kanbantool-mcp live integration: reorder probe B",
    )

    # The API requires the FULL set of subtask ids on the parent, in order.
    # Pull the live list rather than assume only our two exist (defensive
    # against any leftover state from a partial previous run).
    current = await list_subtasks(throwaway_task_id)
    current_ids = [s.id for s in current if s.deleted_at is None]
    assert first.id in current_ids
    assert second.id in current_ids
    # Reverse to make the operation observable: whatever the current order,
    # reversing produces a different one (unless there's only one subtask,
    # which we've already prevented by seeding two).
    new_order = list(reversed(current_ids))

    result = await reorder_subtasks(task_id=throwaway_task_id, ids=new_order)

    assert isinstance(result, list)
    assert all(isinstance(s, Subtask) for s in result)
    # The defining contract: the response order matches the requested order.
    assert [s.id for s in result] == new_order
