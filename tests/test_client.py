"""Tests for the Kanban Tool HTTP client."""

from __future__ import annotations

import httpx
import pytest
import respx

from kanbantool_mcp.client import KanbanToolClient
from kanbantool_mcp.config import Config
from kanbantool_mcp.exceptions import (
    KanbanToolHTTPError,
    KanbanToolPermissionError,
    KanbanToolTransportError,
)

from .conftest import BASE_URL


async def test_get_returns_parsed_dict(client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, json={"id": 1, "email": "a@b.c"})
        )
        result = await client.request("GET", "users/current")
        assert result == {"id": 1, "email": "a@b.c"}


async def test_401_raises_permission_error_mentions_env_var(client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(401, text="unauthorized")
        )
        with pytest.raises(KanbanToolPermissionError) as exc_info:
            await client.request("GET", "users/current")
        assert "KANBANTOOL_API_TOKEN" in str(exc_info.value)
        assert "test-token" not in str(exc_info.value)


async def test_403_raises_permission_error(client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(f"{BASE_URL}boards/1.json").mock(
            return_value=httpx.Response(403, text="forbidden")
        )
        with pytest.raises(KanbanToolPermissionError):
            await client.request("GET", "boards/1")


async def test_404_raises_http_error_with_body(client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(f"{BASE_URL}boards/999.json").mock(
            return_value=httpx.Response(404, text="not found body")
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request("GET", "boards/999")
        assert exc_info.value.status_code == 404
        assert exc_info.value.body_excerpt
        assert "not found" in exc_info.value.body_excerpt
        rendered = str(exc_info.value)
        assert "no such task/board (or you lack access)" in rendered
        # Path is preserved so the LLM still sees which resource was missing.
        assert "boards/999.json" in rendered


async def test_500_raises_http_error(client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(f"{BASE_URL}boards/1.json").mock(return_value=httpx.Response(500, text="boom"))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request("GET", "boards/1")
        assert exc_info.value.status_code == 500
        rendered = str(exc_info.value)
        assert "Kanban Tool API is having issues; retry shortly." in rendered
        assert "boards/1.json" in rendered


async def test_transport_error_then_success_retries(client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.ConnectError("boom"),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = await client.request("GET", "users/current")
        assert result == {"ok": True}


async def test_transport_error_twice_raises(client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[httpx.ConnectError("boom1"), httpx.ConnectError("boom2")]
        )
        with pytest.raises(KanbanToolTransportError) as exc_info:
            await client.request("GET", "users/current")
        assert isinstance(exc_info.value.__cause__, httpx.TransportError)


async def test_json_suffix_auto_appended(client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await client.request("GET", "users/current")
        assert route.called


async def test_json_suffix_not_double_appended(client: KanbanToolClient) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await client.request("GET", "users/current.json")
        assert route.called


def test_config_repr_does_not_leak_token() -> None:
    cfg = Config.from_env()
    assert "test-token" not in repr(cfg)


async def test_non_json_2xx_raises_http_error(client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, text="<html>oops</html>")
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request("GET", "users/current")
        assert exc_info.value.status_code == 200
        assert "<html>" in exc_info.value.body_excerpt


async def test_500_body_scrubs_bearer_token(client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(f"{BASE_URL}boards/1.json").mock(
            return_value=httpx.Response(500, text="Authorization: Bearer leaked-token-secret-xyz")
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request("GET", "boards/1")
        assert "leaked-token-secret-xyz" not in exc_info.value.body_excerpt
        assert "Bearer ***" in exc_info.value.body_excerpt


async def test_404_with_empty_body(client: KanbanToolClient) -> None:
    with respx.mock() as router:
        router.get(f"{BASE_URL}boards/999.json").mock(return_value=httpx.Response(404, text=""))
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request("GET", "boards/999")
        assert exc_info.value.status_code == 404
        assert exc_info.value.body_excerpt == ""


async def test_request_follows_302_redirect(client: KanbanToolClient) -> None:
    # /users/current.json 302s to the resolved /users/{id}.json on real accounts.
    # httpx must follow the redirect transparently and return the target's JSON.
    target_url = f"{BASE_URL}users/42.json"
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(302, headers={"Location": target_url})
        )
        router.get(target_url).mock(return_value=httpx.Response(200, json={"id": 42}))
        result = await client.request("GET", "users/current")
        assert result == {"id": 42}


async def test_request_follows_relative_redirect(client: KanbanToolClient) -> None:
    # The Kanban Tool API returns a path-only Location; httpx must resolve it
    # against the base URL.
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(302, headers={"Location": "/api/v3/users/42.json"})
        )
        router.get(f"{BASE_URL}users/42.json").mock(
            return_value=httpx.Response(200, json={"id": 42})
        )
        result = await client.request("GET", "users/current")
        assert result == {"id": 42}


# --- Retry policy: GET-only retry on 429 + 5xx (M9 / Proposal #6) ----------
#
# These tests use a ``no_sleep`` fixture to make ``asyncio.sleep`` a no-op so
# the retry tests don't add real wall-clock latency to the suite. The
# *recorded* sleep durations are inspected by the Retry-After tests to assert
# the cap behavior, since that's the only way to verify the policy without
# actually waiting.


@pytest.fixture
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Patch ``asyncio.sleep`` (as imported by client.py) to a no-op that
    records the requested durations. Returns the list so tests can assert on
    the values after the request completes."""
    from kanbantool_mcp import client as client_module

    # Capture the real coroutine BEFORE patching — otherwise ``_fake_sleep``
    # ends up calling itself via the patched module attribute and recurses.
    real_sleep = client_module.asyncio.sleep

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        # Yield control so other coroutines can advance — preserves the
        # await-point semantics of the real ``asyncio.sleep``.
        await real_sleep(0)

    monkeypatch.setattr(client_module.asyncio, "sleep", _fake_sleep)
    return sleeps


async def test_get_retries_once_on_429(
    client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    with respx.mock() as router:
        route = router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "1"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = await client.request("GET", "users/current")
        assert result == {"ok": True}
        assert route.call_count == 2
        assert recorded_sleeps == [1.0]


async def test_get_retries_once_on_503(
    client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    with respx.mock() as router:
        route = router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.Response(503, text="upstream busy"),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = await client.request("GET", "users/current")
        assert result == {"ok": True}
        assert route.call_count == 2
        # 5xx delay is the existing 0.5s shape (matches TransportError retry).
        assert recorded_sleeps == [0.5]


async def test_get_429_then_429_gives_up_after_one_retry(
    client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    """Chained transient failure: confirm we do NOT chain backoff. Two 429s
    in a row should surface the second as ``KanbanToolHTTPError(429)``, not
    spin into a third attempt."""
    with respx.mock() as router:
        route = router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "1"}),
                httpx.Response(429, headers={"Retry-After": "1"}),
            ]
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request("GET", "users/current")
        assert exc_info.value.status_code == 429
        assert route.call_count == 2
        # Only the first retry sleeps; the second 429 surfaces immediately.
        assert recorded_sleeps == [1.0]


async def test_get_5xx_then_5xx_gives_up_after_one_retry(
    client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    """Chained 5xx: same one-per-class policy as 429."""
    with respx.mock() as router:
        route = router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.Response(503, text="boom1"),
                httpx.Response(502, text="boom2"),
            ]
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request("GET", "users/current")
        assert exc_info.value.status_code == 502
        assert route.call_count == 2
        assert recorded_sleeps == [0.5]


async def test_get_429_no_retry_after_header_uses_default_delay(
    client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    with respx.mock() as router:
        route = router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = await client.request("GET", "users/current")
        assert result == {"ok": True}
        assert route.call_count == 2
        # No ``Retry-After`` header → fall back to the 1s default.
        assert recorded_sleeps == [1.0]


async def test_get_429_retry_after_capped_at_5s(
    client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    """A ``Retry-After: 4`` is honored as-is (under the cap)."""
    with respx.mock() as router:
        route = router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "4"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = await client.request("GET", "users/current")
        assert result == {"ok": True}
        assert route.call_count == 2
        assert recorded_sleeps == [4.0]


async def test_get_429_retry_after_over_cap_surfaces_typed_error(
    client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    """A ``Retry-After: 60`` exceeds the 5s cap → no retry, raise immediately
    with the typed 429 error so the agent decides what to do (instead of the
    LLM blocking on a one-minute wait)."""
    with respx.mock() as router:
        route = router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "60"})
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request("GET", "users/current")
        assert exc_info.value.status_code == 429
        assert route.call_count == 1
        assert recorded_sleeps == []


async def test_get_429_unparseable_retry_after_uses_default_delay(
    client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    """Kanban Tool's API uses delta-seconds, but ``Retry-After`` legally
    accepts an HTTP-date too. We don't parse dates — fall back to the default
    rather than waiting forever."""
    with respx.mock() as router:
        route = router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = await client.request("GET", "users/current")
        assert result == {"ok": True}
        assert route.call_count == 2
        assert recorded_sleeps == [1.0]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_writes_do_not_retry_on_429(
    method: str, client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    """At-most-once write semantics: writes NEVER retried on 429, even with
    a ``Retry-After`` header. Surfaces as ``KanbanToolHTTPError(429)`` so the
    agent (LLM) decides whether to re-issue the write itself."""
    with respx.mock() as router:
        route = router.request(method, f"{BASE_URL}tasks/1.json").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "1"})
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request(method, "tasks/1")
        assert exc_info.value.status_code == 429
        assert route.call_count == 1
        assert recorded_sleeps == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_writes_do_not_retry_on_5xx(
    method: str, status: int, client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    """Critical: a transient 503 from a write that *did* land server-side
    must NOT retry — that would double-create the resource. Kanban Tool has
    no idempotency-key support, so this is enforced client-side."""
    with respx.mock() as router:
        route = router.request(method, f"{BASE_URL}tasks/1.json").mock(
            return_value=httpx.Response(status, text="upstream busy")
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request(method, "tasks/1")
        assert exc_info.value.status_code == status
        assert route.call_count == 1
        assert recorded_sleeps == []


async def test_get_retries_only_on_429_and_5xx_not_on_other_4xx(
    client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    """Sanity check: 404 and 422 are NOT retryable. They surface immediately
    via ``_raise_for_status``."""
    with respx.mock() as router:
        route = router.get(f"{BASE_URL}boards/999.json").mock(
            return_value=httpx.Response(404, text="missing")
        )
        with pytest.raises(KanbanToolHTTPError) as exc_info:
            await client.request("GET", "boards/999")
        assert exc_info.value.status_code == 404
        assert route.call_count == 1
        assert recorded_sleeps == []


async def test_get_retry_then_transport_error_raises_transport_error(
    client: KanbanToolClient, recorded_sleeps: list[float]
) -> None:
    """If the retry attempt itself hits a transport error, surface that as
    ``KanbanToolTransportError`` rather than masking it. One retry per error
    class — we don't chain into a transport-retry on top of a 5xx-retry."""
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.Response(503, text="boom"),
                httpx.ConnectError("connection refused"),
            ]
        )
        with pytest.raises(KanbanToolTransportError) as exc_info:
            await client.request("GET", "users/current")
        assert isinstance(exc_info.value.__cause__, httpx.TransportError)
        # The 5xx-retry sleep happened before the connect error was raised.
        assert recorded_sleeps == [0.5]
