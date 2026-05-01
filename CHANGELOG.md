# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1](https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/compare/v0.2.0...v0.2.1) (2026-05-01)


### Bug Fixes

* **release:** escape brackets in CHANGELOG-section awk regex ([#91](https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/issues/91)) ([2d5e695](https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/commit/2d5e695d78a3c6d15e4dcecb74db617a9959ee5a)), closes [#90](https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/issues/90)

## [0.2.0] - 2026-05-01

Tool surface expansion plus a sweep through the error-handling story.

### Added

- **User-discovery tools** (#88) — three new MCP tools that close the
  gap left by the API's lack of a bulk list-users endpoint:
  - `whoami() -> User` — the authenticated user's profile, useful for
    resolving "me" / "myself" in agent prompts to the right
    `assigned_user_id`.
  - `get_user(user_id) -> User` — by-id lookup for confirming role
    flags and active state before assigning.
  - `list_board_collaborators(board_id) -> list[Collaborator]` — thin
    wrapper over `get_board` returning the inline `collaborators`
    roster. The canonical user-discovery surface for assignment
    workflows.
- **`User` and `Collaborator` models** (#88) — surface the
  LLM-meaningful subset of the wire shape; heavy nested fields
  (`account`, `settings`, `customizations`, `groups`) dropped via
  `extra="ignore"`.
- **`Board.collaborators: list[Collaborator]`** (#88) — the inline
  user roster on the detail payload; defaults to `[]` for compact
  list-style payloads that omit it.
- **Additive Task fields from the v3 wire payload** (#87) — surfaces
  ~20 fields previously dropped via `extra="ignore"`:
  - Sizing & estimation: `size_estimate`, `size_estimate_description`,
    `time_estimate`.
  - Visual markers: `card_color`, `card_color_in_rgb`,
    `card_color_invert`, `card_type_id`.
  - Scheduling: `recurring_schedule`, `reminders_schedule` (raw
    dicts; typed wrappers can come when a tool needs them).
  - Search: `search_tags` (list of strings — distinct from the
    comma-separated `tags`).
  - Relationships: `linked_tasks`, `linked_tasks_status`,
    `task_dependencies`, `collaborators`, `attachments`,
    `attachments_count` (collections; raw dicts inside).
  - Provenance: `created_by_id`, `moved_at`, `postponed_until`,
    `subtasks_completed_count`, `external_id`, `external_link`.
- **Live integration coverage** for all the new tools and Task fields,
  bringing the live suite from 5 → 10 tests against the test account.

### Changed

- **Typed error surface for response-decode failures** (#85) — every
  read/write tool now wraps `pydantic.ValidationError` as
  `KanbanToolHTTPError(status_code=200, body_excerpt="malformed X
  payload: ...")` rather than leaking the raw pydantic exception to
  MCP clients. The fix is centralised behind two private helpers
  (`_decode` / `_decode_list`) and replaces 9 near-identical
  try/except sites; the per-tool code is now a one-liner. This
  makes every failure path go through the typed `KanbanToolError`
  ladder consistently.
- **Tool surface 12 → 15** with the new `whoami`, `get_user`,
  `list_board_collaborators` (see Added).

### Fixed

- **Bearer-token leak surface in `body_excerpt`** (#85) — the new
  decode helpers fold a `pydantic.ValidationError.__repr__` (which
  embeds the offending input value) into the wrapped error's
  `body_excerpt`. Without scrubbing, a 2xx response body containing a
  `Bearer …` string (e.g. an upstream proxy / WAF echoing the
  `Authorization` header) would land the token unredacted in the
  excerpt. Centralised the scrub in `KanbanToolHTTPError.__init__`
  so every constructor produces a scrubbed excerpt regardless of
  caller; `client._scrub_secrets` continues to scrub upstream too,
  and double-scrubbing is idempotent.
- **Compact `Board` payloads with no `collaborators` key** (#88) —
  the field defaults to `[]` rather than failing validation.

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

[0.2.0]: https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/releases/tag/v0.1.0
