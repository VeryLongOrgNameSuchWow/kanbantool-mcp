"""Smoke tests for the M0 scaffold."""

from __future__ import annotations

import pytest

from kanbantool_mcp import __version__
from kanbantool_mcp.config import Config
from kanbantool_mcp.server import ping


def test_version() -> None:
    assert __version__


def test_config_from_env() -> None:
    assert Config.from_env().base_url == "https://testacct.kanbantool.com/api/v3/"


def test_config_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KANBANTOOL_DOMAIN")
    with pytest.raises(RuntimeError, match="KANBANTOOL_DOMAIN"):
        Config.from_env()


def test_ping_returns_pong() -> None:
    assert ping() == "pong"
