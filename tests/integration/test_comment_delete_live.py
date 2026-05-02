"""Live integration tests for the M2/M5 comment write tools.

Coverage scope: ``add_comment`` (M2) and ``delete_comment`` (M5) end-to-end
against the real Kanban Tool API. The interesting contract bits live
coverage locks in:

- ``add_comment`` POSTs and returns a ``Comment`` with ``content`` populated.
  The body field is ``content``, not ``text`` — the M5 wire-field bugfix
  documents this; locking it live prevents silent regression to the broken
  pre-fix shape (every call 422'd ``Content can't be blank``).
- ``delete_comment`` returns a ``Comment`` with ``deleted_at`` populated
  (soft-delete, mirroring ``delete_subtask``). The offline mock stipulates
  this; the live test confirms the API actually behaves that way.

Cleanup: each test creates a throwaway task and a comment on it, exercises
the operation, then archives the task. No orphans — the parent task is
gone after the test even if assertions fail.
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


async def test_add_comment_returns_comment_with_content(
    _inject_live_client: KanbanToolClient,
    throwaway_task_id: int,
) -> None:
    """``add_comment`` POSTs and returns a ``Comment`` whose ``content``
    echoes the request. The wire-field name is ``content`` (not ``text``);
    pre-M5 the body said ``text`` and every call 422'd ``Content can't be
    blank``. This test is the live regression guard for that fix.

    The created comment is a real artifact on the parent task — clean up by
    soft-deleting it before the parent is archived on fixture teardown.
    Soft-delete is sufficient: archived parents drop their comments from
    listings regardless, and the test is about ``add_comment``'s contract,
    not the lifecycle interaction."""
    sentinel = "kanbantool-mcp live integration: add_comment probe"
    posted = await add_comment(task_id=throwaway_task_id, content=sentinel)
    try:
        assert isinstance(posted, Comment)
        assert posted.id > 0
        # Locking the content round-trip is the bit the M5 wire-field bugfix
        # actually unblocked — pre-fix this assertion would never run because
        # the create call 422'd.
        assert posted.content == sentinel
        # Sanity: a freshly-posted comment isn't pre-deleted.
        assert posted.deleted_at is None
    finally:
        with contextlib.suppress(Exception):
            await delete_comment(task_id=throwaway_task_id, comment_id=posted.id)


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
