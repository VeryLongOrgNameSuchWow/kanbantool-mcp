"""Tests for the MCP prompt templates."""

from __future__ import annotations

import pytest

from kanbantool_mcp.server import daily_standup, mcp, my_workload, triage_backlog

EXPECTED_PROMPTS = {
    "daily_standup": {("board_id", True), ("hours", False)},
    "triage_backlog": {("board_id", True)},
    "my_workload": {("boards", True)},
}


async def test_three_prompts_registered_with_expected_signatures() -> None:
    prompts = await mcp.list_prompts()
    by_name = {p.name: p for p in prompts}
    assert set(by_name) == set(EXPECTED_PROMPTS), f"prompt set drifted; got {sorted(by_name)}"
    for name, expected_args in EXPECTED_PROMPTS.items():
        actual_args = {(a.name, a.required) for a in (by_name[name].arguments or [])}
        assert actual_args == expected_args, f"prompt {name!r} arguments drifted; got {actual_args}"


def test_daily_standup_includes_explicit_since_and_recent_changes() -> None:
    rendered = daily_standup(board_id=4217, hours=12)
    # Self-documenting demonstration of the "always-pass-since" contract — the
    # recipe never relies on the API's default-window behaviour. Forward-
    # compatible if/when the parameter becomes required upstream.
    assert "recent_changes(board_id=4217" in rendered
    assert 'since="' in rendered
    assert "12 hours" in rendered


def test_daily_standup_default_window_is_24h() -> None:
    rendered = daily_standup(board_id=1)
    assert "24 hours" in rendered


def test_triage_backlog_uses_correct_dsl_operators() -> None:
    rendered = triage_backlog(board_id=99)
    # Locks the verified Kanban Tool DSL spellings:
    #   assigned_to:!?  — unassigned (NOT assignee:none, which is invented)
    #   priority:1      — high (numeric: 1=high, 0=normal, -1=low)
    #   archived:false  — exclude archived (live-tested in integration)
    # Unknown operators silently return zero results, so locking the exact
    # spelling here is the only protection against the recipe quietly
    # producing empty triage runs.
    assert 'search_tasks(query="assigned_to:!? priority:1 archived:false"' in rendered
    assert "list_board_collaborators(board_id=99)" in rendered
    # Don't auto-assign without confirmation — defensive default for a tool
    # that proposes destructive-ish writes.
    assert "do NOT auto-assign" in rendered


def test_my_workload_fans_out_one_search_per_board() -> None:
    rendered = my_workload(boards=[10, 20, 30])
    # whoami is load-bearing here, not decorative: the DSL has no
    # ``assignee:me`` shortcut, so the LLM must read User.initials and
    # substitute it into each per-board query as ``<INITIALS>``.
    assert "whoami()" in rendered
    assert "initials" in rendered
    assert "<INITIALS>" in rendered
    for board_id in (10, 20, 30):
        assert (
            f'search_tasks(query="assigned_to:@<INITIALS> archived:false", board_id={board_id})'
            in rendered
        )


def test_my_workload_rejects_empty_boards_list() -> None:
    with pytest.raises(ValueError, match="at least one board id"):
        my_workload(boards=[])
