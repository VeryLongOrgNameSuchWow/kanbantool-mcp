"""Fixtures for live integration tests.

These tests hit a real Kanban Tool account via HTTP. They are excluded from
the default ``pytest`` run (see ``pyproject.toml``'s ``testpaths``) and only
execute in the ``Live Integration`` workflow, where ``KANBANTOOL_DOMAIN`` and
``KANBANTOOL_API_TOKEN`` come from repository secrets.

The parent ``tests/conftest.py`` defines an autouse fixture that pins the env
to dummy ``testacct``/``test-token`` values for offline unit tests. We override
that fixture here as a no-op so the real env vars survive into ``Config.from_env()``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

from kanbantool_mcp import server
from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.config import Config
from kanbantool_mcp.server import list_boards, search_tasks


@pytest.fixture(autouse=True)
def _kanbantool_env() -> None:
    """No-op override of the parent autouse fixture.

    The unit-test conftest pins ``KANBANTOOL_DOMAIN``/``KANBANTOOL_API_TOKEN``
    to dummy values; live tests must keep the real env vars set by the workflow.
    Skip the whole module if either is missing — running these locally without
    credentials would either 401 or hit the wrong account.
    """
    for var in ("KANBANTOOL_DOMAIN", "KANBANTOOL_API_TOKEN"):
        if not os.environ.get(var):
            pytest.skip(f"{var} not set; live integration tests require real credentials.")


@pytest.fixture
async def live_client() -> AsyncIterator[KanbanToolClient]:
    """A ``KanbanToolClient`` wired to the real account from env."""
    c = KanbanToolClient(Config.from_env())
    try:
        yield c
    finally:
        await c.aclose()


@pytest.fixture
def _inject_live_client(
    monkeypatch: pytest.MonkeyPatch, live_client: KanbanToolClient
) -> KanbanToolClient:
    """Wire the live client into ``server._client`` so the MCP tools use it."""
    monkeypatch.setattr(server, "_client", live_client)
    return live_client


@pytest.fixture
async def populated_board_id(_inject_live_client: KanbanToolClient) -> int:
    """Discover any board on the test account that has at least one non-archived task.

    The integration suite validates wire-contract shapes, not the contents of
    a specific board, so we don't pin a particular id. Skip if the account has
    no usable board — the maintainer needs to seed one before these tests can run.
    """
    boards = await list_boards()
    for board in boards:
        response = await search_tasks(query="archived:false", board_id=board.id)
        if response.results:
            return board.id
    pytest.skip(
        "no board with non-archived tasks on the test account; "
        "seed at least one board with one task before running live tests."
    )
