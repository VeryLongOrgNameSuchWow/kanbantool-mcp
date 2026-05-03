# kanbantool-mcp

An MCP server that connects Claude Code (and other MCP clients) to a [Kanban Tool](https://kanbantool.com/) account.

[![CI](https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/kanbantool-mcp.svg)](https://pypi.org/project/kanbantool-mcp/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> **New to MCP?** This is an MCP server — you run it from an MCP-aware assistant. If you've never set one up, install [Claude Code](https://docs.claude.com/en/docs/claude-code), [Claude Desktop](https://claude.ai/download), or [Cursor](https://cursor.sh) first, then come back here.

## Why this exists

Kanban Tool holds the authoritative state of your boards, tasks, and workflow — but an LLM can't see any of it without help. This MCP server gives Claude Code (and any other MCP client) read access to boards and tasks, search via Kanban Tool's query DSL, and write tools to create, update, move, archive, comment on, and break down tasks. The point: stop re-explaining your kanban state to the model on every interaction, and let it act on the board directly when you want it to.

## Status

**Alpha, approaching v1.0.** The 25-tool surface is settled and exercised against a real Kanban Tool account via the `Live Integration` workflow. Pre-1.0 means the surface may still evolve based on real-world feedback — pin a specific version if you need stability across upgrades. See [SEMVER.md](SEMVER.md) for the v1.0 stability commitment (which surfaces are stable, which are not, deprecation policy).

## Roadmap & support

Where the project is going: see the [open milestones](https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/milestones). Larger workstreams are tagged with the [`epic`](https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/issues?q=is%3Aissue+is%3Aopen+label%3Aepic) label.

Maintainer is best-effort and typically responds to issues and PRs within ~a week. If something is blocking you and the silence is longer, a polite bump on the thread is welcome.

## What this looks like

A short illustrative session — the shape of an interaction, not literal terminal output:

```
User:      What's on my Engineering board right now?
Assistant: (calls list_boards)            -> finds "Engineering" with id 4217
           (calls get_board 4217)         -> reads columns: Backlog / Doing / Review / Done
           (calls search_tasks
              query="archived:false",
              board_id=4217)              -> 14 active tasks, grouped by column

           Engineering has 14 active cards. Doing (3): "Migrate auth to OIDC",
           "Flaky CI on macOS", "Add rate-limit headers". Review (2): ...
```

Longer end-to-end walkthroughs (with realistic JSON request/response shapes) live in [`examples/`](examples/).

## Install

### Configuration

Two environment variables, regardless of how you launch the server:

| Variable | What it is | Where to get it |
| --- | --- | --- |
| `KANBANTOOL_DOMAIN` | Your account's subdomain prefix — `acme` for `https://acme.kanbantool.com`. | The URL you log into. |
| `KANBANTOOL_API_TOKEN` | Bearer token for the Kanban Tool API v3. | Profile -> API tokens in your Kanban Tool account. |

### Wiring it into your client

The JSON shape is the same across MCP clients — only the file location and the launcher CLI differ. Pick your client below, drop the snippet into the matching `mcp.json`, and substitute your `KANBANTOOL_DOMAIN` / `KANBANTOOL_API_TOKEN` values.

Each snippet shows the **PyPI** form (`uvx kanbantool-mcp`) — swap the `args` for the **git form** below if you want to track `main` instead of a release:

```json
"args": ["--from", "git+https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp", "kanbantool-mcp"]
```

#### Claude Code

Easiest path is the CLI:

```sh
claude mcp add-json kanbantool '{
  "command": "uvx",
  "args": ["kanbantool-mcp"],
  "env": {
    "KANBANTOOL_DOMAIN": "your-account",
    "KANBANTOOL_API_TOKEN": "your-token"
  }
}'
```

Or edit `~/.claude.json` (project-scoped via `claude mcp add-json -s project ...`). See <https://docs.claude.com/en/docs/claude-code/mcp> for scopes.

#### Claude Desktop

Edit the config file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows). Setup docs: <https://modelcontextprotocol.io/quickstart/user>.

```json
{
  "mcpServers": {
    "kanbantool": {
      "command": "uvx",
      "args": ["kanbantool-mcp"],
      "env": {
        "KANBANTOOL_DOMAIN": "your-account",
        "KANBANTOOL_API_TOKEN": "your-token"
      }
    }
  }
}
```

#### Cursor

Edit `~/.cursor/mcp.json` (or per-project `.cursor/mcp.json`). Setup docs: <https://cursor.com/docs/mcp>.

```json
{
  "mcpServers": {
    "kanbantool": {
      "command": "uvx",
      "args": ["kanbantool-mcp"],
      "env": {
        "KANBANTOOL_DOMAIN": "your-account",
        "KANBANTOOL_API_TOKEN": "your-token"
      }
    }
  }
}
```

#### Continue

Edit `~/.continue/config.yaml` (Continue uses YAML, not JSON, for MCP entries). Setup docs: <https://docs.continue.dev/customize/deep-dives/mcp>.

```yaml
mcpServers:
  - name: kanbantool
    command: uvx
    args:
      - kanbantool-mcp
    env:
      KANBANTOOL_DOMAIN: your-account
      KANBANTOOL_API_TOKEN: your-token
```

#### Cline

Edit `cline_mcp_settings.json` via the Cline panel's MCP Servers > Edit Config button. Setup docs: <https://docs.cline.bot/mcp/configuring-mcp-servers>.

```json
{
  "mcpServers": {
    "kanbantool": {
      "command": "uvx",
      "args": ["kanbantool-mcp"],
      "env": {
        "KANBANTOOL_DOMAIN": "your-account",
        "KANBANTOOL_API_TOKEN": "your-token"
      }
    }
  }
}
```

#### Generic MCP-over-stdio

Any MCP client that supports launching a stdio server. Spawn this command with the env vars set in the child process; the server speaks JSON-RPC on stdin/stdout.

```sh
KANBANTOOL_DOMAIN=your-account \
  KANBANTOOL_API_TOKEN=your-token \
  uvx kanbantool-mcp
```

### Verify your install

Run `kanbantool-mcp --check` (e.g. `uvx kanbantool-mcp --check`) — it validates your env vars, hits the `whoami` endpoint, and prints a one-line OK/FAIL signal. Sample success output:

```
OK: Alice Example (your-account) — token resolves; you can use kanbantool-mcp now
```

The flag exits 0 on success and non-zero on failure (missing env, 401/403 auth, network failure), with an actionable hint per error class. Run it once after wiring the server into your client to confirm the token reaches Kanban Tool **before** asking your assistant to do anything with it.

You can also verify from inside the assistant: ask **"who am I?"** — it'll call the `whoami` tool and confirm your token resolves. If that comes back with your name, the server is reachable and your credentials work.

## Tool reference

| Tool | Purpose | Key params |
| --- | --- | --- |
| `list_boards` | List boards visible to the authenticated user. | — |
| `get_board` | Fetch a board with its columns, swimlanes, and custom-field definitions. | `board_id` |
| `search_tasks` | Search tasks across boards using Kanban Tool's query DSL (e.g. `@alice priority:high tags:bug`). Forwarded to the API verbatim. | `query`, `board_id?`, `limit?`, `page?` |
| `get_task` | Fetch a task by id with headline metadata, subtask/comment counts, and tracked time. | `task_id` |
| `recent_changes` | Fetch the changelog feed for a board — the change-tracking primitive that stands in for webhooks (Kanban Tool ships none). Poll sparingly, always with `since`. | `board_id`, `since?` |
| `create_task` | Create a new task on a board. Optional kwargs are omitted when unset. | `name`, `board_id`, `description?`, `lane_id?`, `priority?`, `tags?`, ... |
| `update_task` | Partial update of an existing task; only kwargs the caller passes are sent. `None` means *omit*, not *clear*. | `task_id`, `name?`, `description?`, `priority?`, ... |
| `move_task` | Move a task between columns, swimlanes, or positions. At least one target must be set. | `task_id`, `column_id?`, `swimlane_id?`, `position?` |
| `archive_task` | Archive a task. Idempotent. | `task_id` |
| `add_comment` | Post a comment on a task. | `task_id`, `content` |
| `delete_comment` | Soft-delete a comment on a task. Returns the deleted comment with `deleted_at` populated. The API has no edit endpoint — delete and re-post if you need to change a comment. | `task_id`, `comment_id` |
| `list_subtasks` | List subtasks attached to a task. | `task_id` |
| `add_subtask` | Add a subtask to a task. | `task_id`, `name` |
| `update_subtask` | Partial update of an existing subtask — mark complete, rename, change assignee. `None` kwargs are omitted. | `subtask_id`, `name?`, `is_completed?`, `assigned_user_id?` |
| `delete_subtask` | Soft-delete a subtask. Returns the deleted subtask with `deleted_at` populated. | `subtask_id` |
| `reorder_subtasks` | Reorder all subtasks under a task. `ids` must include the full set in the desired order. | `task_id`, `ids: list[int]` |
| `whoami` | Fetch the authenticated user's profile — id, role flags, locale. Use to resolve "me" / "myself" in user requests. | — |
| `get_user` | Fetch one user by id. | `user_id` |
| `list_board_collaborators` | List users with access to a board (the canonical user-discovery surface — the API has no bulk list-users endpoint). | `board_id` |
| `list_custom_field_definitions` | List the per-board metadata for the 15 ``custom_field_*`` slots (label, type, enabled state). Use to interpret the values surfaced on `Task.custom_fields`. | `board_id` |
| `set_custom_field` | Set or clear one of the 15 ``custom_field_N`` slots on a task. ``value=None`` clears (sends literal ``null``). | `task_id`, `slot` (1..15), `value` |
| `start_timer` | Start a per-user time tracker on a task. | `task_id`, `board_id` |
| `stop_timer` | Stop a running time tracker. ``ended_at`` defaults to "now" if omitted. | `timer_id`, `ended_at?` |
| `delete_timer` | Delete a time tracker (hard-delete). Returns `None`. | `timer_id` |
| `list_my_timers` | List the authenticated user's time trackers across all tasks. | — |

(`ping` exists as a transport smoke test; not listed above.)

## Examples

A short write-flow alongside the read-flow shown above. Illustrative — shape of a session, not literal terminal output. For longer walkthroughs with realistic JSON shapes, see [`examples/`](examples/).

```
User:      Create a high-priority task in Engineering called "Fix login bug",
           and leave a comment that I'm picking it up today.
Assistant: (calls list_boards)             -> resolves "Engineering" -> id 4217
           (calls create_task
              name="Fix login bug",
              board_id=4217,
              priority="high")             -> task id 88231 created
           (calls add_comment
              task_id=88231,
              content="Picking this up today.")

           Created "Fix login bug" (id 88231) in Engineering at high priority,
           and added your comment.
```

## Development

```sh
git clone https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp.git
cd kanbantool-mcp
uv sync
uv run pytest
uv run ruff check .
uv run ty check
```

Tests are offline-only and mock HTTP via `respx` — no Kanban Tool account needed to contribute.

## Documentation

| File | What it covers |
| --- | --- |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Local setup, conventional-commit rules, what a good PR looks like, how reviews work. |
| [RELEASING.md](RELEASING.md) | Release flow end-to-end (conventional commits → release-please → PyPI), the GitHub App that auths release-please, and break-glass procedures for stuck release PRs. |
| [SEMVER.md](SEMVER.md) | Compatibility commitment for v1.0+ — which surfaces are stable, which are unstable, deprecation policy. |
| [SECURITY.md](SECURITY.md) | How to report security vulnerabilities (GitHub private security advisories). |

## License

[MIT](LICENSE)
