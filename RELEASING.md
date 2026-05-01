# Releasing

This file documents the cut-and-publish process for `kanbantool-mcp`.
The flow is GitHub Flow + tag-driven publish: ship from `main`, tag a
`v*` semver, the `Release` workflow does the rest.

## TL;DR

```bash
# On main, with everything you want in the release already merged.
# 1. Open a release PR.
git checkout -b release/vX.Y.Z

# 2. Bump the version. ONE place — pyproject.toml reads it dynamically.
$EDITOR src/kanbantool_mcp/__init__.py    # __version__ = "X.Y.Z"

# 3. Update CHANGELOG.md.
$EDITOR CHANGELOG.md                      # move [Unreleased] entries
                                          # under [X.Y.Z] - YYYY-MM-DD;
                                          # reset [Unreleased] stubs.

# 4. Smoke the build.
rm -rf dist/ && uv build
unzip -l dist/*.whl   # wheel should be src-only
tar tzf dist/*.tar.gz # sdist must NOT contain CLAUDE.md, .env, etc.

# 5. PR + merge.
git commit -am "release: prepare vX.Y.Z"
gh pr create -t "release: prepare vX.Y.Z" -b "..."
# … review, merge to main …

# 6. Tag and push. The Release workflow takes it from here.
git checkout main && git pull
git tag vX.Y.Z -m "vX.Y.Z — <one-line summary>"
git push origin vX.Y.Z
```

After the workflow completes:

```bash
# Verify and close associated issues.
curl -s https://pypi.org/pypi/kanbantool-mcp/json | jq '.info.version'
gh release view vX.Y.Z --repo VeryLongOrgNameSuchWow/kanbantool-mcp
gh issue close <Closes-N issues from the changelog> --comment "Done in vX.Y.Z"
```

## Pre-cut checklist

The v0.1.0 cut surfaced a handful of things at the very last gate.
Walk this checklist on the release PR — it's all stuff that's easy
to forget when you're focused on the changelog:

### Version

- [ ] `src/kanbantool_mcp/__init__.py`'s `__version__` matches the
  intended tag (no `.dev0`, no stale value).
- [ ] `pyproject.toml` is unchanged for the version (it's `dynamic`).

### Changelog

- [ ] `[Unreleased]` content moved under a new `[X.Y.Z] - YYYY-MM-DD`
  heading with today's date — not `YYYY-MM-DD`.
- [ ] `[Unreleased]` reset to empty `### Added` / `### Changed` /
  `### Fixed` stubs.
- [ ] Bottom-of-file link references updated (the `[Unreleased]: …`
  and `[X.Y.Z]: …` URLs).
- [ ] Every entry under `[X.Y.Z]` corresponds to something that
  actually shipped.

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
4. `softprops/action-gh-release` creates the GitHub release with
   auto-generated notes and attaches the wheel, sdist, and sigstore
   `*.publish.attestation` files.

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
