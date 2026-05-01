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

Write tools (M2):
- `create_task`
- `update_task`
- `move_task`
- `archive_task`
- `add_comment`
- `add_subtask`
- `list_subtasks`
- `update_subtask`
- `delete_subtask`
- `reorder_subtasks`

User discovery tools (post-M3):
- `whoami` — current user (resolves "me" / "myself")
- `get_user` — fetch one user by id
- `list_board_collaborators` — board's user roster (no bulk list-users endpoint exists)

M4 — Completeness:
- `list_custom_field_definitions` — per-board metadata for the 15 `custom_field_*` slots (label, type, enabled state); pair with `Task.custom_fields[custom_field_N]` for values
- `start_timer` — start a per-user time tracker on a task (POST `/time_trackers.json` with `board_id`+`task_id`)
- `stop_timer` — stop a timer (PUT, `ended_at` defaults to "now")
- `delete_timer` — hard-delete a timer
- `list_my_timers` — current user's time trackers across all tasks

M5 — Custom-field writes & comment polish:
- `set_custom_field` — set or clear one of the 15 `custom_field_N` slots on a task. `value=None` clears (sends literal `null` on the wire — does NOT route through `_patch_task` because that helper has None-skip "omit, don't clear" semantics).
- `delete_comment` — soft-delete a comment on a task. Returns the deleted `Comment` with `deleted_at` populated; mirrors the `delete_subtask` shape. The Kanban Tool API has no edit endpoint for comments (verified via spike: PUT/PATCH/POST-with-`_method` overrides all 404), so "fixing" a comment means delete + re-post. While in here, also fixed a P0 wire-field bug in `add_comment` — the body field is `content`, not `text` (every pre-fix call 422'd `Content can't be blank`); the `Comment` model now declares `content: str` directly.

## Kanban Tool API quirks

- Per-account base URL: `https://{KANBANTOOL_DOMAIN}.kanbantool.com/api/v3/`. There is no global host.
- Bearer-token auth.
- Every endpoint requires a `.json` extension in the path (e.g. `/boards/123.json`, not `/boards/123`).
- No "list all boards" endpoint. Read `boards[]` from `/users/current.json` instead.
- No webhook support. Polling `/boards/:id/changelog.json` is the only way to detect changes.
- `search_tasks` accepts a rich DSL — preserve the user's query string verbatim and pass through.

## Phases

- **M0** — Scaffold: package layout, CI, license, docs. *Complete.*
- **M1** — Read tools: list_boards, get_board, search_tasks, get_task, recent_changes. *Complete.*
- **M2** — Write tools: create_task, update_task, move_task, archive_task, add_comment, add_subtask, list_subtasks. *Complete.*
- **M3** — Polish & release: error messages, retries, docs pass, PyPI publish. *Complete.*
- **M4** — Completeness: custom-field reads + time-tracker tools. *Complete.*
- **M5** — Custom-field writes + comment polish: `set_custom_field`, `delete_comment` (the API has no comment-edit endpoint), and the `add_comment` wire-field bugfix (`text` → `content`). *Complete.*
- **M6** — v1.0 readiness: SemVer commitment policy, README pass, error-message audit, RELEASING.md updates. *In progress.*

## Codebase conventions

- **Client singleton** — `server._get_client()` is a lazy module-level singleton (`_client: KanbanToolClient | None`). The stdio MCP runs on a single asyncio loop on a single thread, so no lock around init. Tools call `_get_client()` per request; tests overwrite `server._client` via the `_inject_client` fixture.
- **Test fixtures** — `config`, `client`, `_inject_client`, and `BASE_URL` live in `tests/conftest.py`. Test files consume them by parameter name; don't re-instantiate `Config` or `KanbanToolClient` inline. Tests are offline only — mock HTTP with `respx`.
- **Secret scrubbing** — `client._scrub_secrets` runs `(?i)\bbearer\s+\S+` → `Bearer ***`. Applied to every error branch's `body_excerpt`, to 422 `field_errors` keys/values, and again in `KanbanToolValidationError.__str__` as belt-and-suspenders. Module-private and deliberately single-pattern: bearer auth is the only realistic leak path on this API.
- **Pydantic models** — every model is `BaseModel` with `model_config = ConfigDict(extra="ignore")` (forward-compat with API additions). For fields that shadow Python builtins or use a different wire name, use `Field(alias="<wire>", default=None)` plus `populate_by_name=True` (e.g. `type_`, `lane_id` ↔ `workflow_stage_id`).
- **Error surface** — typed ladder: `KanbanToolError` → `KanbanToolPermissionError`, `KanbanToolHTTPError` → `KanbanToolValidationError`, `KanbanToolTransportError`. All write tools route 4xx/5xx through `client._raise_for_status`; tools propagate the typed exception unchanged. Raw `pydantic.ValidationError` on response decode is a known gap (#28).
- **`_patch_task` helper** — `server._patch_task(task_id, fields, *, method="PUT")` backs both `update_task` and `move_task`. Owns the `{"task": {...}}` envelope, outbound rename via `_PATCH_TASK_RENAMES`, None-skip ("omit, don't clear"), empty-fields `ValueError`, and `Task.model_validate`. To add a new caller-facing alias, extend `_PATCH_TASK_RENAMES`.
