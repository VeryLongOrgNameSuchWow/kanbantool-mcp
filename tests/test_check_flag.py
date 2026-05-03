"""Tests for the ``kanbantool-mcp --check`` first-success-signal flag."""

from __future__ import annotations

import httpx
import pytest
import respx

from kanbantool_mcp.__main__ import main

from .conftest import BASE_URL


def test_check_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Happy path: env is set, ``whoami`` 200s, exit 0 with the OK signal."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(
                200, json={"id": 42, "name": "Alice Example", "initials": "AE"}
            )
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "OK:" in captured.out
    assert "Alice Example" in captured.out
    assert "(testacct)" in captured.out
    assert "you can use kanbantool-mcp now" in captured.out
    assert captured.err == ""


def test_check_success_user_without_name_falls_back_to_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If the API returns a user with no ``name``, render ``user #<id>``."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, json={"id": 7})
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "user #7" in captured.out


def test_check_missing_env_var_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing env var: exit non-zero with the var name AND the token-regen
    pointer so the operator knows where to fix it."""
    monkeypatch.delenv("KANBANTOOL_API_TOKEN")
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    # The missing var must be named explicitly so the operator knows what to set.
    assert "KANBANTOOL_API_TOKEN" in captured.err
    assert "API tokens" in captured.err


def test_check_missing_both_env_vars_lists_both(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing both vars: both are named so the operator fixes both."""
    monkeypatch.delenv("KANBANTOOL_DOMAIN")
    monkeypatch.delenv("KANBANTOOL_API_TOKEN")
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "KANBANTOOL_DOMAIN" in captured.err
    assert "KANBANTOOL_API_TOKEN" in captured.err


def test_check_401_prints_auth_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """401 from ``whoami``: exit non-zero with the actionable token-regen hint."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(401, text="unauthorized")
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "Auth failed" in captured.err
    assert "KANBANTOOL_API_TOKEN" in captured.err


def test_check_403_prints_auth_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """403 from ``whoami``: same auth-hint surface as 401 (both are
    ``KanbanToolPermissionError``)."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(403, text="forbidden")
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "Auth failed" in captured.err


def test_check_transport_error_prints_network_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A network failure surfaces as ``KanbanToolTransportError``; the
    actionable hint names the host and points at firewall/DNS as the likely
    cause."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            side_effect=[
                httpx.ConnectError("dns failure"),
                httpx.ConnectError("dns failure"),
            ]
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "Network failed reaching" in captured.err
    assert "testacct.kanbantool.com" in captured.err


def test_main_no_args_invokes_server_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default behavior (no ``--check`` flag): falls through to ``server.run``,
    starting the FastMCP stdio server. Verified by patching ``run`` and
    asserting it was called exactly once."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp"])
    calls: list[bool] = []

    def _fake_run() -> None:
        calls.append(True)

    monkeypatch.setattr("kanbantool_mcp.__main__.run", _fake_run)
    main()
    assert calls == [True]


def test_check_flag_is_documented_in_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--help`` mentions ``--check`` so first-time users discover the flag."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    # argparse exits 0 on --help.
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "--check" in captured.out
