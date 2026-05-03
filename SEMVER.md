# Versioning policy

`kanbantool-mcp` follows [Semantic Versioning 2.0.0](https://semver.org/)
once it reaches `v1.0.0`. This document tells you, the consumer, exactly
what we promise and what we don't — so you can pin with confidence.

## Pre-1.0 (where we are today)

Before `v1.0.0`:

- **Minor** bumps (e.g. `0.5.0` → `0.6.0`) MAY include breaking changes.
- **Patch** bumps (e.g. `0.6.0` → `0.6.1`) are bug-fix only and SHOULD
  not break anything, but as with any pre-1.0 project the tag is not
  load-bearing.

Everything in `CHANGELOG.md` flagged with a leading `**Breaking:**`
explains what to do. We aim to keep the count small (the project is
opinionated about its tool surface and the wire shape is dictated by
Kanban Tool's API), but reserve the right.

If you need stability before 1.0, pin to an exact version (`==0.X.Y`)
in your dependency manager.

## v1.0+ (what we will commit to)

Once `v1.0.0` ships, the following surfaces are stable under SemVer.
Breaking any of them requires a major bump.

### Stable surfaces

1. **MCP tool names.** A tool's exposed name (`list_boards`, `create_task`,
   `set_custom_field`, ...) is a contract. Renaming a tool is a major
   bump; new tools may land in any minor.
2. **MCP tool required parameters.** Removing a required parameter,
   renaming one, or changing the type to a stricter one (e.g.
   `int` → `Literal[1, 2, 3]`) is a major bump. Adding an optional
   parameter is a minor.
3. **MCP tool return shapes.** The Pydantic model returned by a tool
   (`Task`, `Board`, `Comment`, etc.) is part of the contract. Removing
   a documented field, renaming one, or narrowing its type is a major
   bump. Adding a field is a minor (consumers should already use
   `extra="ignore"`-style tolerance — pydantic does this by default for
   `model_validate`).
4. **Typed exception ladder.** `KanbanToolError` and its documented
   subclasses (`KanbanToolPermissionError`, `KanbanToolHTTPError`,
   `KanbanToolValidationError`, `KanbanToolTransportError`) are part of
   the contract. Removing or renaming them is a major. The MRO between
   them is also stable — anyone catching `KanbanToolHTTPError` should
   continue to catch `KanbanToolValidationError` (a subclass) too.
5. **Environment variable names.** `KANBANTOOL_DOMAIN`,
   `KANBANTOOL_API_TOKEN`, and `KANBANTOOL_READ_ONLY` are the
   configuration contract. Renaming an existing env var, or adding a
   new *required* one, is a major bump. Adding a new *optional* env
   var (defaulting to current behaviour) is a minor.

### Unstable surfaces

The following are NOT covered by SemVer. Pin a hash if you must rely on
them.

1. **Module layout below `kanbantool_mcp.server` and
   `kanbantool_mcp.models`.** Internal helpers like
   `kanbantool_mcp.server._patch_task`, `_decode`, `_decode_list`,
   `_PATCH_TASK_RENAMES`, `_get_client`, the `mcp` instance itself,
   etc. can change in any release. Don't import names that start with
   an underscore.
2. **Error message text.** The exception **types** are stable, the
   exception **strings** are not. Match on `KanbanToolHTTPError`, not
   on its `args[0]`.
3. **Wire-level kwargs to `update_task`-style alias dicts.** The
   `_PATCH_TASK_RENAMES` mapping (`status` ↔ `archived`,
   `lane` ↔ `workflow_stage_id`) is internal; tool-level parameter
   names ARE stable per (2) above, but the rename mapping is not.
4. **Live-integration test scaffolding.** `tests/integration/`'s
   internal structure changes as the live test surface evolves.
5. **CI workflow definitions.** `.github/workflows/*.yml` are
   project-internal; consumers don't depend on them.
6. **Bundled documentation.** `RELEASING.md`, `CONTRIBUTING.md`, this
   file, and `CLAUDE.md` are not API and may be reorganised in any
   release. The `README.md` "Tool reference" table reflects the
   current surface but is documentation, not a contract — the
   authoritative tool list is the set of `@mcp.tool`-decorated
   functions in `kanbantool_mcp.server`.

## Deprecation policy (1.x)

A surface marked for removal will:

1. Be flagged with `warnings.warn(..., DeprecationWarning)` (for
   Python-callable surfaces) or a deprecation note in the tool's
   docstring (for MCP-callable surfaces) starting in `1.M.0`.
2. Stay deprecated for **at least one** subsequent minor release
   (so `1.M.0` → `1.M+1.x`).
3. Be removed in `2.0.0` (or later).

If we ever need to break this — e.g. an upstream Kanban Tool API change
forces our hand on a shorter timeline — the rationale will be on the
relevant `BREAKING CHANGE:` footer in `CHANGELOG.md`.

## What triggers a major bump

In short: anything in the **stable surfaces** list above. In long:

- Removing or renaming a tool.
- Removing a required tool parameter.
- Renaming a required tool parameter.
- Narrowing a tool parameter's type (e.g. `str` → `Literal["a"]`).
- Removing or renaming a Pydantic model field that is currently
  documented.
- Narrowing a Pydantic model field's type.
- Removing or renaming a typed exception subclass.
- Removing or renaming an environment variable that the server reads.
- Raising the minimum Python version (e.g. dropping 3.11).

If you're unsure whether a change is breaking, default to "yes." Every
release on PyPI is forever — getting the bump right matters more than
shipping fast.
