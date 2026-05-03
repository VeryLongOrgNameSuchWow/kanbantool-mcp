"""Live integration tests for the read tools.

These hit a real Kanban Tool test account. They are excluded from the default
``pytest`` run (see ``pyproject.toml``'s ``addopts``) and only execute under
the ``Live Integration`` workflow on demand.

Coverage scope: the five read tools end-to-end against any populated board on
the test account. We deliberately do NOT exercise the write tools live — they
would create real artifacts on the account every run and the spike in #62
already confirmed the write path. Read coverage proves the wire contract for
our renamed/aliased fields, which is the bit most likely to silently drift.

Assertions are SHAPE-not-VALUE: the test account's task counts and content
will shift over time as we exercise the integration. Locking exact numbers
would make the suite brittle without adding signal. The tests do not assume
any particular board name or id — see the ``populated_board_id`` fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.models import (
    Board,
    ChangelogEntry,
    Collaborator,
    CustomFieldDefinition,
    Subtask,
    Task,
    TimeTracker,
    User,
)
from kanbantool_mcp.server import (
    get_board,
    get_task,
    get_user,
    list_board_collaborators,
    list_boards,
    list_custom_field_definitions,
    list_my_timers,
    list_subtasks,
    recent_changes,
    search_tasks,
    whoami,
)


async def test_list_boards_returns_account_boards(_inject_live_client: KanbanToolClient) -> None:
    boards = await list_boards()

    assert isinstance(boards, list)
    assert len(boards) >= 1
    assert all(isinstance(b, Board) for b in boards)


async def test_get_board_returns_columns_and_card_template(
    _inject_live_client: KanbanToolClient, populated_board_id: int
) -> None:
    board = await get_board(populated_board_id)

    assert isinstance(board, Board)
    assert board.id == populated_board_id
    # The detail endpoint surfaces columns (wire: ``workflow_stages``) and
    # ``card_template``; both are absent from the compact list endpoint.
    assert len(board.columns) >= 1
    assert isinstance(board.card_template, dict)
    assert len(board.card_template) >= 1


async def test_search_tasks_filters_archived(
    _inject_live_client: KanbanToolClient, populated_board_id: int
) -> None:
    # Earlier spike probes saw ``/tasks/search.json`` 500 on an empty account;
    # against a populated board the call should succeed. If it still 500s,
    # that's a real upstream finding worth flagging in CI output.
    response = await search_tasks(query="archived:false", board_id=populated_board_id)

    assert len(response.results) >= 1
    assert all(isinstance(t, Task) for t in response.results)
    assert all(t.board_id == populated_board_id for t in response.results)
    # ``archived:false`` should mean the filter behaved.
    assert all(t.archived_at is None for t in response.results)
    # Pagination envelope is populated by the live API.
    assert response.total_count is None or response.total_count >= len(response.results)
    assert response.page == 1
    assert isinstance(response.has_more, bool)


async def test_get_task_populates_renamed_wire_fields(
    _inject_live_client: KanbanToolClient, populated_board_id: int
) -> None:
    # Pick a task id off the live board rather than hard-coding one — task ids
    # aren't documented to be stable, and this exercises the discovery flow an
    # LLM agent would actually use.
    board = await get_board(populated_board_id)
    search_hits = await search_tasks(query="archived:false", board_id=populated_board_id)
    assert search_hits.results, "populated_board_id fixture promised non-archived tasks"
    task_id = search_hits.results[0].id

    task = await get_task(task_id)

    assert isinstance(task, Task)
    assert task.id == task_id
    assert task.board_id == populated_board_id
    # The renamed inbound alias: wire ``workflow_stage_id`` → model ``lane_id``.
    assert task.lane_id is not None
    assert task.lane_id in {c.id for c in board.columns}
    # Counts/totals surface as their renamed model fields. They're typed
    # ``int | None`` — assert the type, not a specific value.
    assert task.comments_count is None or isinstance(task.comments_count, int)
    assert task.timers_total is None or isinstance(task.timers_total, int)
    assert task.assigned_user_id is None or isinstance(task.assigned_user_id, int)
    # Non-archived path: ``archived_at`` should be None and the derived
    # ``is_archived`` flag should agree.
    assert task.archived_at is None
    assert task.is_archived is False
    # ``block_reason`` is optional; just lock the type.
    assert task.block_reason is None or isinstance(task.block_reason, str)


async def test_get_task_surfaces_additive_v3_fields(
    _inject_live_client: KanbanToolClient, populated_board_id: int
) -> None:
    """Type-only assertions that the additive #38 / #59 fields are reachable
    through the model. We don't lock values — the test account's data
    drifts — but the v3 wire payload should advertise these keys, so the
    typed attributes must exist on the resulting ``Task`` and respect their
    declared types where present."""
    search_hits = await search_tasks(query="archived:false", board_id=populated_board_id)
    assert search_hits.results, "populated_board_id fixture promised non-archived tasks"
    task_id = search_hits.results[0].id

    task = await get_task(task_id)

    # Sizing & estimation
    assert task.size_estimate is None or isinstance(task.size_estimate, int)
    assert task.size_estimate_description is None or isinstance(task.size_estimate_description, str)
    assert task.time_estimate is None or isinstance(task.time_estimate, int)
    # Search / discoverability — list of strings on the wire; ``None`` is
    # coerced to ``[]`` so callers see a stable list type.
    assert isinstance(task.search_tags, list)
    assert all(isinstance(tag, str) for tag in task.search_tags)
    # Visual markers
    assert task.card_color is None or isinstance(task.card_color, str)
    assert task.card_color_in_rgb is None or isinstance(task.card_color_in_rgb, str)
    assert task.card_color_invert is None or isinstance(task.card_color_invert, bool)
    assert task.card_type_id is None or isinstance(task.card_type_id, int)
    # Schedule fields (raw dicts)
    assert task.recurring_schedule is None or isinstance(task.recurring_schedule, dict)
    assert task.reminders_schedule is None or isinstance(task.reminders_schedule, dict)
    # Relationships — collection fields default to ``[]``, never ``None``.
    assert isinstance(task.linked_tasks, list)
    assert task.linked_tasks_status is None or isinstance(task.linked_tasks_status, str)
    assert isinstance(task.task_dependencies, list)
    assert isinstance(task.collaborators, list)
    # Attachments
    assert isinstance(task.attachments, list)
    assert task.attachments_count is None or isinstance(task.attachments_count, int)
    # Provenance & state
    assert task.created_by_id is None or isinstance(task.created_by_id, int)
    assert task.moved_at is None or isinstance(task.moved_at, str)
    assert task.postponed_until is None or isinstance(task.postponed_until, str)
    assert task.subtasks_completed_count is None or isinstance(task.subtasks_completed_count, int)
    assert task.external_id is None or isinstance(task.external_id, str)
    assert task.external_link is None or isinstance(task.external_link, str)


async def test_recent_changes_populates_renamed_fields(
    _inject_live_client: KanbanToolClient, populated_board_id: int
) -> None:
    # Look back a year to keep the test stable: the populated board's most
    # recent activity may have been months ago, but anything older than the
    # window would silently produce an empty list and make the
    # populates-renamed-fields assertions vacuous.
    since = datetime.now(UTC) - timedelta(days=365)
    entries = await recent_changes(board_id=populated_board_id, since=since)

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


async def test_list_subtasks_and_inline_task_subtasks_agree(
    _inject_live_client: KanbanToolClient, populated_board_id: int
) -> None:
    """The Kanban Tool API has no dedicated list-subtasks endpoint — subtasks
    are inlined on ``GET /tasks/{id}.json``. Lock that contract: ``list_subtasks``
    is sugar over ``get_task``, and both must surface the same shape.

    Shape-not-value: we don't know if the picked task has subtasks (the welcome
    board has none today), so we assert types and structural equivalence rather
    than counts. This test would have caught the broken nested-endpoint path
    pre-fix (the old code 404'd live)."""
    search_hits = await search_tasks(query="archived:false", board_id=populated_board_id)
    assert search_hits.results, "populated_board_id fixture promised non-archived tasks"
    task_id = search_hits.results[0].id

    subtasks = await list_subtasks(task_id)
    assert isinstance(subtasks, list)
    assert all(isinstance(s, Subtask) for s in subtasks)

    # Inline path: ``Task.subtasks`` from ``get_task`` should be the same list.
    task = await get_task(task_id)
    assert isinstance(task.subtasks, list)
    assert all(isinstance(s, Subtask) for s in task.subtasks)
    assert [s.id for s in subtasks] == [s.id for s in task.subtasks]


async def test_whoami_returns_authenticated_user(
    _inject_live_client: KanbanToolClient,
) -> None:
    """``whoami`` resolves through the ``/users/current.json`` 302-redirect
    alias (see #57) and returns the typed User. Locks shape-only assertions
    so the test stays valid as the account's profile drifts."""
    me = await whoami()
    assert isinstance(me, User)
    assert me.id > 0
    # ``name`` and ``initials`` are nullable per the model but should be
    # populated for any real account. Type-only check tolerates either way.
    assert me.name is None or isinstance(me.name, str)
    assert me.initials is None or isinstance(me.initials, str)
    # Role flags should at minimum be Boolean-or-None (never raise).
    assert me.is_account_admin is None or isinstance(me.is_account_admin, bool)


async def test_get_user_round_trips_against_whoami(
    _inject_live_client: KanbanToolClient,
) -> None:
    """``whoami`` returns id; ``get_user(id)`` should return a User whose id
    matches. Cheaper than locking specific account details."""
    me = await whoami()
    fetched = await get_user(me.id)
    assert isinstance(fetched, User)
    assert fetched.id == me.id


async def test_list_board_collaborators_against_populated_board(
    _inject_live_client: KanbanToolClient, populated_board_id: int
) -> None:
    """The board's ``collaborators[]`` is the canonical user-discovery
    surface (no bulk list-users endpoint exists). Verifies the Board model
    decodes the field and the wrapper tool propagates it."""
    collaborators = await list_board_collaborators(populated_board_id)
    assert isinstance(collaborators, list)
    # Any account with at least one user (i.e. the authenticated one) on the
    # board should report >= 1 collaborator. Type-only assertions for the
    # rest tolerate locale / suspension state drift.
    assert len(collaborators) >= 1
    assert all(isinstance(c, Collaborator) for c in collaborators)
    first = collaborators[0]
    assert first.id > 0
    assert first.name is None or isinstance(first.name, str)
    assert first.active is None or isinstance(first.active, bool)


async def test_list_custom_field_definitions_against_populated_board(
    _inject_live_client: KanbanToolClient, populated_board_id: int
) -> None:
    """Lock the wire-shape contract for the per-board custom-field metadata
    surface. Kanban Tool boards always have 15 slots whether they're
    enabled or not — the trial seed fixture has them all disabled, but the
    structural assertions below tolerate either state."""
    definitions = await list_custom_field_definitions(populated_board_id)

    assert isinstance(definitions, list)
    # All known Kanban Tool plans expose 15 numbered slots, even on the free
    # trial. If this ever changes upstream, expect this to be a real signal.
    assert len(definitions) == 15
    assert all(isinstance(d, CustomFieldDefinition) for d in definitions)
    assert [d.slot for d in definitions] == list(range(1, 16))
    first = definitions[0]
    # Type-only locks for the rest: labels are user-customisable, types vary
    # per board, etc. ``enabled`` is always Boolean-or-None.
    assert first.label is None or isinstance(first.label, str)
    assert first.type_ is None or isinstance(first.type_, str)
    assert first.enabled is None or isinstance(first.enabled, bool)


async def test_get_task_collects_custom_field_values(
    _inject_live_client: KanbanToolClient, populated_board_id: int
) -> None:
    """The Task before-validator pulls ``custom_field_1..15`` into a single
    ``custom_fields`` dict. Verify the lift happens against a real task —
    on the welcome board they're all ``None``, but the keys must be there."""
    search_hits = await search_tasks(query="archived:false", board_id=populated_board_id)
    assert search_hits.results, "populated_board_id fixture promised non-archived tasks"
    task_id = search_hits.results[0].id
    task = await get_task(task_id)

    # On any board with all 15 slots emitted (every Kanban Tool board, per
    # the metadata test above), the dict should have all 15 keys present.
    assert isinstance(task.custom_fields, dict)
    assert set(task.custom_fields.keys()) == {f"custom_field_{i}" for i in range(1, 16)}


async def test_list_my_timers_returns_typed_list(
    _inject_live_client: KanbanToolClient,
) -> None:
    """Read-only live coverage for the time-tracker surface. Write tools
    (start_timer/stop_timer/delete_timer) are unit-tested only — running
    them in CI would create real timer artifacts on the test account
    every run. The full create→stop→delete cycle was spike-verified
    locally during implementation and is covered by the offline unit suite."""
    timers = await list_my_timers()

    assert isinstance(timers, list)
    # The user might genuinely have zero timers right now (rynbou's account
    # is mostly clean), so we don't assert a count. Type-only locks.
    assert all(isinstance(t, TimeTracker) for t in timers)
    for t in timers:
        # Critical fields the model surfaces; the rest are optional.
        # ``users/current.json``'s timer list always carries ``id``; only
        # inline-on-Task entries (from ``/tasks/{id}.json``) omit it, hence
        # the ``int | None`` model type.
        assert t.id is not None
        assert t.id > 0
        assert t.user_id is None or isinstance(t.user_id, int)
        # ``is_running`` is the computed-field flag; should be Boolean,
        # never None (derived from ``ended_at``).
        assert isinstance(t.is_running, bool)
