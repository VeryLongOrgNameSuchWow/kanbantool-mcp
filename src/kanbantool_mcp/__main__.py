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

from .client import KanbanToolClient
from .config import Config
from .exceptions import KanbanToolPermissionError, KanbanToolTransportError
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
        print(f"FAIL: {exc}", file=sys.stderr)
        print(
            "Set both KANBANTOOL_DOMAIN (your account subdomain) and "
            "KANBANTOOL_API_TOKEN (bearer token) in the MCP client's `env` block, "
            f"then re-run --check. Get a token at: {_TOKEN_REGEN_HINT}",
            file=sys.stderr,
        )
        return 2

    client = KanbanToolClient(config)
    try:
        try:
            data = await client.request("GET", "users/current")
        except KanbanToolPermissionError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            print(
                f"Auth failed. Verify KANBANTOOL_API_TOKEN at {_TOKEN_REGEN_HINT}",
                file=sys.stderr,
            )
            return 3
        except KanbanToolTransportError as exc:
            host = httpx.URL(config.base_url).host
            print(f"FAIL: {exc}", file=sys.stderr)
            print(
                f"Network failed reaching {host}. "
                "Check KANBANTOOL_DOMAIN, your firewall/proxy settings, and "
                "DNS resolution. Re-run --check once the network reaches "
                "kanbantool.com.",
                file=sys.stderr,
            )
            return 4
        # Validate the response shape minimally — same model as the ``whoami``
        # MCP tool uses, so a malformed body surfaces the same way an LLM would
        # see it via the live tool.
        user = User.model_validate(data)
        display_name = user.name or f"user #{user.id}"
        print(
            f"OK: {display_name} ({config.domain}) — token resolves; you can use kanbantool-mcp now"
        )
        return 0
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
