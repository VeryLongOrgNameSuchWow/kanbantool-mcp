"""Tests for the ``kanbantool-mcp --check`` first-success-signal flag."""

from __future__ import annotations

import httpx
import pytest
import respx

from kanbantool_mcp.__main__ import (
    _EXIT_AUTH_FAILED,
    _EXIT_HTTP_FAILED,
    _EXIT_MALFORMED_RESPONSE,
    _EXIT_MISSING_ENV,
    _EXIT_OK,
    _EXIT_TRANSPORT_FAILED,
    main,
)

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
        assert exc_info.value.code == _EXIT_OK
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
        assert exc_info.value.code == _EXIT_OK
    captured = capsys.readouterr()
    assert "user #7" in captured.out


def test_check_missing_env_var_exits_with_pinned_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing env var: exit with the pinned ``_EXIT_MISSING_ENV`` code, the
    var name, and the token-regen pointer."""
    monkeypatch.delenv("KANBANTOOL_API_TOKEN")
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == _EXIT_MISSING_ENV
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
    assert exc_info.value.code == _EXIT_MISSING_ENV
    captured = capsys.readouterr()
    assert "KANBANTOOL_DOMAIN" in captured.err
    assert "KANBANTOOL_API_TOKEN" in captured.err


def test_check_401_exits_with_pinned_auth_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """401 from ``whoami``: exit with the pinned ``_EXIT_AUTH_FAILED`` code
    and the actionable token-regen hint."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(401, text="unauthorized")
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == _EXIT_AUTH_FAILED
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "Auth failed" in captured.err
    assert "KANBANTOOL_API_TOKEN" in captured.err


def test_check_403_exits_with_pinned_auth_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """403 from ``whoami``: same auth-hint surface and same pinned exit code
    as 401 (both are ``KanbanToolPermissionError``)."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(403, text="forbidden")
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == _EXIT_AUTH_FAILED
    captured = capsys.readouterr()
    assert "Auth failed" in captured.err


def test_check_transport_error_exits_with_pinned_transport_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A network failure surfaces as ``KanbanToolTransportError``; pin the
    ``_EXIT_TRANSPORT_FAILED`` code and assert the full actionable hint
    string is emitted (not just the bare host substring — that would be
    flagged by CodeQL's URL-substring-sanitization rule, plus a tighter
    match catches accidental wording regressions)."""
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
        assert exc_info.value.code == _EXIT_TRANSPORT_FAILED
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    # The full hint string is more precise than checking for the host
    # substring alone — pins both the host AND the actionable advice that
    # follows it.
    assert "Network failed reaching testacct.kanbantool.com." in captured.err
    assert "Check KANBANTOOL_DOMAIN" in captured.err


def test_check_404_prints_domain_wrong_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo'd ``KANBANTOOL_DOMAIN`` resolves to a wildcard
    ``*.kanbantool.com`` host that 404s on every path. Surface that as a
    typed ``KanbanToolHTTPError`` with a domain-wrong hint, exit
    ``_EXIT_HTTP_FAILED`` — NOT a Python stack trace through the operator."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(404, text="not found")
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == _EXIT_HTTP_FAILED
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "404" in captured.err
    assert "KANBANTOOL_DOMAIN is wrong" in captured.err


def test_check_500_prints_service_down_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """5xx from ``whoami``: surface the typed error with a service-down hint,
    exit ``_EXIT_HTTP_FAILED``."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock() as router:
        # 500 (no retry on this path because the endpoint is GET — the
        # retry policy will do one more attempt; mock returns 500 both times).
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(500, text="internal error")
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == _EXIT_HTTP_FAILED
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "500" in captured.err
    assert "service may be down" in captured.err


def test_check_unexpected_4xx_prints_generic_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 410 (or any other unexpected 4xx outside 401/403/404): surface the
    typed error with the generic catch-all hint, exit ``_EXIT_HTTP_FAILED``.
    Avoids the Python-stack-trace fall-through the /review flagged."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(410, text="gone")
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == _EXIT_HTTP_FAILED
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "410" in captured.err


def test_check_malformed_response_body_prints_bug_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 200 with a body that doesn't match the ``User`` shape (missing
    required ``id`` field): surface the ``pydantic.ValidationError`` as a
    typed failure with a "file an issue" hint, exit
    ``_EXIT_MALFORMED_RESPONSE``. Without this catch the operator would see
    a raw pydantic stack trace."""
    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--check"])
    with respx.mock() as router:
        router.get(f"{BASE_URL}users/current.json").mock(
            return_value=httpx.Response(200, json={"name": "no-id-field"})
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == _EXIT_MALFORMED_RESPONSE
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "doesn't match" in captured.err
    assert "file an issue" in captured.err


def test_check_fail_helper_scrubs_bearer_tokens(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Defense-in-depth: the ``_fail`` helper that prints the ``FAIL: <exc>``
    stderr line MUST scrub ``Bearer <token>`` sequences from the exception
    message before printing.

    Realistic leak path today is zero (the typed ``KanbanToolError`` subclasses
    already scrub their own ``__str__`` output, and ``RuntimeError`` from
    ``Config.from_env`` lists missing env-var names — never tokens). But the
    audit brief required defense-in-depth at the entry-point boundary too,
    so a future exception subclass that surfaces a bearer-token in its
    message can't bypass the scrub.

    Verified by directly invoking ``_fail`` with a synthetic exception
    that intentionally carries a ``Bearer …`` substring in ``str(exc)``."""
    from kanbantool_mcp.__main__ import _fail

    _fail(RuntimeError("upstream echo: Authorization: Bearer leaked-token-xyz"), "hint")
    captured = capsys.readouterr()
    assert "leaked-token-xyz" not in captured.err
    assert "Bearer ***" in captured.err
    assert "hint" in captured.err


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
    # ``--version`` should also be discoverable from --help.
    assert "--version" in captured.out


def test_version_flag_prints_version_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--version`` (locked flag name; future-proof) prints
    ``kanbantool-mcp <version>`` and exits 0. Sourced via
    ``importlib.metadata.version`` so the wheel's metadata is the
    single source of truth — the test accepts either the source-tree
    ``__version__`` (when installed) or the literal ``"unknown"``
    (when running from a non-installed checkout)."""
    from kanbantool_mcp import __version__ as src_version

    monkeypatch.setattr("sys.argv", ["kanbantool-mcp", "--version"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    # argparse's ``version`` action prints to stdout and includes the prog name.
    assert "kanbantool-mcp" in captured.out
    out = captured.out.strip()
    assert out.endswith(src_version) or out.endswith("unknown"), (
        f"Expected output ending with {src_version!r} or 'unknown', got {out!r}"
    )
