# Contributing

Thanks for considering a contribution to `kanbantool-mcp`. The project
is small, opinionated, and pre-1.0 — read this once before you open a
PR and we'll get along fine.

## Getting started

```bash
git clone git@github.com:VeryLongOrgNameSuchWow/kanbantool-mcp.git
cd kanbantool-mcp
uv sync --dev          # installs runtime + dev dependencies
uv run pytest          # offline suite, < 2s; prints --randomly-seed=<N>
uv run ruff check .    # lint
uv run ruff format --check .
uv run ty check        # type check
```

Python 3.11+ required. The CI matrix runs 3.11 / 3.12 / 3.13.

Tests run in a randomized order via `pytest-randomly` to surface accidental
ordering dependencies. The seed is printed at the top of every run; reproduce
a flaky failure with `uv run pytest -p randomly --randomly-seed=<N>`.

## Filing issues

Use the issue templates — bug report or feature request. The bug
template asks for repro steps, expected vs. actual, and the
`KANBANTOOL_DOMAIN`/`KANBANTOOL_API_TOKEN` shape (please **don't**
paste tokens; redact). Feature requests should explain the LLM-agent
use case the tool would enable, not just "expose endpoint X."

## Pull requests

We use [GitHub Flow](https://docs.github.com/en/get-started/using-github/github-flow):
feature branch → PR → squash-merge to `main`. No `develop` / `release/*`
branches.

### Conventional commits

Squash-merge commit titles MUST follow
[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>[optional scope][!]: <short description>
```

| Type | Use for |
|------|---------|
| `feat` | New tool, new model field, new MCP-visible behaviour |
| `fix` | Bug fix (incl. error-surface inconsistencies) |
| `docs` | README, CHANGELOG, CONTRIBUTING, RELEASING, examples |
| `chore` | Dependency bumps, repo housekeeping |
| `ci` | CI workflow changes |
| `test` | Test-only changes |
| `refactor` | Rename / move without behaviour change |
| `perf` | Performance improvement without API change |

Add `!` (e.g. `feat!: ...`) or a `BREAKING CHANGE:` footer for
backwards-incompatible changes — release-please uses these to bump
the major version.

`Closes #N` footers in the squash-merge body auto-close the issue at
release time (parsed from the CHANGELOG entry by the release
workflow).

### What a good PR looks like

- One logical change per PR. Don't bundle unrelated cleanup.
- Tests that exercise the change. Offline tests must stay offline
  (mock with `respx`); see `tests/conftest.py` for shared fixtures.
- If you touch a tool's wire contract (URL, body shape, response
  parsing), add or update a live integration test under
  `tests/integration/`. Live tests are excluded from the default
  `pytest` run and only execute via the manual `Live Integration`
  workflow.
- Docstrings for `@mcp.tool` functions are the prompt the LLM sees.
  Keep them terse, action-oriented, and accurate. Don't explain
  *what* the code does — explain *when* an agent should reach for
  this tool and what a good argument looks like.

### When you add or rename a tool

The LLM-facing mental model of `kanbantool-mcp` is encoded across
several surfaces that will silently drift if you only update one.
When you add a tool, rename a tool, or change a tool's semantics in
a way that contradicts any of these, update them in the same PR:

- **Tool registration** — the `@mcp.tool` function and its
  docstring in `src/kanbantool_mcp/server.py`. The docstring is the
  per-tool prompt the LLM sees.
- **`_SERVER_INSTRUCTIONS`** in `src/kanbantool_mcp/server.py` —
  the always-loaded preamble passed as `instructions=` on the
  FastMCP server. Hard-capped at ~150 words; it's the mental model,
  not the reference.
- **`llms.txt`** at the repo root — the longer companion that
  extends the preamble with quirks, edge cases, and the full tool
  inventory.
- **README per-client install snippets** — only when the change
  affects what a fresh installer sees (new env var, renamed
  command, changed default).

### When you add or rename an env var or CLI flag

Env vars and CLI flags are the operator-facing contract and drift
across a different set of surfaces than tools. When you add, rename,
or change the semantics of either, update them in the same PR.

For an env var (e.g. adding `KANBANTOOL_NEW_THING`):

- **README env-var table** — the configuration reference under
  "Wiring it into your client."
- **`SEMVER.md` stable env var list** — the enumerated set under
  "Environment variable names" in the stable surfaces section.
- **`config.py`** — the reader and any validation.
- **`_SERVER_INSTRUCTIONS`** — only if the env var changes how the
  LLM should reason about the server (e.g. `KANBANTOOL_READ_ONLY`
  hides write tools; `KANBANTOOL_LOG_LEVEL` doesn't).
- **README per-client install snippets** — only if the env var is
  required to start the server. Optional vars stay out of the
  snippets to keep the happy path short.

For a CLI flag (e.g. adding `--new-flag`):

- **README install snippets / quickstart** — wherever the launcher
  command appears.
- **`__main__.py`** — the flag parser and handler.

### CI

Every PR must pass:

- `test (3.11 / 3.12 / 3.13)` — full offline suite.
- `dry-run` (only fires on PRs touching packaging-relevant files) —
  builds wheel + sdist, smoke-tests the wheel installs.

The live integration suite is **not** required for PR — by design.
It only runs manually via the `Live Integration` workflow against a
real Kanban Tool account.

### Reviews

Two-step: I'll look at the change for correctness + simplicity; if
you've added a tool or touched the wire surface, I'll also dispatch
the live integration suite before merging.

## Code style

- `ruff` is the formatter and the linter. `ruff format` for the
  former, `ruff check` for the latter. Settings live in
  `pyproject.toml`.
- `ty` is the type-checker. Settings live in `pyproject.toml`.
- Pydantic models use `ConfigDict(extra="ignore")` for forward-compat
  with API additions. When a wire field uses a different name than
  the model attribute, use `Field(alias="<wire>")` plus
  `populate_by_name=True`.
- Errors flow through the typed ladder in `src/kanbantool_mcp/exceptions.py`
  — `KanbanToolError` → `KanbanToolPermissionError`,
  `KanbanToolHTTPError` → `KanbanToolValidationError`,
  `KanbanToolTransportError`. New tools should raise these, not
  `ValueError` / `RuntimeError`.

## Releasing

You don't cut releases — release-please does. Land conventional
commits on `main`, the bot opens (or refreshes) a "chore(main):
release X.Y.Z" PR. Merging it auto-tags + auto-publishes to PyPI.
See `RELEASING.md` for the full flow.

## Security

If you find a security issue, **don't** open a public issue or PR.
Use GitHub private security advisories — link in `SECURITY.md`.
