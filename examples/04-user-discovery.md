# Example 4: User discovery — assignment workflows

Two short flows showing how Claude figures out *who* to assign tasks
to. The Kanban Tool API v3 has no bulk list-users endpoint, so user
ids surface through `whoami` (the current authenticated user) and
`list_board_collaborators` (the roster on a specific board).
`get_user` follows up with role flags and active state.

> JSON snippets are illustrative — shapes match `User` /
> `Collaborator` / `Task`, ids and names are made up.

---

## Flow A — "assign this to me"

**User:** Set me as the assignee on task 50240.

**Assistant:** I need my own user id first.

```text
> whoami()
```

```json
{
  "id": 42,
  "name": "Reviewer",
  "initials": "R",
  "is_account_admin": true,
  "is_account_owner": false,
  "is_project_manager": true,
  "is_suspended": false,
  "timezone": "Europe/Warsaw",
  "locale": "en"
}
```

```text
> update_task(task_id=50240, assigned_user_id=42)
```

```json
{
  "id": 50240,
  "name": "Fix login bug",
  "board_id": 4711,
  "lane_id": 90001,
  "assigned_user_id": 42,
  "is_archived": false,
  "updated_at": "2026-05-01T13:42:11Z"
}
```

**Assistant:** Done — task `50240` is now assigned to you (id 42).

---

## Flow B — "assign to a teammate by name"

**User:** Assign the auth migration task to Alice.

**Assistant:** I'll list the Engineering board's collaborators to find
Alice's user id, then update the task.

```text
> list_board_collaborators(board_id=4711)
```

```json
[
  {"id": 42,  "name": "Reviewer",       "initials": "R",  "active": true},
  {"id": 88,  "name": "Alice Hansen",   "initials": "AH", "active": true},
  {"id": 91,  "name": "Bob Chen",       "initials": "BC", "active": true},
  {"id": 14,  "name": "Carol Stoyanov", "initials": "CS", "active": false}
]
```

**Assistant:** Alice is id `88`, active. Let me confirm her role before
assigning — `get_user` returns the full profile.

```text
> get_user(user_id=88)
```

```json
{
  "id": 88,
  "name": "Alice Hansen",
  "initials": "AH",
  "is_account_admin": false,
  "is_account_owner": false,
  "is_project_manager": true,
  "is_suspended": false,
  "timezone": "Europe/Berlin",
  "locale": "en"
}
```

```text
> search_tasks(query="name:\"auth migration\"", board_id=4711)
```

```json
{
  "results": [
    {"id": 50412, "name": "Auth migration kickoff", "board_id": 4711, "lane_id": 90002}
  ],
  "total_count": 1,
  "page": 1,
  "has_more": false
}
```

```text
> update_task(task_id=50412, assigned_user_id=88)
```

```json
{
  "id": 50412,
  "name": "Auth migration kickoff",
  "board_id": 4711,
  "lane_id": 90002,
  "assigned_user_id": 88,
  "updated_at": "2026-05-01T13:43:55Z"
}
```

**Assistant:** Done — assigned "Auth migration kickoff" (`50412`) to
Alice Hansen (`88`). She's an active project manager on the
Engineering board.

---

## Why this surface exists

- The API has **no bulk list-users endpoint**. Listing all users in
  the account isn't possible; user ids come through the boards a user
  has access to.
- `list_board_collaborators(board_id)` is the canonical lookup. It's
  sugar over `get_board` (the roster comes inline on the detail
  payload), so the cost is one HTTP call.
- `whoami()` short-circuits the "me / myself" reference — no need to
  search for the authenticated user in the collaborator list.
- `get_user(id)` is the per-user follow-up when role flags or active
  state matter (e.g. don't assign to a suspended user; don't auto-
  promote without checking `is_account_admin`).
