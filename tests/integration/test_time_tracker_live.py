"""Live integration tests for the M4 time-tracker tools.

Coverage scope: the four write/read time-tracker tools end-to-end against a
real Kanban Tool account. Each test creates the artifacts it needs (timer
on a throwaway task) and cleans them up before returning, so a re-run leaves
no orphans on the test account.

These run only via the ``Live Integration`` workflow — see
``pyproject.toml``'s ``addopts`` (``--ignore=tests/integration``) and
``.github/workflows/integration.yml``.

Assertions are SHAPE-not-VALUE: the live API's specific ids, timestamps,
and per-account state shift, so the tests lock model types and the bits of
contract semantics worth guarding (``is_running`` flips, ``ended_at`` set,
DELETE-returns-None) — never specific values.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import pytest

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.models import TimeTracker
from kanbantool_mcp.server import (
    archive_task,
    create_task,
    delete_timer,
    list_boards,
    list_my_timers,
    start_timer,
    stop_timer,
)


@pytest.fixture
async def throwaway_task(_inject_live_client: KanbanToolClient) -> AsyncIterator[tuple[int, int]]:
    """Create a synthetic task on the first available board, yield
    ``(task_id, board_id)``, then archive the task on teardown.

    Timers must be attached to a real task on a real board — the API rejects
    ``start_timer`` calls otherwise. We create a single-use task per test
    so timer assertions can run against fresh state, and archive it after
    (the API has no hard-delete for tasks via our tool surface; archival
    is the cleanup signal an LLM caller would use too)."""
    boards = await list_boards()
    if not boards:
        pytest.skip("test account has no boards; live tests need at least one.")
    board_id = boards[0].id
    task = await create_task(
        name="kanbantool-mcp live integration: timer scratch",
        board_id=board_id,
        description="Throwaway task created by tests/integration/test_time_tracker_live.py.",
    )
    try:
        yield task.id, board_id
    finally:
        # Best-effort cleanup — never let a teardown failure mask the
        # actual test result. If archive_task raises (e.g. transient 5xx),
        # the orphaned task will surface in the next run's listing and a
        # maintainer can sweep it manually.
        with contextlib.suppress(Exception):
            await archive_task(task.id)


async def test_start_timer_returns_running_tracker(
    _inject_live_client: KanbanToolClient,
    throwaway_task: tuple[int, int],
) -> None:
    """``start_timer`` POSTs and returns a ``TimeTracker`` in the running
    state (``ended_at`` unset, ``is_running=True``). Cleans up by deleting
    the timer immediately after the assertions land."""
    task_id, board_id = throwaway_task

    timer = await start_timer(task_id=task_id, board_id=board_id)
    try:
        assert isinstance(timer, TimeTracker)
        # Direct timer endpoints always include ``id``; only inline-on-Task
        # entries from ``/tasks/{id}.json`` omit it (which is why ``id`` is
        # typed ``int | None``).
        assert timer.id is not None
        assert timer.id > 0
        assert timer.task_id == task_id
        assert timer.board_id == board_id
        assert timer.ended_at is None
        assert timer.is_running is True
    finally:
        # Always clean up — even if the assertions failed, the timer is
        # already on the wire and would persist on the test account.
        with contextlib.suppress(Exception):
            if timer.id is not None:
                await delete_timer(timer.id)


async def test_stop_timer_sets_ended_at(
    _inject_live_client: KanbanToolClient,
    throwaway_task: tuple[int, int],
) -> None:
    """``stop_timer`` PUTs ``ended_at`` (defaults to now) and the response's
    ``is_running`` flips to ``False``. Round-trip: start → stop → delete."""
    task_id, board_id = throwaway_task

    started = await start_timer(task_id=task_id, board_id=board_id)
    assert started.id is not None
    try:
        stopped = await stop_timer(timer_id=started.id)

        assert isinstance(stopped, TimeTracker)
        assert stopped.id == started.id
        # The defining bit of contract: stopping populates ``ended_at`` and
        # the derived ``is_running`` flag flips.
        assert stopped.ended_at is not None
        assert isinstance(stopped.ended_at, str)
        assert stopped.is_running is False
    finally:
        with contextlib.suppress(Exception):
            await delete_timer(started.id)


async def test_delete_timer_returns_none(
    _inject_live_client: KanbanToolClient,
    throwaway_task: tuple[int, int],
) -> None:
    """``delete_timer`` returns ``None`` on success — the API responds with
    an empty body (204 / empty 200) and the typed return mirrors that.
    No try/finally cleanup needed: deletion IS the cleanup."""
    task_id, board_id = throwaway_task

    timer = await start_timer(task_id=task_id, board_id=board_id)
    assert timer.id is not None
    result = await delete_timer(timer.id)

    assert result is None


async def test_list_my_timers_returns_typed_list(
    _inject_live_client: KanbanToolClient,
    throwaway_task: tuple[int, int],
) -> None:
    """``list_my_timers`` returns the authenticated user's timers across
    all tasks. Seed one running timer so the list is guaranteed non-empty,
    confirm the seeded id is present, then clean up.

    Note: the basic typed-list shape is also covered by
    ``test_live_read_tools.test_list_my_timers_returns_typed_list``; this
    test additionally asserts the create→list round-trip works (a freshly
    started timer surfaces on the listing)."""
    task_id, board_id = throwaway_task

    started = await start_timer(task_id=task_id, board_id=board_id)
    assert started.id is not None
    try:
        timers = await list_my_timers()

        assert isinstance(timers, list)
        assert all(isinstance(t, TimeTracker) for t in timers)
        # The just-started timer must appear in the listing.
        assert any(t.id == started.id for t in timers)
    finally:
        with contextlib.suppress(Exception):
            await delete_timer(started.id)
