"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _kanbantool_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBANTOOL_DOMAIN", "testacct")
    monkeypatch.setenv("KANBANTOOL_API_TOKEN", "test-token")
