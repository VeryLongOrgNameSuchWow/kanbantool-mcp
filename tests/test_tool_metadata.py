"""Decorator-side metadata coverage: ``annotations`` + ``output_schema``.

Catches a class of regressions the existing tests don't:

- A new tool added without an ``annotations=`` block (the LLM loses a hint
  about whether the tool is safe to call freely).
- A model field that should appear in the output schema (especially a
  ``@computed_field`` like ``Task.is_archived``) silently dropped because
  the schema was generated in validation mode rather than serialization
  mode.
- FastMCP changing its handling of ``output_schema=`` such that the schema
  we pass is no longer surfaced on the registered tool.

The assertions are deliberately structural rather than golden-snapshot —
the intent is "this metadata is plumbed end-to-end", not "the JSON schema
matches a specific shape down to the last key".
"""

from __future__ import annotations

from typing import Any

import pytest

from kanbantool_mcp import server


# Typed as ``dict[str, Any]`` (not ``dict[str, FunctionTool]``) because
# importing ``FunctionTool`` from ``fastmcp.tools.function_tool`` would
# couple the tests to a private import path; ``Any`` lets ty resolve the
# ``.annotations`` / ``.output_schema`` attribute accesses below without
# a fragile direct import that ``fastmcp`` reorganises between minor
# releases.
@pytest.fixture
def registered_tools() -> dict[str, Any]:
    """All tools registered on the module-level FastMCP server, indexed
    by tool name. Uses ``mcp.list_tools()`` (the supported public API for
    enumerating registered ``FunctionTool`` objects in FastMCP 3.x)."""
    import asyncio

    tools = asyncio.run(server.mcp.list_tools())
    return {t.name: t for t in tools}


def test_every_tool_has_annotations(registered_tools: dict[str, Any]) -> None:
    """Every ``@mcp.tool`` should declare at least one of the safety hints
    so MCP clients (and the LLM behind them) can reason about the tool's
    side effects without reading the implementation."""
    missing: list[str] = []
    for name, tool in registered_tools.items():
        ann = getattr(tool, "annotations", None)
        if ann is None:
            missing.append(name)
            continue
        # ``readOnlyHint`` is the one hint we set on every tool — read or
        # write — so its absence flags an oversight at the decorator.
        if ann.readOnlyHint is None:
            missing.append(name)
    assert not missing, f"Tools missing annotations: {missing}"


@pytest.mark.parametrize(
    "tool_name,expected_property",
    [
        # Task-returning tools must surface the computed flags so the LLM
        # learns about archived/blocked state without parsing timestamps.
        ("get_task", "is_archived"),
        ("get_task", "is_blocked"),
        ("create_task", "is_archived"),
        ("update_task", "is_archived"),
        ("move_task", "is_archived"),
        ("archive_task", "is_archived"),
        ("set_custom_field", "is_archived"),
        # TimeTracker-returning tools must surface ``is_running`` so a
        # caller can tell at a glance whether ``stop_timer`` is needed.
        ("start_timer", "is_running"),
        ("stop_timer", "is_running"),
    ],
)
def test_computed_fields_surface_in_output_schema(
    registered_tools: dict[str, Any],
    tool_name: str,
    expected_property: str,
) -> None:
    """Pydantic v2's ``model_json_schema()`` defaults to validation mode,
    which omits ``@computed_field`` properties. Tools must build their
    schema in serialization mode so derived flags reach the wire."""
    tool = registered_tools[tool_name]
    schema = getattr(tool, "output_schema", None)
    assert schema is not None, f"{tool_name} is missing output_schema"
    # Resolve through ``$ref`` if pydantic emitted the model under ``$defs``.
    properties = _resolve_object_properties(schema)
    assert expected_property in properties, (
        f"{tool_name}.output_schema is missing computed field "
        f"{expected_property!r} (props: {sorted(properties.keys())})"
    )


@pytest.mark.parametrize(
    "tool_name",
    [
        "list_boards",
        "list_board_collaborators",
        "list_custom_field_definitions",
        # ``search_tasks`` deliberately omitted — it returns a typed
        # ``SearchResults`` object (already an MCP-valid object schema),
        # not a bare list, so it does NOT carry the wrap envelope. See
        # ``test_search_tasks_output_schema_describes_search_results``.
        "recent_changes",
        "list_subtasks",
        "reorder_subtasks",
        "list_my_timers",
    ],
)
def test_list_returning_tools_use_wrap_envelope(
    registered_tools: dict[str, Any],
    tool_name: str,
) -> None:
    """MCP requires ``output_schema`` be an object at the top level. List
    returns must therefore be wrapped in a ``{"result": [...]}`` envelope
    tagged with ``x-fastmcp-wrap-result: True`` — the marker FastMCP's
    runtime uses to unwrap before dispatching the structured content."""
    tool = registered_tools[tool_name]
    schema = getattr(tool, "output_schema", None)
    assert schema is not None, f"{tool_name} is missing output_schema"
    assert schema.get("type") == "object", (
        f"{tool_name}.output_schema must be type=object at the top level "
        f"(got {schema.get('type')!r})"
    )
    assert schema.get("x-fastmcp-wrap-result") is True, (
        f"{tool_name}.output_schema must carry x-fastmcp-wrap-result for "
        "FastMCP to unwrap the array on the wire"
    )
    properties = schema.get("properties", {})
    assert "result" in properties and properties["result"].get("type") == "array", (
        f"{tool_name}.output_schema must wrap as {{result: array}}"
    )


def test_search_tasks_output_schema_describes_search_results(
    registered_tools: dict[str, Any],
) -> None:
    """``search_tasks`` returns a ``SearchResults`` wrapper, not ``list[Task]``
    — so the output_schema must describe the four wrapper fields, NOT the
    auto-wrapped ``{"result": [Task...]}`` envelope. Guards against the
    regression where the decorator's ``output_schema=_output_schema(...)``
    argument falls out of sync with the function's return type and silently
    advertises the wrong contract to MCP clients."""
    tool = registered_tools["search_tasks"]
    schema = getattr(tool, "output_schema", None)
    assert schema is not None, "search_tasks must declare an output_schema"
    properties = _resolve_object_properties(schema)
    expected = {"results", "total_count", "page", "has_more"}
    missing = expected - properties.keys()
    assert not missing, (
        f"search_tasks.output_schema is missing SearchResults fields {missing!r} "
        f"(props: {sorted(properties.keys())}). The decorator's output_schema= "
        f"argument is out of sync with the SearchResults return type."
    )
    # Sanity: the wrap-result marker should NOT be present — SearchResults
    # is already an object, FastMCP only wraps non-objects.
    assert schema.get("x-fastmcp-wrap-result") is not True, (
        "search_tasks.output_schema must NOT carry x-fastmcp-wrap-result; "
        "SearchResults is already type=object."
    )


def test_delete_timer_output_schema_describes_null(
    registered_tools: dict[str, Any],
) -> None:
    """``delete_timer`` returns ``None`` (the API responds with an empty
    body). We don't pin ``output_schema=`` on it — FastMCP auto-derives a
    ``{result: null}`` envelope from the ``-> None`` annotation, which is
    the right shape: callers see "this tool returns no payload" without
    us having to construct a custom schema for it."""
    tool = registered_tools["delete_timer"]
    schema = getattr(tool, "output_schema", None)
    assert schema is not None, "delete_timer should auto-derive an envelope schema"
    assert schema.get("properties", {}).get("result", {}).get("type") == "null", (
        f"delete_timer schema should describe a null result; got {schema!r}"
    )


def test_destructive_tools_advertise_destructive_hint(
    registered_tools: dict[str, Any],
) -> None:
    """The four destructive tools (the ones that delete or archive
    server-side records) must set ``destructiveHint=True`` so MCP clients
    can route them through extra confirmation."""
    destructive = {"archive_task", "delete_comment", "delete_subtask", "delete_timer"}
    for name in destructive:
        ann = registered_tools[name].annotations
        assert ann.destructiveHint is True, (
            f"{name} must declare destructiveHint=True (got {ann.destructiveHint!r})"
        )


def test_read_tools_advertise_readonly_hint(
    registered_tools: dict[str, Any],
) -> None:
    """Read tools must set ``readOnlyHint=True``. This is the strongest
    available signal that the LLM can call the tool without confirmation."""
    read_only = {
        "ping",
        "list_boards",
        "get_board",
        "search_tasks",
        "get_task",
        "recent_changes",
        "whoami",
        "get_user",
        "list_board_collaborators",
        "list_custom_field_definitions",
        "list_subtasks",
        "list_my_timers",
    }
    for name in read_only:
        ann = registered_tools[name].annotations
        assert ann.readOnlyHint is True, (
            f"{name} must declare readOnlyHint=True (got {ann.readOnlyHint!r})"
        )


def _resolve_object_properties(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the ``properties`` dict for an object schema, following one
    level of ``$ref`` if the top-level schema is a reference. Single-model
    return types resolve cleanly; the ``$defs``-only case shouldn't happen
    here but the helper handles it defensively."""
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return properties
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        defs = schema.get("$defs", {})
        if isinstance(defs, dict):
            target = defs.get(ref.removeprefix("#/$defs/"), {})
            if isinstance(target, dict):
                target_props = target.get("properties", {})
                if isinstance(target_props, dict):
                    return target_props
    return {}
