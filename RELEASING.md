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
| `docs` | patch | `### Documentation` |
| `refactor` | none[*] | (omitted) |
| `chore` | none | (omitted) |
| `ci` / `build` / `test` | none | (omitted) |

[*] release-please-action's default Python release-type bumps on
`feat:` (minor), `fix:` (patch), `docs:` (patch), and `perf:` (patch).
The other types (`refactor:`, `chore:`, `ci:`, `build:`, `test:`) are
recognised in commit messages but don't trigger a release PR. They also
don't appear in CHANGELOG by default — if you want them recorded, lift
them into a `fix:` (when behaviour-affecting) or use the `Release-As:`
escape hatch (see "Forcing a release" below) to tag a milestone-style
stretch.

The `refactor: doesn't bump` rule is what tripped us cutting v0.7.0
from M7 — three commits (`refactor:` + `test:` + `chore:`), zero auto-
bump candidates. v0.7.0 ended up via a `Release-As:` footer on an
empty commit. v0.7.1 then bumped automatically off a `docs:` PR.

A `!` after the type or a `BREAKING CHANGE:` footer triggers a major
bump (e.g. `feat!: drop py3.10` or `fix(server): swap error
code\n\nBREAKING CHANGE: clients must handle KanbanToolValidationError
now`). Pre-1.0, breaking changes can ship as minor or patch per
[SEMVER.md](SEMVER.md) — the `!` is reserved for the eventual major
gate.

The `Closes #N` footer is honoured separately by `release.yml`'s
auto-close step — see "Tag → publish flow" below.

## Forcing a release

If you have a stretch of commits that don't include a `feat:` or `fix:`
but you still want them shipped under a tagged version (e.g. an M7-style
"all polish, no new features" milestone), add a `Release-As: x.y.z`
footer to a single commit on `main`:

```bash
git commit --allow-empty -m "$(cat <<'EOF'
chore: trigger v0.7.0 release for accumulated polish

Release-As: 0.7.0
EOF
)"
git push
```

release-please picks up the footer, opens a release PR at the named
version, and the rest of the flow is identical to the auto-bump path.
Pick the version per `SEMVER.md` — minor for substantive cleanup, patch
for tightening.

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

## release-please authentication (GitHub App)

`release-please.yml` runs as the **kanbantool-mcp-release-please** GitHub
App, not as the workflow's default `GITHUB_TOKEN`. This is load-bearing,
and it's worth understanding why before touching the wiring.

### Why an App, not GITHUB_TOKEN

GitHub has an anti-loop rule: tags and PRs created by `GITHUB_TOKEN` do
not fire downstream workflows. With `release-please.yml` running as
`GITHUB_TOKEN`:

- The release PR it opens has **zero** CI runs (no `pull_request:`
  trigger fires for bot PRs from `GITHUB_TOKEN`). The strict main
  ruleset requires the `test (3.11/3.12/3.13)` checks to pass, so the
  PR can't merge without intervention.
- After the release PR is merged, the `vX.Y.Z` tag push from
  `release-please-action` does not fire `release.yml` either — so PyPI
  publish has to be `workflow_dispatch`-ed by hand.

App-minted installation tokens are not subject to the anti-loop rule.
Both pain points disappear with the App in place.

### Where the App lives

| Field | Value |
| --- | --- |
| App name | `kanbantool-mcp-release-please` |
| Owner | `@VeryLongOrgNameSuchWow` (org-level App) |
| Permissions | `contents: write`, `pull-requests: write`, `metadata: read` |
| Installed on | `kanbantool-mcp` only (single-repo scope) |
| Repo secrets | `RELEASE_PLEASE_APP_ID`, `RELEASE_PLEASE_APP_PRIVATE_KEY` |
| Workflow step | `actions/create-github-app-token@v2` (pinned by SHA in `release-please.yml`) |

### Rotating the private key

Required when the existing key is exposed, or as a routine annual rotation.

1. Org settings → Developer settings → GitHub Apps → `kanbantool-mcp-release-please` → **Generate a private key**. A new `*.pem` downloads.
2. Update the repo secret:
   ```bash
   gh secret set RELEASE_PLEASE_APP_PRIVATE_KEY \
     --repo VeryLongOrgNameSuchWow/kanbantool-mcp \
     < /path/to/new-key.pem
   ```
3. (Optional) On the App settings page, **Delete** the old private key once you've confirmed the new one works (the next `release-please.yml` run will).
4. Securely shred the local `*.pem` file (`shred -u`).

Do **not** commit the `.pem` to the repo. `*.pem` is gitignored as
defense-in-depth, but the secrets path is the only correct destination.

### Recovering if the App breaks

If the App is suspended, uninstalled, or its private key is rotated
without the secret being updated, `release-please.yml` will fail at the
`create-github-app-token` step. The workflow log surfaces the JWT decode
error clearly. Re-issue the key per "Rotating" above; no other workflow
runs are affected (CI on PRs uses `GITHUB_TOKEN`, which is independent).

If the App must be replaced entirely (e.g. ownership transfer), the
flow is the same as the original setup — see the original PR `#110`
for the full recipe.

## When a release PR has 0 CI checks

Symptom: `gh pr view` on the release PR shows `mergeable: MERGEABLE,
state: BLOCKED` with no `test (3.X)` rows. This shouldn't happen post-
App-auth, but if it does (e.g. release-please ran during an App outage
and fell back to `GITHUB_TOKEN`), there are two break-glass paths:

### Empty commit on the bot's branch

Cleanest — actually proves CI passes before merge:

```bash
git fetch origin
git checkout release-please--branches--main--components--kanbantool-mcp
git commit --allow-empty -m "ci: trigger CI on release PR"
git push
git checkout main
```

CI fires, ruleset is satisfied, merge normally.

### Admin bypass (faster, skips CI)

The main ruleset has `Repository admin` as a bypass actor (added in
ruleset id `15784452`). Repo admins can `gh pr merge --admin`:

```bash
gh pr merge <release-pr-number> --squash --delete-branch --admin \
  --repo VeryLongOrgNameSuchWow/kanbantool-mcp
```

Use this only when you know the diff is identical to the
release-please intent (`__version__` bump + `CHANGELOG.md` prepend) and
no separate verification of the test matrix is needed. Otherwise prefer
the empty-commit route.

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
