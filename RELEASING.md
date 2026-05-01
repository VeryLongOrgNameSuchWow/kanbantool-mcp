# Releasing

This file documents the cut-and-publish process for `kanbantool-mcp`.
The flow is **conventional commits → release-please opens a release PR
→ merge → auto-tag + auto-publish**. Tag-driven, mostly hands-off.

## TL;DR

```bash
# 1. Land your changes as conventional-commit PRs to main.
#    Squash-merge titles must follow https://www.conventionalcommits.org/.
#    Examples:
#       feat: add list_users tool
#       fix(server): handle 422 with no body
#       docs(readme): tighten install snippet
#       fix!: drop unsupported py3.10                  ← MAJOR bump
#       chore: bump dependabot pin                    ← no release entry

# 2. release-please opens (or refreshes) a release PR titled
#    "chore(main): release X.Y.Z". It bumps __version__ in
#    src/kanbantool_mcp/__init__.py and prepends an entry to CHANGELOG.md
#    derived from your conventional commits.

# 3. Review the release PR.
#    - Are the version bump and CHANGELOG section right?
#    - Edit CHANGELOG.md directly if you want to soften wording or
#      reorganise sections; release-please respects manual edits and
#      will not overwrite them on subsequent pushes.

# 4. Merge the release PR (squash). release-please-action then:
#    - tags the merge commit as vX.Y.Z;
#    - creates a GitHub Release with the CHANGELOG body.

# 5. The new tag fires release.yml, which:
#    - builds wheel + sdist;
#    - publishes to PyPI via OIDC trusted publishing;
#    - attaches dist artifacts + sigstore attestations to the release;
#    - closes any "Closes #N" issues mentioned in the new CHANGELOG section.

# That's it. No manual ``git tag``, no manual ``gh release create``.
```

## After the workflow completes

```bash
# Verify (optional — auto-close handles the rest).
curl -s https://pypi.org/pypi/kanbantool-mcp/json | jq '.info.version'
gh release view vX.Y.Z --repo VeryLongOrgNameSuchWow/kanbantool-mcp
```

## Conventional commits — the contract

release-please reads merge-commit titles on `main`. Pattern:

```
<type>[optional scope][!]: <description>

[body]

[footer(s) — e.g. "Closes #N", "BREAKING CHANGE: …"]
```

| `<type>` | Bumps | Appears in CHANGELOG under |
|----------|-------|----------------------------|
| `feat` | minor | `### Features` |
| `fix` | patch | `### Bug Fixes` |
| `perf` | patch | `### Performance Improvements` |
| `refactor` | patch | `### Refactors` |
| `docs` | patch | `### Documentation` |
| `chore` | none | (omitted unless `!`) |
| `ci` / `build` / `test` | none | (omitted unless `!`) |

A `!` after the type or a `BREAKING CHANGE:` footer triggers a major bump
(e.g. `feat!: drop py3.10` or `fix(server): swap error code\n\nBREAKING
CHANGE: clients must handle KanbanToolValidationError now`).

The `Closes #N` footer is honoured separately by `release.yml`'s
auto-close step — see "Tag → publish flow" below.

## Manual-cut escape hatch

If something goes wrong and you need to cut without release-please
(e.g. emergency hotfix, release-please service is down):

```bash
git checkout main && git pull
$EDITOR src/kanbantool_mcp/__init__.py  # __version__ = "X.Y.Z"
$EDITOR CHANGELOG.md                    # add "## [X.Y.Z] - YYYY-MM-DD"
git commit -am "chore: release X.Y.Z"
git push origin main
git tag vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
# release.yml fires; release will be created without notes
gh release edit vX.Y.Z --notes-file <(awk '/^## \[X.Y.Z\]/,/^## \[/' CHANGELOG.md)
```

## Pre-cut checklist

Walk this on the release PR (whether opened by release-please or
hand-cut). The first two sections are normally green-by-construction
when release-please opened the PR; the rest are maintainer
responsibility because release-please can't see them.

### Version (release-please owns)

- [ ] `src/kanbantool_mcp/__init__.py`'s `__version__` matches the
  intended tag — no `.dev`, no skipped semver step.
- [ ] `pyproject.toml` is unchanged for the version (it's `dynamic`
  via Hatchling).
- [ ] `.release-please-manifest.json` matches the bumped version
  (release-please updates this; don't hand-edit).

### Changelog (release-please owns)

- [ ] The new `[X.Y.Z] - YYYY-MM-DD` section actually reflects what
  shipped. release-please groups by conventional-commit type; if a
  fix landed under `chore:` it won't show up — fix the commit history
  before merging the release PR (or move the entry manually; the bot
  honours manual edits).
- [ ] `Closes #N` footers from the merged feature commits propagated
  into the release-PR's CHANGELOG section. (release.yml's auto-close
  step keys off these — see "Tag → publish flow".)

### README

- [ ] No "coming soon" / "pre-alpha" / "not yet on PyPI" /
  "(once vX.Y.Z ships)" copy that's about to become stale.
- [ ] Status section matches the `Development Status :: …` classifier.
- [ ] Badges still resolve (CI, PyPI, license, Python version).

### pyproject

- [ ] Classifiers match reality (Dev Status, supported Python
  versions, license, OS, audience).
- [ ] `requires-python` matches the CI matrix.
- [ ] `dependencies` lower bounds are still honest (recent enough to
  match what we exercise; not so loose that we'd silently regress).

### Examples / docs

- [ ] Example code in `examples/` uses real, current MCP-tool kwargs
  (the v0.1.0 review caught a stale `assignees=[12]` that should have
  been `assigned_user_id=12`).
- [ ] Tool reference table in README has the same set of tools the
  server actually exposes.

### Build artifacts

- [ ] `uv build` produces exactly two artifacts: `*.whl` and `*.tar.gz`.
  No `default.gitignore`, no stray files.
- [ ] Wheel contents (`unzip -l dist/*.whl`) are src-only — no
  `tests/`, `.github/`, `examples/`, `CLAUDE.md`, `RELEASING.md`.
- [ ] sdist contents (`tar tzf dist/*.tar.gz`) include the source
  tree + tests + examples, but **not** `CLAUDE.md` (excluded via
  `[tool.hatch.build.targets.sdist] exclude`).

### Security

- [ ] `git ls-files` doesn't list any `.env`, `*.kdbx`, `*.pem`,
  `id_*`, `credentials.*` etc.
- [ ] Quick grep for accidental token strings: `grep -RIn -e "Bearer "
  -e "letmein" --include="*.py" --include="*.md"` returns only
  scrubber code, docs, or test placeholders.

## Tag → publish flow

The `Release` workflow (`.github/workflows/release.yml`) fires on
any `v*` tag push. It runs in the `pypi` GitHub Environment, which
has a deployment branch policy that admits only `v*` tags.

The job:

1. Checks out the tag.
2. `astral-sh/setup-uv` → `uv build` → produces wheel + sdist.
3. `pypa/gh-action-pypi-publish` claims an OIDC token from PyPI's
   trusted-publisher endpoint (configured under the org's PyPI
   account) and uploads the artifacts. **No PyPI password ever
   leaves the workflow runner.**
4. `softprops/action-gh-release` attaches the wheel, sdist, and
   sigstore `*.publish.attestation` files to the GitHub release that
   release-please-action created when the release PR merged.
5. A final shell step parses the new `[X.Y.Z]` section of
   `CHANGELOG.md` for `Closes #N` footers and runs
   `gh issue close $N` for each — keeping the issue tracker honest
   without a manual sweep.

If the workflow fails partway through (e.g. PyPI 5xx), the tag is
already on the remote and you have to resolve manually — see
**Re-running** below.

## Trusted publisher (one-time setup)

This is done once per project; documented for future maintainers.

PyPI side (https://pypi.org/manage/account/publishing/):

| Field | Value |
| --- | --- |
| PyPI Project Name | `kanbantool-mcp` |
| Owner | `VeryLongOrgNameSuchWow` |
| Repository name | `kanbantool-mcp` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

GitHub side (`Settings → Environments → pypi`):

- Deployment branch policy: tag pattern `v*` only.
- Optional: required reviewers (gates each publish behind a human
  approval click). Off by default; turn on if you want belt-and-
  suspenders before each upload.

## Re-running a failed release

If the release workflow fails mid-flight (e.g. PyPI was down):

- **PyPI upload step failed; tag exists.** Re-trigger by re-running
  the workflow from the GitHub Actions UI (`Re-run failed jobs`).
  PyPI's trusted-publisher accepts retries on the same artifact
  filename, so this is safe.
- **PyPI upload succeeded but the GitHub release step failed.** The
  package is shipped; rerunning is fine but won't re-upload (PyPI
  refuses overwrites). The release-creation step is idempotent —
  rerunning it just creates the missing release.
- **Bad version pushed.** PyPI versions are immutable. You can `yank`
  a bad version on PyPI's UI (still installable but flagged) and
  cut a new patch (`vX.Y.Z+1`) with the fix. Don't try to delete
  and re-publish the same version number.

## After the release

- [ ] Verify on PyPI: `pip index versions kanbantool-mcp` or
  `curl https://pypi.org/pypi/kanbantool-mcp/json | jq '.info.version'`.
- [ ] Verify on GitHub: `gh release view vX.Y.Z`.
- [ ] Close `Closes #N` issues referenced in the changelog.
- [ ] If anything went sideways, file a v0.1.x follow-up issue.
