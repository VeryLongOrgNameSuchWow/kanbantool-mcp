# CLAUDE.md

## Project

`kanbantool-mcp` is a FastMCP-based MCP server that bridges Claude Code with the Kanban Tool API v3 (https://kanbantool.com/developer/api-v3). Stack: Python 3.11+, FastMCP, httpx, pydantic. Distributed via `uvx`.

Runtime configuration comes from two env vars: `KANBANTOOL_DOMAIN` (account subdomain prefix) and `KANBANTOOL_API_TOKEN` (bearer token).

## Commands

All run through `uv`:

- Run server: `uv run kanbantool-mcp`
- Tests: `uv run pytest`
- Lint: `uv run ruff check .`
- Format check: `uv run ruff format --check .`
- Type check: `uv run ty check`

## Tool surface

The server exposes a curated ~12 tools, not every Kanban Tool endpoint. Surface stays small on purpose — pick the highest-leverage workflows for an LLM agent.

Read tools (M1):
- `list_boards`
- `get_board`
- `search_tasks`
- `get_task`
- `recent_changes`
- `list_subtasks`

Write tools (M2):
- `create_task`
- `update_task`
- `move_task`
- `archive_task`
- `add_comment`
- `add_subtask`

## Kanban Tool API quirks

- Per-account base URL: `https://{KANBANTOOL_DOMAIN}.kanbantool.com/api/v3/`. There is no global host.
- Bearer-token auth.
- Every endpoint requires a `.json` extension in the path (e.g. `/boards/123.json`, not `/boards/123`).
- No "list all boards" endpoint. Read `boards[]` from `/users/current.json` instead.
- No webhook support. Polling `/boards/:id/changelog.json` is the only way to detect changes.
- `search_tasks` accepts a rich DSL — preserve the user's query string verbatim and pass through.

## Phases

- **M0** — Scaffold: package layout, CI, license, docs (this commit).
- **M1** — Read tools: list_boards, get_board, search_tasks, get_task, recent_changes, list_subtasks.
- **M2** — Write tools: create_task, update_task, move_task, archive_task, add_comment, add_subtask.
- **M3** — Polish & release: error messages, retries, docs pass, PyPI publish.

## Testing

Tests are offline only — use `respx` to mock the Kanban Tool HTTP. Don't introduce live-API tests outside the dedicated integration workflow.
