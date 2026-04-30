# Example 1: Board status summary

A read-only flow. The user asks for the current state of a board; the assistant
walks `list_boards` → `get_board` → `search_tasks` and summarises by column.

> JSON snippets are illustrative — shapes match `Board` / `Task`, ids are made up.

---

**User:** What's the current state of my Engineering board?

**Assistant:** I'll find the board first, fetch its columns, then pull the active tasks.

```text
> list_boards()
```

```json
[
  {"id": 4711, "name": "Engineering", "slug": "engineering", "use_swimlanes": false, "is_archived": false, "user_role": "admin"},
  {"id": 4712, "name": "Marketing",   "slug": "marketing",   "use_swimlanes": false, "is_archived": false, "user_role": "member"}
]
```

```text
> get_board(board_id=4711)
```

```json
{
  "id": 4711,
  "name": "Engineering",
  "use_swimlanes": false,
  "columns": [
    {"id": 90001, "name": "Backlog",     "position": 1, "type": "queue"},
    {"id": 90002, "name": "In Progress", "position": 2, "type": "in_progress", "wip_limit": 4},
    {"id": 90003, "name": "Review",      "position": 3, "type": "in_progress", "wip_limit": 3},
    {"id": 90004, "name": "Done",        "position": 4, "type": "done"}
  ],
  "swimlanes": [],
  "custom_fields": []
}
```

```text
> search_tasks(query="archived:false", board_id=4711, limit=50)
```

```json
[
  {"id": 50231, "name": "Wire up Stripe webhook",  "lane_id": 90002, "priority": "high",   "assignees": [12], "tags": "backend,billing"},
  {"id": 50232, "name": "Refactor auth middleware","lane_id": 90002, "priority": "medium", "assignees": [12, 18]},
  {"id": 50233, "name": "Onboarding tour copy",    "lane_id": 90003, "priority": "low",    "assignees": [44]},
  {"id": 50234, "name": "Migrate Postgres to 16",  "lane_id": 90001, "priority": "high",   "assignees": []}
]
```

**Assistant:** Engineering has 4 active tasks:

- **In Progress (2/4)** — *Wire up Stripe webhook* (high), *Refactor auth middleware* (medium)
- **Review (1/3)** — *Onboarding tour copy* (low)
- **Backlog** — *Migrate Postgres to 16* (high, unassigned)

Both WIP-limited columns have headroom; the Postgres migration is the only
unassigned high-priority item — worth picking up next.
