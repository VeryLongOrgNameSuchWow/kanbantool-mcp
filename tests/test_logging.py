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
from kanbantool_mcp.exceptions import KanbanToolHTTPError

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


@pytest.mark.parametrize("level", ["debug", "Info", "warning", "ErRoR"])
def test_lowercase_or_mixed_case_level_is_accepted(
    monkeypatch: pytest.MonkeyPatch, reload_client_module, level: str
) -> None:
    # The env value is normalised via ``.strip().upper()`` before checking
    # against the canonical Python logging level names, so ``debug`` /
    # ``Info`` round-trip to valid handlers. Locked here so a future
    # refactor that drops the ``.upper()`` pass surfaces immediately.
    monkeypatch.setenv("KANBANTOOL_LOG_LEVEL", level)
    reload_client_module()
    assert _kanbantool_handlers(client_module.logger) != []


@pytest.mark.parametrize("level", ["trace", "verbose", "VERBOSE", "", "  ", "9", "info!"])
def test_unknown_level_silently_ignored(
    monkeypatch: pytest.MonkeyPatch, reload_client_module, level: str
) -> None:
    # Anything outside the canonical logging names — a typo, a spelling
    # from another framework's level set, an empty string, a numeric
    # level — falls back to the silent NullHandler. Never break the
    # server because someone typo'd the env var.
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


async def test_debug_body_excerpt_escapes_control_chars(
    client: KanbanToolClient, caplog: pytest.LogCaptureFixture
) -> None:
    # A response body containing raw CR/LF or ANSI escape sequences could
    # forge fake log lines or hijack the operator's terminal (e.g.
    # ``\x1b[2J`` clears the screen). The DEBUG path runs the excerpt
    # through ``repr()`` so control chars surface as their escape
    # sequences (``\\r``, ``\\n``, ``\\x1b[2J``) — visible but inert.
    #
    # Strict JSON forbids unescaped control chars inside strings (RFC 8259
    # §7), but a misbehaving upstream proxy or a non-conforming API could
    # still ship them — this test models that adversarial case. The body
    # is invalid JSON so ``request()`` raises after the DEBUG log fires,
    # which is the path we actually want to assert on; we catch the
    # downstream error and inspect ``caplog``.
    payload = '"line1\r\nline2 \x1b[2J cleared"'
    with (
        caplog.at_level(logging.DEBUG, logger="kanbantool_mcp.client"),
        respx.mock(assert_all_called=True) as router,
    ):
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, text=payload),
        )
        # Non-JSON body: ``request()`` raises ``KanbanToolHTTPError`` after
        # the DEBUG log has already fired, which is the path we want here.
        with pytest.raises(KanbanToolHTTPError):
            await client.request("GET", "users/current")

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    msg = debug_records[0].getMessage()
    # The raw control chars must NOT appear unescaped in the rendered line.
    assert "\r" not in msg
    assert "\n" not in msg
    assert "\x1b" not in msg
    # The escaped form is present (locks the chosen ``repr()`` strategy —
    # if a future refactor switches to a different escaper, update this).
    assert "\\r" in msg
    assert "\\n" in msg
    assert "\\x1b" in msg


async def _noop_async_sleep(_: float) -> None:
    """Drop-in replacement for ``asyncio.sleep`` so retry tests don't pay
    wall-clock latency. Mirrors the signature so monkeypatching is safe."""


async def test_debug_logs_post_retry_response_not_intermediate_5xx(
    client: KanbanToolClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The retry block (PR #145) sits BETWEEN the INFO log and the DEBUG
    # log inside ``request()``. After a GET 5xx → retry → 200, only the
    # final response should land in the DEBUG log; the intermediate 503
    # is invisible (correct: we'd otherwise mislead an operator into
    # thinking the call failed when it actually succeeded on retry).
    monkeypatch.setattr("kanbantool_mcp.client.asyncio.sleep", _noop_async_sleep)

    with (
        caplog.at_level(logging.DEBUG, logger="kanbantool_mcp.client"),
        respx.mock(assert_all_called=True) as router,
    ):
        router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.Response(503, text="upstream blip"),
                httpx.Response(200, json={"id": 1, "name": "Alice"}),
            ]
        )
        await client.request("GET", "users/current")

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debug_records) == 1
    msg = debug_records[0].getMessage()
    assert "[200]" in msg
    assert "Alice" in msg
    # The intermediate 503 body must not surface — only the post-retry
    # response is logged.
    assert "[503]" not in msg
    assert "upstream blip" not in msg


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
