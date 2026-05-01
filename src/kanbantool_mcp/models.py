"""Pydantic models for Kanban Tool resources. Expanded in M1/M2.

Abstraction layer — internal name vs. wire name. The Kanban Tool API exposes
several fields under names we deliberately rename for ergonomics on the MCP
surface; the mapping is centralised here so the next maintainer doesn't have
to grep for it:

- ``Board.columns`` ↔ API ``workflow_stages``
- ``Task.lane_id`` ↔ API ``workflow_stage_id``
- ``Column.type_`` ↔ API ``type`` (avoids shadowing the Python builtin
  internally; serialised back as ``type`` via alias).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class Column(BaseModel):
    """A workflow stage on a board (the API calls these ``workflow_stages``)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, serialize_by_alias=True)

    id: int
    # Live API returns ``null`` for the synthetic root stage that parents the
    # real columns (the one whose ``parent_id is None`` and ``lane_type is None``).
    # Keep the field nullable so the full tree validates; consumers filter
    # leaves via ``parent_id`` if they want only display columns.
    name: str | None = None
    position: int | None = None
    parent_id: int | None = None
    wip_limit: int | None = None
    # Semantic role string from the API ("backlog_inventory", "in_progress",
    # "completed", ...). The LLM uses this to distinguish columns by intent
    # rather than by display name.
    lane_type: str | None = None
    # ``type`` shadows a Python builtin internally; ``serialization_alias``
    # keeps the wire/MCP-schema name as ``type`` while the Python attribute
    # stays ``type_``.
    type_: str | None = Field(default=None, alias="type", serialization_alias="type")


class Swimlane(BaseModel):
    """A horizontal lane on a board."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str
    position: int | None = None


class Board(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str
    description: str | None = None
    slug: str | None = None
    use_swimlanes: bool | None = None
    is_archived: bool | None = None
    user_role: str | None = None
    # Detail-only collections; absent from list_boards' compact payload, hence defaults.
    columns: list[Column] = Field(default_factory=list, alias="workflow_stages")
    swimlanes: list[Swimlane] = Field(default_factory=list)
    # The user-facing roster of board members. The API v3 has no bulk
    # list-users endpoint, so this is the canonical way to discover user
    # IDs for ``assigned_user_id`` on tasks. ``Collaborator`` is defined
    # below; the forward reference resolves via ``from __future__ import
    # annotations`` + pydantic's lazy resolution at validate time.
    collaborators: list[Collaborator] = Field(default_factory=list)
    # ``card_template`` is the API's per-board "which card fields are shown"
    # config — a dict keyed by field name (``description``, ``priority``,
    # ``custom_field_1``, ...) whose values describe each field's enabled
    # state, position, and (for ``custom_field_*``) label/type/options.
    # Exposed verbatim as a dict; a typed wrapper can come in v0.2.0 once
    # we have a use case beyond "show the LLM the config".
    card_template: dict[str, Any] | None = None
    # v0.2.0: embedded ``tasks: list[Task]`` (live-API spike showed the API
    # returns these on the detail endpoint — could remove a round-trip for
    # many flows). Pending design call.


class Task(BaseModel):
    """A task (card) on a board.

    Mirrors the GET ``/tasks/{id}.json`` response. Heavier nested resources
    (full subtasks, comments, time-tracker entries) are intentionally elided
    here — only their counts/totals are surfaced. Dedicated tools fetch the
    deep objects when an agent actually needs them.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str
    description: str | None = None
    board_id: int | None = None
    # ``workflow_stage_id`` is the on-the-wire name; expose the column id under
    # the terser ``lane_id`` alias that matches how callers reason about a
    # card's location (column/lane).
    lane_id: int | None = Field(default=None, alias="workflow_stage_id")
    swimlane_id: int | None = None
    position: int | None = None
    # ``priority`` is a small enum on most accounts but some installs return
    # raw integers; accept either rather than guessing.
    priority: int | str | None = None
    color: str | None = None
    # ISO 8601 strings as returned by the API; no native datetime parsing here.
    due_date: str | None = None
    start_date: str | None = None
    tags: str | None = None
    # The API surfaces a single user id, not a list. The richer Assignee
    # submodel (and any multi-user collaborators) can land alongside a tool
    # that actually needs it.
    assigned_user_id: int | None = None
    # Archival is timestamped on the wire; ``is_archived`` is a derived
    # convenience exposed via a property below.
    archived_at: str | None = None
    # ``block_reason`` is the only block-related field the API actually
    # returns — ``is_blocked`` is derived from "reason is set".
    block_reason: str | None = None
    subtasks_count: int | None = None
    # Inline subtasks on the detail endpoint. Per the Kanban Tool API v3 docs
    # there's no dedicated "list subtasks" endpoint — subtasks live on the
    # parent task's detail payload. Empty default for the compact list shape
    # (e.g. ``search_tasks`` results), where the API omits ``subtasks``.
    subtasks: list[Subtask] = Field(default_factory=list)
    comments_count: int | None = None
    # Flat seconds — the running total across all users' completed timers
    # on this task. ``time_trackers`` below is the per-timer detail.
    timers_total: int | None = None
    # Inline per-user timer entries on the task detail endpoint. Each entry
    # is one user's timer (running or finished) on this task. ``TimeTracker``
    # is forward-referenced; the definition lives after ``User`` near the
    # bottom of the file.
    time_trackers: list[TimeTracker] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    # --- Sizing & estimation -------------------------------------------------
    # Story-point / abstract size as set on the card.
    size_estimate: int | None = None
    # Free-form description accompanying ``size_estimate``.
    size_estimate_description: str | None = None
    # Estimated effort in seconds (distinct from ``timers_total``, which is
    # recorded actuals).
    time_estimate: int | None = None

    # --- Search / discoverability -------------------------------------------
    # Free-form list of search-helper strings; distinct from ``tags`` (which is
    # the comma-separated user-facing label string). Live spike confirmed the
    # wire shape is a list of strings.
    search_tags: list[str] = Field(default_factory=list)

    # --- Visual markers ------------------------------------------------------
    # Named card colour, e.g. ``"red"`` (separate from ``color`` which is a
    # hex/CSS string on some accounts).
    card_color: str | None = None
    # API-provided RGB rendering of ``card_color``, useful when a UI wants to
    # avoid maintaining its own colour-name → swatch lookup.
    card_color_in_rgb: str | None = None
    # True when the card-colour foreground should be inverted for contrast.
    card_color_invert: bool | None = None
    # Per-account card-type id (board-config concept; the LLM mostly treats
    # this as opaque).
    card_type_id: int | None = None

    # --- Schedule fields -----------------------------------------------------
    # raw API shape passed through; typed wrapper deferred to v0.x.x
    recurring_schedule: dict[str, Any] | None = None
    # raw API shape passed through; typed wrapper deferred to v0.x.x
    reminders_schedule: dict[str, Any] | None = None

    # --- Relationships -------------------------------------------------------
    # raw entries — typed sub-model can come when an MCP tool actually needs it
    linked_tasks: list[dict[str, Any]] = Field(default_factory=list)
    # Status string summarising the linked-task relationship state.
    linked_tasks_status: str | None = None
    # raw entries — typed sub-model can come when an MCP tool actually needs it
    task_dependencies: list[dict[str, Any]] = Field(default_factory=list)
    # raw entries — typed sub-model can come when an MCP tool actually needs it
    collaborators: list[dict[str, Any]] = Field(default_factory=list)

    # --- Attachments ---------------------------------------------------------
    # raw entries — typed sub-model can come when an MCP tool actually needs it
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    attachments_count: int | None = None

    # --- Provenance & state --------------------------------------------------
    # User id of the task's creator.
    created_by_id: int | None = None
    # ISO 8601 timestamp of the task's last column/swimlane move.
    moved_at: str | None = None
    # ISO 8601 timestamp the task is snoozed/postponed until.
    postponed_until: str | None = None
    # Companion to ``subtasks_count`` — number of subtasks already completed.
    subtasks_completed_count: int | None = None
    # Caller-supplied identifier for cross-system linking.
    external_id: str | None = None
    # Caller-supplied URL for cross-system linking.
    external_link: str | None = None

    # --- Custom fields -------------------------------------------------------
    # The wire payload exposes ``custom_field_1`` .. ``custom_field_15`` as 15
    # top-level keys. Their per-board *meaning* (label, type, enabled state)
    # lives in ``Board.card_template[custom_field_N]`` — fetch that via
    # ``list_custom_field_definitions(board_id)`` to know what each slot
    # holds. Collapse the 15 keys into one dict-typed surface here so
    # callers don't have to enumerate them; the ``_collect_custom_fields``
    # validator does the lift before the rest of the model parses.
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    # v0.2.0+: ``changelogs`` / ``time_trackers`` (heavy nested data —
    # exposed via dedicated tools rather than inlined here) remain dropped
    # via ``extra="ignore"`` for now. Tracked under #38.

    # The Kanban Tool API serialises empty collections as JSON ``null`` rather
    # than ``[]`` for several of these additive fields (live spike confirmed
    # for ``linked_tasks``; the other detail-only collections behave the same
    # way). Coerce ``None`` → ``[]`` at validation time so callers see a
    # consistent list-typed surface and don't have to defensive-check for
    # ``None``. Applies to all the additive collection fields; the existing
    # ``subtasks`` field is a typed sub-model list and is omitted from this
    # list so the original parsing behaviour stays untouched.
    @field_validator(
        "linked_tasks",
        "task_dependencies",
        "collaborators",
        "attachments",
        "search_tags",
        mode="before",
    )
    @classmethod
    def _none_to_empty_list(cls, value: Any) -> Any:
        return [] if value is None else value

    # The wire spreads custom field values across ``custom_field_1`` ..
    # ``custom_field_15`` top-level keys. Lift them into ``self.custom_fields``
    # before the rest of the model parses so callers see a single dict instead
    # of 15 individual attributes. ``extra="ignore"`` then drops the
    # now-pulled-out raw keys silently.
    @model_validator(mode="before")
    @classmethod
    def _collect_custom_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # Only lift if the caller hasn't already supplied a ``custom_fields``
        # dict directly (e.g. in tests that round-trip ``model_dump`` output).
        if "custom_fields" in data:
            return data
        custom = {k: data[k] for k in data if k.startswith("custom_field_")}
        if not custom:
            return data
        # Build a new dict — don't mutate the caller's input.
        rest = {k: v for k, v in data.items() if not k.startswith("custom_field_")}
        rest["custom_fields"] = custom
        return rest

    # ``@computed_field`` is required so pydantic v2 includes the derived
    # boolean in ``model_dump()`` and ``model_json_schema()`` — a bare
    # ``@property`` is invisible to the serialiser, which means FastMCP would
    # never surface the flag to the LLM. The ``prop-decorator`` ignore quiets
    # the pydantic v2 ``@computed_field`` + ``@property`` decorator-order quirk.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_archived(self) -> bool:
        """True iff the task has an archival timestamp."""
        return self.archived_at is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_blocked(self) -> bool:
        """True iff the task has a non-empty block reason."""
        return self.block_reason is not None


class Comment(BaseModel):
    """A comment on a task. Mirrors the POST ``/tasks/{id}/comments.json``
    response. Field names follow the wire format directly — no aliasing —
    since the API's keys are already pleasant Python identifiers.

    Wire-shape note: the comment body lives under ``content`` on the wire,
    NOT ``text``. An earlier iteration of this model declared ``text`` and
    the server sent ``{"comment": {"text": ...}}`` — both wrong; the live
    API silently 422'd every call. Confirmed via spike against a real
    account before the M5 fix landed."""

    model_config = ConfigDict(extra="ignore")

    id: int
    # Always a string on live comments — the API enforces non-blank ``content``
    # at create time, so the field never carries ``None`` on the wire.
    content: str
    user_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # Soft-delete timestamp — populated on the response from
    # ``DELETE /tasks/{task_id}/comments/{id}.json`` (the API never hard-deletes,
    # so the record stays around with this set). ``None`` on every live comment.
    deleted_at: str | None = None


class Subtask(BaseModel):
    """A subtask attached to a task.

    Surfaced inline on ``Task.subtasks`` whenever a task is fetched (the
    Kanban Tool API v3 has no dedicated list-subtasks endpoint — see
    https://kanbantool.com/developer/api-v3). ``POST /subtasks.json`` returns
    the same shape. ``name`` is the API-native field — kept verbatim rather
    than aliased to ``title`` so there's no rename to maintain on either edge.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    is_completed: bool | None = None
    position: int | None = None
    # Parent task id — surfaced on the wire after the create round-trip and
    # useful for callers reasoning about a subtask in isolation.
    task_id: int | None = None
    # Single-assignee, mirroring ``Task.assigned_user_id``.
    assigned_user_id: int | None = None
    # Soft-delete timestamp — populated on the response from
    # ``DELETE /subtasks/{id}.json`` (the API never hard-deletes, so the
    # record stays around with this set). ``None`` on every live subtask.
    deleted_at: str | None = None


class ChangelogEntry(BaseModel):
    """A single entry from a board's changelog feed.

    Field names mirror the API verbatim (no aliasing) — the wire keys are
    already pleasant Python identifiers. Fields beyond ``id`` and
    ``created_at`` are optional: the API's exact shape varies by event type,
    and ``data`` is the catch-all for action-specific context (e.g. for
    ``what="created"`` it carries ``user_initials``, ``task_name``,
    ``workflow_stage_name``, ...)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    created_at: datetime
    # Action verb (``"created"``, ``"updated"``, ``"moved"``, ...).
    what: str | None = None
    user_id: int | None = None
    # Object the action targeted — ``"Task"``, ``"Board"``, etc.
    changed_object_type: str | None = None
    changed_object_id: int | None = None
    # Pre-rendered human-readable summary. Useful for LLM consumers that just
    # want a one-line "what happened" string without re-templating ``data``.
    description: str | None = None
    # Action-specific payload (e.g. ``user_initials``, ``task_name``,
    # ``workflow_stage_name``). Shape varies by ``what``.
    data: dict[str, Any] | None = None


class Collaborator(BaseModel):
    """A user with access to a specific board.

    Inline on ``Board.collaborators[]`` — the API v3 has no bulk-list-users
    endpoint, so this is the canonical way to discover user IDs for
    assignment workflows.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str | None = None
    initials: str | None = None
    # ``True`` for users still allowed to act on the board, ``False`` for
    # suspended / removed members. Useful when callers want to ignore stale
    # entries that the wire still reports.
    active: bool | None = None


class User(BaseModel):
    """An account-scoped user.

    Returned from ``GET /users/{id}.json`` (and its ``/users/current.json``
    redirect alias). Surfaces the LLM-meaningful subset of the wire shape;
    heavy nested structures (``account``, ``customizations``, ``settings``,
    ``groups``) are dropped via ``extra="ignore"`` and can be promoted to
    typed sub-models when an MCP tool actually needs them.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str | None = None
    initials: str | None = None
    # Role flags — a single user can hold any combination of these.
    is_account_admin: bool | None = None
    is_account_owner: bool | None = None
    is_project_manager: bool | None = None
    is_suspended: bool | None = None
    # ISO 8601 strings — match the existing convention on Task/Board.
    last_activity_on: str | None = None
    last_login_at: str | None = None
    created_at: str | None = None
    # Account-level locale + timezone surfaced for LLMs that want to render
    # times or messages in the user's local context.
    timezone: str | None = None
    locale: str | None = None


class CustomFieldDefinition(BaseModel):
    """Per-board metadata for one of the ``custom_field_1..15`` slots.

    Sourced from ``Board.card_template[custom_field_N]`` and surfaced via
    the ``list_custom_field_definitions`` MCP tool. Use this to interpret
    the raw values on ``Task.custom_fields`` — the slot number alone tells
    you nothing about what's in it on a given board.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # Slot number (1..15). The value is added by the tool when assembling
    # the result list — the wire payload doesn't carry it explicitly because
    # the slot is encoded in the parent dict's key.
    slot: int
    # Human-readable label set by the board owner (e.g. "Customer", "ETA").
    label: str | None = None
    # Field type — observed values: ``"text"``. Other types likely include
    # ``"number"``, ``"date"``, ``"dropdown"`` per the Kanban Tool UI; treat
    # this as a hint string rather than a closed enum.
    type_: str | None = Field(default=None, alias="type", serialization_alias="type")
    # ``True`` when the field is shown on this board's UI / available for
    # writes. ``False`` slots are usually dormant — values may exist on
    # individual tasks but the LLM should generally skip them.
    enabled: bool | None = None
    # Comma-separated values for dropdown-typed fields; empty for free-text.
    options: str | None = None
    # UI position on the card detail panel — useful when you want to
    # surface fields in the order the user sees them.
    position: int | None = None


class TimeTracker(BaseModel):
    """A single user's elapsed-time record on a task.

    Time tracking in Kanban Tool is per-user, per-task — each ``TimeTracker``
    is one user's running or finished timer. The aggregate counters on
    ``Task`` (``timers_total``, ``timers_active_count``, etc.) summarise
    across users.

    Live-spike-confirmed wire shape: API returns the timer fields plus a
    nested ``task`` object (priority/name/etc.) that we drop via
    ``extra="ignore"`` — the timer's own ``task_id`` is sufficient.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    # User the timer belongs to (timers are per-user, per-task).
    user_id: int | None = None
    board_id: int | None = None
    task_id: int | None = None
    # ISO 8601 timestamps. ``ended_at is None`` means the timer is still
    # running; the typed ``is_running`` flag below derives this.
    started_at: str | None = None
    ended_at: str | None = None
    # ``True`` for timers exposed in the user's normal timer list (vs.
    # archived/postponed entries).
    listed: bool | None = None
    # Sprint id the timer was started inside; matches ``task_id`` when no
    # sprint is active.
    sprint_id: int | None = None
    # Elapsed seconds since the most recent resume — distinct from the task
    # aggregate ``timers_total``.
    seconds_from_resumed_sprint: int | None = None
    # Per-user ordering on the timer list UI; rarely meaningful to the LLM.
    position: int | None = None
    # ISO timestamps for the highlight + enlist UI features.
    highlighted_at: str | None = None
    enlist_at: str | None = None
    # Audit timestamps.
    created_at: str | None = None
    updated_at: str | None = None

    # ``@computed_field`` here so pydantic v2 includes the derived flag in
    # ``model_dump()`` / ``model_json_schema()`` (a bare ``@property`` is
    # invisible to the serialiser, which means FastMCP wouldn't surface it
    # to the LLM).
    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_running(self) -> bool:
        """True iff the timer is still active (no ``ended_at`` set)."""
        return self.ended_at is None
