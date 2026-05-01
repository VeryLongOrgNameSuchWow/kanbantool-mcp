# Examples

Short transcripts showing what Claude Code can do once `kanbantool-mcp` is wired
up. The JSON snippets in each file are illustrative — they have realistic
shapes, but the ids, names, and timestamps are made up.

All examples assume an account at `rynbou.kanbantool.com` (i.e. `KANBANTOOL_DOMAIN=rynbou`).

- [`01-board-status.md`](01-board-status.md) — read-only flow: summarising the
  current state of a board (`list_boards` → `get_board` → `search_tasks`).
- [`02-create-and-comment.md`](02-create-and-comment.md) — write flow: creating
  a high-priority task and following up with a comment (`search_tasks` →
  `create_task` → `add_comment`).
- [`03-poll-recent-changes.md`](03-poll-recent-changes.md) — polling flow:
  using `recent_changes` to track board activity over time, and a note on why
  there are no webhooks.
- [`04-user-discovery.md`](04-user-discovery.md) — assignment flow:
  using `whoami` and `list_board_collaborators` to resolve user ids
  before calling `update_task`.
