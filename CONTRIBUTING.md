# Contributing

Thanks for considering a contribution to `kanbantool-mcp`. The project
is small, opinionated, and pre-1.0 — read this once before you open a
PR and we'll get along fine.

## Getting started

```bash
git clone git@github.com:VeryLongOrgNameSuchWow/kanbantool-mcp.git
cd kanbantool-mcp
uv sync --dev          # installs runtime + dev dependencies
uv run pytest          # 122+ tests, offline, < 2s
uv run ruff check .    # lint
uv run ruff format --check .
uv run ty check        # type check
```

Python 3.11+ required. The CI matrix runs 3.11 / 3.12 / 3.13.

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
