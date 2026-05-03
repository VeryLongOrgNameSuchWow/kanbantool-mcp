"""Tests for the ``KANBANTOOL_READ_ONLY`` env var.

The flag is read at module import time (decorators evaluate during import,
so the read/write split must be settled by then). Each test reloads the
``server`` module under a fresh env to assert a specific registration
outcome, then reloads it back to default state to keep the rest of the
suite working against the full 26-tool surface.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

from kanbantool_mcp import config as config_module
from kanbantool_mcp import server as server_module

# The 11 read-class tools, mirrors ``server.READ_ONLY_TOOLS``. Spelled out
# inline here as a check on the constant — if the constant drifts, this set
# stays correct (and the assertion below catches the drift).
EXPECTED_READ_TOOLS = frozenset(
    {
        "list_boards",
        "get_board",
        "search_tasks",
        "get_task",
        "recent_changes",
        "whoami",
        "get_user",
        "list_board_collaborators",
        "list_custom_field_definitions",
        "list_subtasks",
        "list_my_timers",
    }
)

# ``ping`` is always registered (transport smoke test, no API calls).
EXPECTED_READ_ONLY_TOOLS = EXPECTED_READ_TOOLS | {"ping"}


def _registered_tool_names() -> set[str]:
    tools = asyncio.run(server_module.mcp.list_tools())
    return {t.name for t in tools}


@pytest.fixture
def reload_server_module(monkeypatch: pytest.MonkeyPatch):
    """Yield a callable that reloads ``server`` (and its config dependency)
    so a freshly-set ``KANBANTOOL_READ_ONLY`` value takes effect for the
    decorators. After the test, reload one more time with the flag cleared
    so the default-mode test fixtures see the full tool surface."""

    def _reload() -> None:
        importlib.reload(config_module)
        importlib.reload(server_module)

    yield _reload

    monkeypatch.delenv("KANBANTOOL_READ_ONLY", raising=False)
    _reload()


def test_read_only_constant_lists_eleven_tools() -> None:
    assert len(server_module.READ_ONLY_TOOLS) == 11
    assert server_module.READ_ONLY_TOOLS == EXPECTED_READ_TOOLS


def test_default_mode_registers_full_tool_surface() -> None:
    # Locks the canonical 26-tool count (11 reads + 14 writes + ping).
    names = _registered_tool_names()
    assert len(names) == 26
    assert names >= EXPECTED_READ_ONLY_TOOLS
    assert "create_task" in names
    assert "delete_subtask" in names


@pytest.mark.parametrize("flag_value", ["1", "true", "yes", "on", "TRUE", "On"])
def test_read_only_mode_registers_only_read_tools(
    monkeypatch: pytest.MonkeyPatch, reload_server_module, flag_value: str
) -> None:
    monkeypatch.setenv("KANBANTOOL_READ_ONLY", flag_value)
    reload_server_module()

    names = _registered_tool_names()
    assert names == EXPECTED_READ_ONLY_TOOLS
    assert len(names) == 12  # 11 read tools + ping


@pytest.mark.parametrize("flag_value", ["", "0", "false", "no", "off", "ture"])
def test_falsey_or_unrecognised_values_keep_writes_enabled(
    monkeypatch: pytest.MonkeyPatch, reload_server_module, flag_value: str
) -> None:
    # A typo'd value (``ture``) MUST NOT silently disable the gate by accident.
    # Anything outside the known truthy set leaves the full surface registered.
    monkeypatch.setenv("KANBANTOOL_READ_ONLY", flag_value)
    reload_server_module()

    names = _registered_tool_names()
    assert "create_task" in names
    assert "add_comment" in names
    assert "delete_timer" in names


def test_read_only_mode_keeps_write_functions_importable(
    monkeypatch: pytest.MonkeyPatch, reload_server_module
) -> None:
    # The decorator returns the bare function in read-only mode; tests and
    # other in-process callers can still invoke ``server.create_task`` even
    # when the MCP surface hides it. Locks the "decorator returns the
    # function unchanged" branch so a future refactor doesn't accidentally
    # turn it into a registration-only wrapper.
    monkeypatch.setenv("KANBANTOOL_READ_ONLY", "1")
    reload_server_module()

    assert callable(server_module.create_task)
    assert callable(server_module.delete_timer)
