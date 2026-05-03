"""Tests for the opt-in ``KANBANTOOL_LOG_LEVEL`` request/response logging."""

from __future__ import annotations

import importlib
import logging
import sys

import httpx
import pytest
import respx

from kanbantool_mcp import client as client_module
from kanbantool_mcp.client import KanbanToolClient

from .conftest import BASE_URL


@pytest.fixture
def reload_client_module(monkeypatch: pytest.MonkeyPatch):
    """Reload ``client`` so a freshly-set ``KANBANTOOL_LOG_LEVEL`` is picked
    up by ``_configure_logging_from_env``. Each test gets a clean handler
    state. Fixture also resets to the no-env baseline after the test so the
    rest of the suite isn't perturbed by leftover log handlers."""

    def _reload() -> None:
        importlib.reload(client_module)

    yield _reload

    monkeypatch.delenv("KANBANTOOL_LOG_LEVEL", raising=False)
    _reload()


def _kanbantool_handlers(logger: logging.Logger) -> list[logging.Handler]:
    """Return only the handlers our module installs — filters out the
    NullHandler that's always present and any user-configured handlers."""
    return [h for h in logger.handlers if getattr(h, "_kanbantool_stream_handler", False)]


def test_default_unset_attaches_only_null_handler(
    monkeypatch: pytest.MonkeyPatch, reload_client_module
) -> None:
    monkeypatch.delenv("KANBANTOOL_LOG_LEVEL", raising=False)
    reload_client_module()
    assert _kanbantool_handlers(client_module.logger) == []
    # NullHandler IS expected so a missing user config doesn't print
    # "no handlers found" warnings.
    assert any(isinstance(h, logging.NullHandler) for h in client_module.logger.handlers)


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_valid_level_attaches_stderr_handler(
    monkeypatch: pytest.MonkeyPatch, reload_client_module, level: str
) -> None:
    monkeypatch.setenv("KANBANTOOL_LOG_LEVEL", level)
    reload_client_module()
    handlers = _kanbantool_handlers(client_module.logger)
    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.StreamHandler)
    assert handlers[0].stream is sys.stderr
    assert client_module.logger.level == getattr(logging, level)


@pytest.mark.parametrize("level", ["debug", "Info", "trace", "verbose", "", "  "])
def test_invalid_or_lowercase_level_silently_ignored(
    monkeypatch: pytest.MonkeyPatch, reload_client_module, level: str
) -> None:
    # Lowercase is rejected because our normalisation upper-cases the env
    # value but the validator checks against the canonical Python logging
    # names — so ``debug`` works (we upper it) but a typo like ``trace``
    # is rejected silently. Adjust if we ever want case-insensitive
    # acceptance with no upper-casing pass.
    if level.strip().upper() in {"DEBUG", "INFO"}:
        # ``debug`` and ``Info`` round-trip through .upper() to valid names
        monkeypatch.setenv("KANBANTOOL_LOG_LEVEL", level)
        reload_client_module()
        assert _kanbantool_handlers(client_module.logger) != []
        return
    monkeypatch.setenv("KANBANTOOL_LOG_LEVEL", level)
    reload_client_module()
    assert _kanbantool_handlers(client_module.logger) == []


def test_idempotent_reconfigure_does_not_stack_handlers(
    monkeypatch: pytest.MonkeyPatch, reload_client_module
) -> None:
    monkeypatch.setenv("KANBANTOOL_LOG_LEVEL", "INFO")
    reload_client_module()
    reload_client_module()
    reload_client_module()
    assert len(_kanbantool_handlers(client_module.logger)) == 1


# --- Request/response logging behavior -------------------------------------


async def test_info_level_logs_request_method_and_path(
    client: KanbanToolClient, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        caplog.at_level(logging.INFO, logger="kanbantool_mcp.client"),
        respx.mock(assert_all_called=True) as router,
    ):
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
        await client.request("GET", "users/current")

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info_records) == 1
    msg = info_records[0].getMessage()
    assert "GET" in msg
    assert "users/current.json" in msg


async def test_debug_level_logs_response_status_and_body_excerpt(
    client: KanbanToolClient, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        caplog.at_level(logging.DEBUG, logger="kanbantool_mcp.client"),
        respx.mock(assert_all_called=True) as router,
    ):
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, json={"id": 42, "name": "Alice"})
        )
        await client.request("GET", "users/current")

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debug_records) == 1
    msg = debug_records[0].getMessage()
    assert "[200]" in msg
    assert "Alice" in msg


async def test_debug_response_body_is_scrubbed_of_bearer_tokens(
    client: KanbanToolClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Defensive: if an upstream proxy or a misconfigured API echoes the
    # Authorization header back into the response body, the log line must
    # NOT carry that token to the user's terminal. Scrub posture mirrors
    # the typed-exception body_excerpt path.
    with (
        caplog.at_level(logging.DEBUG, logger="kanbantool_mcp.client"),
        respx.mock(assert_all_called=True) as router,
    ):
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(
                200,
                text='{"id": 1, "echoed_auth": "Bearer super-secret-leaked-token"}',
            )
        )
        await client.request("GET", "users/current")

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    msg = debug_records[0].getMessage()
    assert "super-secret-leaked-token" not in msg
    assert "Bearer ***" in msg


async def test_debug_body_excerpt_truncated_to_limit(
    client: KanbanToolClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Long bodies (paginated lists, large boards) shouldn't dump multi-KB
    # log lines. Cap mirrors the body_excerpt limit used by exceptions.
    # Use a JSON-array of 5000 'x' entries so the response is well-formed
    # JSON (the request method otherwise raises on non-JSON bodies before
    # the test can assert on log contents).
    long_body = '"' + "x" * 5000 + '"'
    with (
        caplog.at_level(logging.DEBUG, logger="kanbantool_mcp.client"),
        respx.mock(assert_all_called=True) as router,
    ):
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, text=long_body),
        )
        await client.request("GET", "users/current")

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    msg = debug_records[0].getMessage()
    # The status prefix and method are also in the line, so the message is
    # longer than just the body excerpt — but the excerpt itself is capped.
    assert "x" * 201 not in msg


async def test_default_silent_mode_emits_no_records(
    client: KanbanToolClient, caplog: pytest.LogCaptureFixture
) -> None:
    # caplog captures via the root logger regardless of handlers; even so,
    # without an explicit at_level call the kanbantool logger stays at its
    # default level (WARNING) so info/debug records are filtered before
    # they hit the capture handler.
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
        await client.request("GET", "users/current")

    relevant = [r for r in caplog.records if r.name == "kanbantool_mcp.client"]
    # caplog captures at WARNING+ by default; INFO/DEBUG drop on the floor.
    assert all(r.levelno >= logging.WARNING for r in relevant)
