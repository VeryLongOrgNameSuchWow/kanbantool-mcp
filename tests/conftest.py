"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from kanbantool_mcp import server
from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.config import Config

BASE_URL = "https://testacct.kanbantool.com/api/v3/"


@pytest.fixture(autouse=True)
def _kanbantool_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBANTOOL_DOMAIN", "testacct")
    monkeypatch.setenv("KANBANTOOL_API_TOKEN", "test-token")


@pytest.fixture
def config() -> Config:
    return Config.from_env()


@pytest.fixture
async def client(config: Config) -> AsyncIterator[KanbanToolClient]:
    c = KanbanToolClient(config)
    try:
        yield c
    finally:
        await c.aclose()


@pytest.fixture
def _inject_client(monkeypatch: pytest.MonkeyPatch, client: KanbanToolClient) -> KanbanToolClient:
    monkeypatch.setattr(server, "_client", client)
    return client
