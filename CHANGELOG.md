# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

## [0.1.0] - 2026-05-01

Initial release.

### Added

- **MCP server** (`kanbantool-mcp` console entry point) bridging Claude Code to the
  [Kanban Tool API v3](https://kanbantool.com/developer/api-v3) over stdio.
- **Read tools** — `list_boards`, `get_board`, `search_tasks` (with the full DSL
  passed verbatim), `get_task`, `recent_changes` (changelog poller; the API has
  no webhooks).
- **Write tools** — `create_task`, `update_task`, `move_task`, `archive_task`,
  `add_comment`, `add_subtask`, `list_subtasks`. Single shared `_patch_task`
  helper backs `update_task` and `move_task`.
- **Typed error ladder** — `KanbanToolError` → `KanbanToolPermissionError`
  (401/403), `KanbanToolHTTPError` → `KanbanToolValidationError` (422 with
  parsed `field_errors`), `KanbanToolTransportError`. Actionable hints
  prepended to 404/5xx messages.
- **Pydantic models** for every Kanban Tool resource the tools surface, with
  `extra="ignore"` for forward-compat against API additions, inbound aliases
  for renamed fields (`Column.workflow_stages` → `columns`,
  `Task.workflow_stage_id` → `lane_id`), and computed `is_archived` /
  `is_blocked` flags on `Task`.
- **Live integration test workflow** (`workflow_dispatch` only) hitting a real
  Kanban Tool account against the seeded data. Fails fast when repo secrets
  are missing.
- **CI matrix** on Python 3.11/3.12/3.13, with ruff + ty + pytest gating every
  PR. SHA-pinned third-party Actions; Dependabot-managed (Python + Actions
  weekly, grouped).
- **Documentation** — README with badges, install, tool reference, and three
  end-to-end usage transcripts in `examples/`.

### Configuration

- Two environment variables: `KANBANTOOL_DOMAIN` (account subdomain) and
  `KANBANTOOL_API_TOKEN` (bearer token).
- Distributed via `uv` / `uvx`; supports Python 3.11+.

### Security

- Bearer-token scrubbing (`Bearer ***`) applied to every error-body excerpt,
  422 field-error keys/values, and validation-error string output. Defends the
  one realistic upstream-echo path.
- Explicit guidance in the README and `SECURITY.md` against committing tokens
  to source control; `.gitignore` strengthened with the usual secret-bearing
  patterns (`.env`, `*.pem`, `*.key`, `id_rsa`, `*.kdbx`).

### Known limitations

- `recent_changes` returns the full history when called without `since`. Pass
  the timestamp of the most recent entry on follow-up calls; poll at
  30–120s cadence, not per-keystroke.
- Subtasks are returned inline on `Task.subtasks` whenever a task is fetched.
  `list_subtasks` is sugar for callers that only want the list — same one
  HTTP call as `get_task` underneath.
- `Column.name` is nullable for the synthetic root stage that parents the
  real columns; consumers wanting only display columns filter by `parent_id`.

[Unreleased]: https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/releases/tag/v0.1.0
