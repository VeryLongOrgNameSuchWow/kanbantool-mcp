# Example 2: Create a task and add a comment

A write flow. The user asks for a high-priority task to be created and
annotated with reproduction steps. The assistant resolves the board, calls
`create_task`, then follows up with `add_comment`.

> JSON snippets are illustrative — shapes match `Task` / `Comment`, ids are made up.

---

**User:** Create a high-priority task in Engineering called "Fix login bug" assigned to me, and add a comment with steps to reproduce.

**Assistant:** I'll find the Engineering board first, then create the task, then attach the repro steps as a comment.

```text
> list_boards()
```

```json
[
  {"id": 4711, "name": "Engineering", "slug": "engineering", "user_role": "admin"},
  {"id": 4712, "name": "Marketing",   "slug": "marketing",   "user_role": "member"}
]
```

```text
> create_task(
    name="Fix login bug",
    board_id=4711,
    priority="high",
    assigned_user_id=12,
  )
```

```json
{
  "id": 50240,
  "name": "Fix login bug",
  "board_id": 4711,
  "lane_id": 90001,
  "priority": "high",
  "assigned_user_id": 12,
  "archived_at": null,
  "is_archived": false,
  "subtasks_count": 0,
  "comments_count": 0,
  "created_at": "2026-04-30T11:14:08Z"
}
```

```text
> add_comment(
    task_id=50240,
    content="Steps to reproduce:\n1. Log out.\n2. Visit /login on Safari 17.\n3. Submit valid creds — page reloads with no session cookie set.\nExpected: redirect to /dashboard.",
  )
```

```json
{
  "id": 88102,
  "content": "Steps to reproduce:\n1. Log out.\n2. Visit /login on Safari 17.\n3. Submit valid creds — page reloads with no session cookie set.\nExpected: redirect to /dashboard.",
  "user_id": 12,
  "created_at": "2026-04-30T11:14:09Z",
  "deleted_at": null
}
```

**Assistant:** Done. Created task `#50240` "Fix login bug" on Engineering at high priority, assigned to you, and posted the repro steps as the first comment. The card landed in Backlog (`lane_id 90001`) — let me know if you'd like it moved straight to In Progress.
