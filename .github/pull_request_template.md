<!-- PR title MUST follow Conventional Commits (release-please reads it):
     <type>[scope][!]: <short description>
     e.g.  feat(server): add list_users tool
           fix: handle 422 with empty body
           docs(readme): tighten install snippet -->

## Summary

<!-- One paragraph: what changes and why. -->

## What's in / what's out

<!-- Optional. Use if scope was non-obvious or you deferred related work. -->

## Test plan

- [ ] `uv run pytest`
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run ty check`
- [ ] (if you touched a tool's wire contract) Live integration tests exercise the new path
- [ ] CI matrix on this PR — green

## Closes / refs

<!-- "Closes #N" footers in the squash-merge body auto-close issues at
     release time. Use them. -->

Closes #
