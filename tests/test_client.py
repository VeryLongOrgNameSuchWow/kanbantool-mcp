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
