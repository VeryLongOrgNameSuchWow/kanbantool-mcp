"""Live integration tests for the read tools.

These hit a real Kanban Tool test account. They are excluded from the default
``pytest`` run (see ``pyproject.toml``'s ``testpaths``) and only execute under
the ``Live Integration`` workflow on demand.

Coverage scope: the five read tools end-to-end against the seeded "Welcome"
board on the test account. We deliberately do NOT exercise the write tools
live — they would create real artifacts on the account every run and the
spike in #62 already confirmed the write path. Read coverage proves the wire
contract for our renamed/aliased fields, which is the bit most likely to
silently drift.

Assertions are SHAPE-not-VALUE: the test account's task counts and content
will shift over time as we exercise the integration. Locking exact numbers
would make the suite brittle without adding signal.
"""

from __future__ import annotations

import pytest

from kanbantool_mcp import server
from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.models import Board, ChangelogEntry, Task
from kanbantool_mcp.server import (
    get_board,
    get_task,
    list_boards,
    recent_changes,
    search_tasks,
)

# The seeded "Welcome to Kanban Tool!" board id on the test account. Stable
# across runs; if Kanban Tool ever re-seeds welcome boards with a different
# id this is the one knob to update.
_WELCOME_BOARD_ID = 1180501
_WELCOME_BOARD_NAME = "Welcome to Kanban Tool!"


@pytest.fixture
def _inject_live_client(
    monkeypatch: pytest.MonkeyPatch, live_client: KanbanToolClient
) -> KanbanToolClient:
    """Wire the live client into ``server._client`` so the MCP tools use it.

    Mirrors the unit-test ``_inject_client`` fixture; lives here (not in the
    integration ``conftest.py``) because it depends on the per-test
    ``monkeypatch`` fixture and is only consumed by tests that drive the MCP
    tool functions directly.
    """
    monkeypatch.setattr(server, "_client", live_client)
    return live_client


async def test_list_boards_returns_welcome_board(_inject_live_client: KanbanToolClient) -> None:
    boards = await list_boards()

    assert isinstance(boards, list)
    assert len(boards) >= 1
    assert all(isinstance(b, Board) for b in boards)

    welcome = next((b for b in boards if b.name == _WELCOME_BOARD_NAME), None)
    assert welcome is not None, (
        f"expected a board named {_WELCOME_BOARD_NAME!r}; got {[b.name for b in boards]!r}"
    )
    assert welcome.id == _WELCOME_BOARD_ID


async def test_get_board_returns_columns_and_card_template(
    _inject_live_client: KanbanToolClient,
) -> None:
    board = await get_board(_WELCOME_BOARD_ID)

    assert isinstance(board, Board)
    assert board.id == _WELCOME_BOARD_ID
    assert board.name == _WELCOME_BOARD_NAME
    # The detail endpoint surfaces columns (wire: ``workflow_stages``) and
    # ``card_template``; both are absent from the compact list endpoint.
    assert len(board.columns) >= 1
    assert isinstance(board.card_template, dict)
    assert len(board.card_template) >= 1


async def test_search_tasks_returns_non_archived_tutorial_tasks(
    _inject_live_client: KanbanToolClient,
) -> None:
    # Earlier spike probes saw ``/tasks/search.json`` 500 on an empty account;
    # with the seeded welcome board populated the call should succeed. If it
    # still 500s, that's a real upstream finding worth flagging in CI output.
    tasks = await search_tasks(query="archived:false", board_id=_WELCOME_BOARD_ID)

    assert isinstance(tasks, list)
    assert len(tasks) >= 1
    assert all(isinstance(t, Task) for t in tasks)
    assert all(t.board_id == _WELCOME_BOARD_ID for t in tasks)
    # ``archived:false`` should exclude the two archived spike tasks (ids
    # 77649691 / 77649693). Assert the filter behaved rather than the count.
    assert all(t.archived_at is None for t in tasks)


async def test_get_task_populates_renamed_wire_fields(
    _inject_live_client: KanbanToolClient,
) -> None:
    # Pick a task id off the live board rather than hard-coding one — task ids
    # on the welcome board aren't documented to be stable, and this exercises
    # the discovery flow an LLM agent would actually use.
    board = await get_board(_WELCOME_BOARD_ID)
    search_hits = await search_tasks(query="archived:false", board_id=_WELCOME_BOARD_ID)
    assert search_hits, "no non-archived tasks on the welcome board to fetch"
    task_id = search_hits[0].id

    task = await get_task(task_id)

    assert isinstance(task, Task)
    assert task.id == task_id
    assert task.board_id == _WELCOME_BOARD_ID
    # The renamed inbound alias: wire ``workflow_stage_id`` → model ``lane_id``.
    assert task.lane_id is not None
    assert task.lane_id in {c.id for c in board.columns}
    # Counts/totals surface as their renamed model fields. They're typed
    # ``int | None`` — assert the type, not a specific value, since the test
    # account's tutorial tasks may or may not have comments/timers.
    assert task.comments_count is None or isinstance(task.comments_count, int)
    assert task.timers_total is None or isinstance(task.timers_total, int)
    assert task.assigned_user_id is None or isinstance(task.assigned_user_id, int)
    # Non-archived path: ``archived_at`` should be None and the derived
    # ``is_archived`` flag should agree.
    assert task.archived_at is None
    assert task.is_archived is False
    # ``block_reason`` is optional; just lock the type.
    assert task.block_reason is None or isinstance(task.block_reason, str)


async def test_recent_changes_populates_renamed_fields(
    _inject_live_client: KanbanToolClient,
) -> None:
    entries = await recent_changes(board_id=_WELCOME_BOARD_ID)

    assert isinstance(entries, list)
    assert len(entries) >= 1
    assert all(isinstance(e, ChangelogEntry) for e in entries)

    first = entries[0]
    # Lock the renamed-field surface — these are the fields most likely to
    # silently drift if the API ever renames them upstream.
    assert first.what is not None
    assert isinstance(first.what, str)
    assert first.user_id is None or isinstance(first.user_id, int)
    assert first.changed_object_type is None or isinstance(first.changed_object_type, str)
    assert first.description is None or isinstance(first.description, str)
