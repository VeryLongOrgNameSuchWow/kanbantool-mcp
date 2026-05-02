"""Live integration tests for the M5 ``delete_comment`` tool.

Coverage scope: the comment soft-delete flow end-to-end against the real
Kanban Tool API. The interesting contract bit live coverage locks in:

- ``delete_comment`` returns a ``Comment`` with ``deleted_at`` populated
  (soft-delete, mirroring ``delete_subtask``). The offline mock stipulates
  this; the live test confirms the API actually behaves that way.

Cleanup: each test creates a throwaway task and a comment on it, exercises
the delete, then archives the task. No orphans — the parent task is gone
after the test even if assertions fail.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import pytest

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.models import Comment
from kanbantool_mcp.server import (
    add_comment,
    archive_task,
    create_task,
    delete_comment,
    list_boards,
)


@pytest.fixture
async def throwaway_task_id(_inject_live_client: KanbanToolClient) -> AsyncIterator[int]:
    """Create a single-use task on the first available board, yield its id,
    archive on teardown.

    Each delete_comment test gets its own task so a comment delete never
    affects a sibling test's view of the comments collection. Archival on
    teardown leaves the test account clean."""
    boards = await list_boards()
    if not boards:
        pytest.skip("test account has no boards; live tests need at least one.")
    board_id = boards[0].id
    task = await create_task(
        name="kanbantool-mcp live integration: comment scratch",
        board_id=board_id,
        description="Throwaway task created by tests/integration/test_comment_delete_live.py.",
    )
    try:
        yield task.id
    finally:
        with contextlib.suppress(Exception):
            await archive_task(task.id)


async def test_delete_comment_returns_soft_deleted_comment(
    _inject_live_client: KanbanToolClient,
    throwaway_task_id: int,
) -> None:
    """``delete_comment`` returns the deleted ``Comment`` with ``deleted_at``
    populated — soft-delete semantics. The id matches the original comment
    (the record is retained server-side, just flagged)."""
    posted = await add_comment(
        task_id=throwaway_task_id,
        content="kanbantool-mcp live integration: about to be deleted",
    )
    assert posted.deleted_at is None  # sanity: live comments aren't pre-deleted

    deleted = await delete_comment(task_id=throwaway_task_id, comment_id=posted.id)

    assert isinstance(deleted, Comment)
    assert deleted.id == posted.id
    # The soft-delete signal: ``deleted_at`` is now populated.
    assert deleted.deleted_at is not None
    assert isinstance(deleted.deleted_at, str)
