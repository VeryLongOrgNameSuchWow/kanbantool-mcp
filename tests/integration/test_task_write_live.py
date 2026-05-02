"""Live integration tests for the M2 task write tools.

Coverage scope: ``create_task``, ``update_task``, ``move_task``, ``archive_task``
end-to-end against the real Kanban Tool API. PR #121 covered M4/M5; the older
M1/M2 surface was assumed-tested but the only live coverage was indirect (these
tools were used as setup in other integration files, not contract-tested).
With v1.0 committing to wire shapes via SEMVER.md, every tool needs at least
one direct live happy-path that asserts the response shape.

Each test creates the artifacts it needs on the first available board, runs
the contract assertion, and archives the throwaway task on teardown. No
orphans — even if assertions fail, ``contextlib.suppress(Exception)`` keeps
cleanup from masking the real failure while still leaving the account clean
in the common case.

Assertions are SHAPE-not-VALUE: lock the typed model and the specific field
the tool modifies (``update_task(name="X")`` → ``result.name == "X"``);
don't lock unrelated drift-prone fields like timestamps or counts.
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
    get_board,
    get_task,
    list_boards,
    move_task,
    update_task,
)


@pytest.fixture
async def throwaway_task_id(_inject_live_client: KanbanToolClient) -> AsyncIterator[int]:
    """Create a single-use task on the first available board, yield its id,
    archive on teardown.

    Each test gets its own task so concurrent or repeat runs don't trip over
    each other's state. Archival on teardown leaves the test account clean."""
    boards = await list_boards()
    if not boards:
        pytest.skip("test account has no boards; live tests need at least one.")
    board_id = boards[0].id
    task = await create_task(
        name="kanbantool-mcp live integration: task-write scratch",
        board_id=board_id,
        description="Throwaway task created by tests/integration/test_task_write_live.py.",
    )
    try:
        yield task.id
    finally:
        with contextlib.suppress(Exception):
            await archive_task(task.id)


async def test_create_task_returns_task_with_name_and_board(
    _inject_live_client: KanbanToolClient,
) -> None:
    """``create_task`` POSTs and returns a ``Task`` whose ``name`` and
    ``board_id`` echo the request. Cleans up by archiving the new task."""
    boards = await list_boards()
    if not boards:
        pytest.skip("test account has no boards; live tests need at least one.")
    board_id = boards[0].id

    sentinel_name = "kanbantool-mcp live integration: create_task probe"
    task = await create_task(
        name=sentinel_name,
        board_id=board_id,
        description="Created by test_create_task_returns_task_with_name_and_board.",
    )
    try:
        assert isinstance(task, Task)
        assert task.id > 0
        assert task.name == sentinel_name
        assert task.board_id == board_id
        # Newly-created tasks aren't archived; lock that derived flag here so
        # ``archive_task`` later has a meaningful before/after.
        assert task.is_archived is False
    finally:
        with contextlib.suppress(Exception):
            await archive_task(task.id)


async def test_update_task_sets_new_name(
    _inject_live_client: KanbanToolClient,
    throwaway_task_id: int,
) -> None:
    """``update_task`` PATCHes a partial body and returns a ``Task`` with the
    updated field reflected on the response. Renaming is the cheapest write
    that has an observable, type-stable effect."""
    sentinel = "kanbantool-mcp live integration: renamed by update_task"

    updated = await update_task(task_id=throwaway_task_id, name=sentinel)

    assert isinstance(updated, Task)
    assert updated.id == throwaway_task_id
    assert updated.name == sentinel


async def test_move_task_changes_lane(
    _inject_live_client: KanbanToolClient,
    throwaway_task_id: int,
) -> None:
    """``move_task`` PATCHes ``{column_id: ...}`` (wire ``workflow_stage_id``)
    and returns a ``Task`` whose ``lane_id`` is the requested column. Picks
    a column on the same board different from the task's current lane so the
    move is observable, skipping if the board has only a single column."""
    task = await get_task(throwaway_task_id)
    assert task.board_id is not None
    board = await get_board(task.board_id)
    other_columns = [c for c in board.columns if c.id != task.lane_id]
    if not other_columns:
        pytest.skip("board has only one column; move_task needs at least two to be observable.")
    target_column_id = other_columns[0].id

    moved = await move_task(task_id=throwaway_task_id, column_id=target_column_id)

    assert isinstance(moved, Task)
    assert moved.id == throwaway_task_id
    # ``column_id`` (caller-facing) → wire ``workflow_stage_id`` → model ``lane_id``.
    assert moved.lane_id == target_column_id


async def test_archive_task_sets_is_archived(
    _inject_live_client: KanbanToolClient,
) -> None:
    """``archive_task`` PATCHes ``{_action: "archive"}`` and returns a ``Task``
    with ``archived_at`` populated and the derived ``is_archived`` True.

    This test does NOT use the ``throwaway_task_id`` fixture — that fixture's
    teardown also archives, which would no-op on an already-archived task but
    the contract assertion belongs in-test. Manage the throwaway lifecycle
    inline."""
    boards = await list_boards()
    if not boards:
        pytest.skip("test account has no boards; live tests need at least one.")
    board_id = boards[0].id
    fresh = await create_task(
        name="kanbantool-mcp live integration: archive_task probe",
        board_id=board_id,
    )
    # Sanity: a brand-new task is not archived.
    assert fresh.is_archived is False

    archived = await archive_task(fresh.id)

    assert isinstance(archived, Task)
    assert archived.id == fresh.id
    # The defining bit of contract: ``archived_at`` populates and the derived
    # ``is_archived`` flag flips.
    assert archived.archived_at is not None
    assert isinstance(archived.archived_at, str)
    assert archived.is_archived is True
