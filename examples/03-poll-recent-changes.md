# Example 3: Polling for recent changes

`recent_changes(board_id, since=...)` is the change-tracking primitive
exposed by `kanbantool-mcp`. Kanban Tool ships **no webhook support**, so
polling the board changelog is the only way to detect activity. This example
shows an LLM agent monitoring a board over a few minutes.

> JSON snippets are illustrative — shapes match `ChangelogEntry`, ids are made up.

## Why polling, not webhooks

Kanban Tool API v3 does not provide outbound webhooks. The server therefore
exposes a single periodic-fetch tool over `/boards/:id/changelog.json` rather
than pretending push semantics exist. Two consequences worth internalising:

- The agent owns the cadence. The tool's docstring asks for **30–120s
  between polls** — fast enough to feel responsive, slow enough to stay well
  inside Kanban Tool's rate limit and avoid burning tokens on noise.
- Always pass `since`. Omitting it returns the entire history of the board,
  which can be huge.

## Transcript

**User:** Keep an eye on the Engineering board and tell me when something interesting happens.

**Assistant:** I'll poll the changelog every ~60s, advancing `since` to the newest entry I've already seen.

**First poll** — initial bootstrap, `since` set to "now" so we only see new activity:

```text
> recent_changes(board_id=4711, since="2026-04-30T11:14:00Z")
```

```json
[]
```

**~60s later** — someone moved a card:

```text
> recent_changes(board_id=4711, since="2026-04-30T11:14:00Z")
```

```json
[
  {
    "id": 770211,
    "created_at": "2026-04-30T11:14:42Z",
    "action": "task_moved",
    "actor_id": 18,
    "actor_name": "Priya N.",
    "target_type": "Task",
    "target_id": 50232,
    "details": {"from_stage_id": 90002, "to_stage_id": 90003}
  }
]
```

**Assistant:** Priya moved *Refactor auth middleware* from In Progress → Review. I'll keep watching.

**~60s later** — `since` advanced to the newest entry's `created_at`:

```text
> recent_changes(board_id=4711, since="2026-04-30T11:14:42Z")
```

```json
[
  {
    "id": 770212,
    "created_at": "2026-04-30T11:15:30Z",
    "action": "comment_added",
    "actor_id": 44,
    "actor_name": "Tom R.",
    "target_type": "Task",
    "target_id": 50231,
    "details": {"comment_id": 88110}
  }
]
```

**Assistant:** New comment on *Wire up Stripe webhook* by Tom. Want me to fetch the comment text with `get_task`?

---

Pattern recap: poll on a steady cadence, always pass `since`, advance `since`
to the newest entry's `created_at` after each call. Entries come back
newest-first per the API.
