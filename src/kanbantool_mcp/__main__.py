"""Console script entry point for kanbantool-mcp.

Three modes:

- Default (no args): runs the FastMCP stdio server. The MCP client launches
  this and talks JSON-RPC over the process's stdin/stdout.
- ``--check``: validates the env vars, hits ``whoami``, prints a one-line
  signal, and exits. Intended as the first thing you run after wiring the
  server into an MCP client to confirm the token resolves and the network
  reaches Kanban Tool. Exits 0 on success, non-zero on failure.
- ``--version``: prints the installed package version and exits.

``--check`` and ``--version`` are flags, not subcommands, on purpose: they
keep the v1.0 CLI surface tight (one entry point, two optional behavior
switches). Adding a subcommand would imply more subcommands later — we'd
rather grow that surface only if real demand shows up.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from importlib.metadata import PackageNotFoundError, version

import httpx
from pydantic import ValidationError as PydanticValidationError

from .client import KanbanToolClient, _scrub_secrets
from .config import Config
from .exceptions import (
    KanbanToolHTTPError,
    KanbanToolPermissionError,
    KanbanToolTransportError,
)
from .models import User
from .server import run


def _resolve_version() -> str:
    """Return the installed package version, or ``"unknown"`` if the package
    isn't installed (e.g. running from a checked-out source tree without
    ``uv pip install -e .``). ``importlib.metadata`` is the canonical source
    — single source of truth shared with PyPI / wheel metadata, so a
    release-please version bump on ``__init__.py.__version__`` flows here
    automatically once the wheel rebuilds."""
    try:
        return version("kanbantool-mcp")
    except PackageNotFoundError:
        return "unknown"


_TOKEN_REGEN_HINT = (
    "https://kanbantool.com/ -> log in -> account avatar -> My profile -> API tokens"
)

# Exit codes for ``--check`` failure paths. Pinned (rather than just "non-zero")
# so callers — humans grepping logs, install scripts, CI gates — can branch on
# the cause without parsing the stderr text. Kept locally as constants because
# they're scoped to this entry point; promoting them to ``exceptions.py`` would
# imply a wider re-use that doesn't exist yet.
_EXIT_OK = 0
_EXIT_MISSING_ENV = 2
_EXIT_AUTH_FAILED = 3
_EXIT_TRANSPORT_FAILED = 4
_EXIT_HTTP_FAILED = 5
_EXIT_MALFORMED_RESPONSE = 6


def _fail(exc: BaseException, hint: str) -> None:
    """Print a ``FAIL: <scrubbed-exception> / <hint>`` block to stderr.

    The exception message is run through ``_scrub_secrets`` defensively. The
    typed Kanban Tool exceptions already scrub bearer-token sequences in their
    ``__str__`` output, but a future exception subclass — or a stray
    ``RuntimeError`` from somewhere we don't expect — could surface an
    un-scrubbed token. Cheap belt-and-suspenders that the audit brief
    explicitly required for the ``--check`` flow.
    """
    # CodeQL flags this as ``py/clear-text-logging-sensitive-data``; the
    # expression IS sanitized by ``_scrub_secrets`` (the project's
    # centralized bearer-token regex scrubber, applied to every error
    # surface — see ``client._scrub_secrets``) but CodeQL's taint tracker
    # doesn't recognize the custom regex sanitizer. Dismissed as a false
    # positive (alert #3 — see
    # https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/security/code-scanning/3).
    print(f"FAIL: {_scrub_secrets(str(exc))}", file=sys.stderr)
    print(hint, file=sys.stderr)


async def _run_check() -> int:
    """Resolve the configured token against ``whoami`` and print a one-line
    signal. Returns the exit code (0 on success, non-zero on failure).

    Pulled into its own coroutine so the sync ``main`` entry point can await
    it via ``asyncio.run`` without juggling a fresh event loop inline."""
    try:
        config = Config.from_env()
    except RuntimeError as exc:
        # ``Config.from_env`` already names the missing var(s) in its message;
        # add the actionable next step so the operator knows what to do.
        _fail(
            exc,
            "Set both KANBANTOOL_DOMAIN (your account subdomain) and "
            "KANBANTOOL_API_TOKEN (bearer token) in the MCP client's `env` "
            f"block, then re-run --check. Get a token at: {_TOKEN_REGEN_HINT}",
        )
        return _EXIT_MISSING_ENV

    client = KanbanToolClient(config)
    try:
        try:
            data = await client.request("GET", "users/current")
        except KanbanToolPermissionError as exc:
            _fail(exc, f"Auth failed. Verify KANBANTOOL_API_TOKEN at {_TOKEN_REGEN_HINT}")
            return _EXIT_AUTH_FAILED
        except KanbanToolTransportError as exc:
            host = httpx.URL(config.base_url).host
            _fail(
                exc,
                f"Network failed reaching {host}. Check KANBANTOOL_DOMAIN, "
                "your firewall/proxy settings, and DNS resolution. Re-run "
                "--check once the network reaches kanbantool.com.",
            )
            return _EXIT_TRANSPORT_FAILED
        except KanbanToolHTTPError as exc:
            # Catches the full 4xx/5xx surface that isn't 401/403. Most likely
            # cause: a typo'd ``KANBANTOOL_DOMAIN`` resolves to a wildcard
            # ``*.kanbantool.com`` host that 404s on every path. Also catches
            # ``KanbanToolValidationError`` (subclass) — unlikely from
            # ``whoami`` but kept generic to avoid leaking through unhandled.
            if exc.status_code == 404:
                hint = (
                    f"Got 404 from {config.base_url}users/current.json. "
                    "Likely cause: KANBANTOOL_DOMAIN is wrong (the subdomain "
                    "doesn't exist). Verify the subdomain you log into matches "
                    "what's in KANBANTOOL_DOMAIN."
                )
            elif exc.status_code >= 500:
                hint = (
                    f"Kanban Tool API returned {exc.status_code}. The service "
                    "may be down or having issues; check kanbantool.com status "
                    "and re-run --check shortly."
                )
            else:
                hint = (
                    f"Kanban Tool API returned an unexpected HTTP "
                    f"{exc.status_code}. Inspect the FAIL: line above for "
                    "details and re-run --check after addressing the cause."
                )
            _fail(exc, hint)
            return _EXIT_HTTP_FAILED
        # Validate the response shape minimally — same model as the ``whoami``
        # MCP tool uses, so a malformed body surfaces the same way an LLM would
        # see it via the live tool.
        try:
            user = User.model_validate(data)
        except PydanticValidationError as exc:
            _fail(
                exc,
                "Kanban Tool API returned a body that doesn't match the "
                "expected `User` shape — likely a kanbantool-mcp bug or an "
                "upstream API change. Please file an issue at "
                "https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/issues "
                "with this output.",
            )
            return _EXIT_MALFORMED_RESPONSE
        display_name = user.name or f"user #{user.id}"
        print(
            f"OK: {display_name} ({config.domain}) — token resolves; you can use kanbantool-mcp now"
        )
        return _EXIT_OK
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kanbantool-mcp",
        description=(
            "MCP server bridging an MCP client (Claude Code, Cursor, etc.) to "
            "the Kanban Tool API v3. With no flags, runs the stdio server. "
            "Use --check to validate your env + token before wiring the server "
            "into a client; --version prints the installed package version."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_resolve_version()}",
        help="Print the installed kanbantool-mcp version and exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Validate KANBANTOOL_DOMAIN/KANBANTOOL_API_TOKEN and confirm the "
            "token resolves against the Kanban Tool API. Prints a one-line "
            "signal and exits (0 on success, non-zero on failure). Does NOT "
            "start the MCP server."
        ),
    )
    args = parser.parse_args()

    if args.check:
        # ``asyncio.run`` builds and tears down a fresh event loop for the
        # short-lived check; the long-running stdio server uses its own loop
        # via ``mcp.run`` below.
        sys.exit(asyncio.run(_run_check()))

    run()


if __name__ == "__main__":
    main()
