# kanbantool-mcp

An MCP server that connects Claude Code (and other MCP clients) to a [Kanban Tool](https://kanbantool.com/) account.

[![CI](https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/kanbantool-mcp.svg)](https://pypi.org/project/kanbantool-mcp/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Why this exists

Kanban Tool holds the authoritative state of your boards, tasks, and workflow — but an LLM can't see any of it without help. This MCP server gives Claude Code (and any other MCP client) read access to boards and tasks, search via Kanban Tool's query DSL, and write tools to create, update, move, archive, comment on, and break down tasks. The point: stop re-explaining your kanban state to the model on every interaction, and let it act on the board directly when you want it to.

## Status

**Alpha.** The 12-tool surface is settled and exercised against a real Kanban Tool account via the `Live Integration` workflow. Pre-1.0 means the surface may still evolve based on real-world feedback — pin a specific version if you need stability across upgrades.

## Install

### Configuration

Two environment variables, regardless of how you launch the server:

| Variable | What it is | Where to get it |
| --- | --- | --- |
| `KANBANTOOL_DOMAIN` | Your account's subdomain prefix — `acme` for `https://acme.kanbantool.com`. | The URL you log into. |
| `KANBANTOOL_API_TOKEN` | Bearer token for the Kanban Tool API v3. | Profile -> API tokens in your Kanban Tool account. |

### From git (current)

Add to your MCP client's `mcp.json`:

```json
{
  "mcpServers": {
    "kanbantool": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/VeryLongOrgNameSuchWow/kanbantool-mcp",
        "kanbantool-mcp"
      ],
      "env": {
        "KANBANTOOL_DOMAIN": "your-account",
        "KANBANTOOL_API_TOKEN": "your-token"
      }
    }
  }
}
```

### From PyPI

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
| `add_comment` | Post a comment on a task. | `task_id`, `text` |
| `list_subtasks` | List subtasks attached to a task. | `task_id` |
| `add_subtask` | Add a subtask to a task. | `task_id`, `title` |
| `whoami` | Fetch the authenticated user's profile — id, role flags, locale. Use to resolve "me" / "myself" in user requests. | — |
| `get_user` | Fetch one user by id. | `user_id` |
| `list_board_collaborators` | List users with access to a board (the canonical user-discovery surface — the API has no bulk list-users endpoint). | `board_id` |

(`ping` exists as a transport smoke test; not listed above.)

## Examples

These transcripts are illustrative — they show the shape of a session, not literal terminal output.

### Read flow

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

### Write flow

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
              text="Picking this up today.")

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

## Security

See [SECURITY.md](SECURITY.md) for how to report vulnerabilities.

## License

[MIT](LICENSE)
