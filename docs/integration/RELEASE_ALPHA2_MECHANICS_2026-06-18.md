# Release mechanics — v0.1.0-alpha.2
_Authored: 2026-06-18. Evidence gathered read-only from the live repo._

---

## 1. Current version string — what to change

Two files define the version; both must be edited atomically:

| File | Line | Current value | Target value |
|------|------|---------------|--------------|
| `pyproject.toml` | 3 | `version = "0.1.0-alpha.202606120000"` | `version = "0.1.0-alpha.2"` |
| `src/general_ludd/__init__.py` | 3 | `__version__ = "0.1.0-alpha.202606120000"` | `__version__ = "0.1.0-alpha.2"` |

No `setup.cfg` or `_version.py` exists. The CI workflow (`build.yml` lines 46-47, 85-86, 120-121) injects the version from the tag automatically during CI builds using `sed`, so the files above are the only source-of-truth on the local/tagging side.

**PEP 440 note.** The tag is `v0.1.0-alpha.2`; CI strips the leading `v` (via `${GITHUB_REF_NAME#v}`) and injects `0.1.0-alpha.2` into both files. `0.1.0-alpha.2` is PEP 440 compliant (it maps to `0.1.0a2` internally but the hyphen form is also accepted by modern packaging). Confirm with `make version` after editing.

---

## 2. Existing tags

No tags exist in this repo. Running `make git-log` shows no `(tag:...)` decorations. The `.git/refs/tags/` directory is empty. There is **no `v0.1.0-alpha.1`** — this is the first tag release.

Tag naming convention: `v<semver>` prefix. The CI `build.yml` line 23 matches `refs/tags/v*` and the release job (line 228) is conditioned on `startsWith(github.ref, 'refs/tags/v')`. The annotated-tag target in the Makefile (line 807) uses `TAG=v0.1.0-alpha.N`. Therefore the correct tag for this release is:

```text
v0.1.0-alpha.2
```

Lightweight vs annotated: `git-tag-push` creates an **annotated** tag (`git tag -a`), which is the correct form for a release.

---

## 3. GitHub remote

Confirmed from `.git/config`:

```json
[remote "sandboxcom"]
    url = git@github.com:sandboxcom/gludd.git
    fetch = +refs/heads/*:refs/remotes/sandboxcom/*
```

Remote name: `sandboxcom`
URL: `git@github.com:sandboxcom/gludd.git`
Auth: `sandboxcom_github_rsa` (committed deploy key — **never print its contents**; `make git-tracked-keys` confirms it is tracked as `sandboxcom_github_rsa`)

All three sandboxcom Makefile targets (`git-push-sandboxcom`, `git-pull-sandboxcom`, `git-fetch-sandboxcom`, `git-tag-push`) prepend `GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new'`, so SSH auth requires that `sandboxcom_github_rsa` is present in the repo root (it is tracked) and readable by the shell.

`make git-push-sandboxcom` pushes the `master` branch only. Tags are pushed separately by `make git-tag-push`.

---

## 4. Relevant Makefile targets

### 4a. Targets that exist today (quote = Makefile line)

**`make gate`** — line 235
The 5-phase pre-release gate: lint → typecheck → collect → full test suite → smoke. Writes `.gate-status`; touches `.gate-failed` on any phase failure.
```makefile
gate:
    @rm -f .gate-failed
    @echo "=== GATE $(shell date -u +%Y-%m-%dT%H:%M:%SZ) ===" > .gate-status
    ...
    @if [ -f .gate-failed ]; then rm -f .gate-failed; exit 1; fi
    @echo "Gate: ALL PASSED"
```

**`make git-commit MSG='...'`** — line 1230
Gate-guarded commit: verifies `.gate-status` exists, all 5 checks are `PASS`, and the epoch is less than 30 minutes old before running `git commit`.

**`make git-add FILES='...'`** — line 664
Stage specific files. Use this to stage `pyproject.toml` and `src/general_ludd/__init__.py` after editing.

**`make git-push-sandboxcom`** — line 791
Pushes `master` branch to `sandboxcom/gludd` via the deploy key.
```makefile
git-push-sandboxcom:
    @GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push -u sandboxcom master
    @echo "Pushed to sandboxcom/gludd"
```

**`make git-tag-push TAG=v0.1.0-alpha.2 MSG='...'`** — line 806
Creates an annotated tag locally and pushes it to `sandboxcom`. This is what triggers the `release` CI job.
```makefile
git-tag-push:
    @[ -n "$(TAG)" ] || { echo "Usage: make git-tag-push TAG=v0.1.0-alpha.N [MSG='...']"; exit 1; }
    @git tag -a "$(TAG)" -m "$(if $(MSG),$(MSG),$(TAG))"
    @GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push sandboxcom "$(TAG)"
    @echo "Pushed tag $(TAG) to sandboxcom/gludd (triggers release job)"
```

**`make release-view TAG=v0.1.0-alpha.2`** — line 817
After CI completes, confirms the published GitHub Release and lists its downloadable assets.

**`make ci-watch-head`** — line 1107
Discovers the CI run for the current git HEAD and polls it to run-level conclusion. Use to monitor the CI pipeline after pushing the tag.

**`make ci-wait-anon RUN=<id>`** — line 1073
Polls a specific run id until conclusion is terminal. Exits non-zero on any non-`success` conclusion.

**`make ci-status-anon`** — line 1040
Unauthenticated API probe of recent runs (works while the repo is public).

### 4b. Targets that do NOT exist and must be added (or avoided)

- There is **no `make release`** or **`make bump-version`** target. Version edits must be done manually with the Edit tool, then staged via `make git-add`.
- There is **no `make list-tags`** target. Tags can only be inspected via `make git-log` (shows `(tag:...)` decorations when present) or via the unauthenticated GitHub API (`make ci-status-anon` / `make gh-tags REPO=sandboxcom/gludd`).
- There is **no `make tag-and-release`** composite target. The steps must be run in sequence as documented in Section 6 below.

If a `make list-tags` target is desired, add to `Makefile`:
```makefile
list-tags:
    @git tag --list --sort=-creatordate | head -20
```

---

## 5. CHANGELOG

`CHANGELOG.md` exists and tracks changes in Keep-a-Changelog format. The current `[Unreleased]` section (lines 5-38) covers the work for this release. Before committing the version bump, rename that section header from:

```markdown
## [Unreleased] — next alpha — 2026-06-17
```

to:

```markdown
## [0.1.0-alpha.2] — 2026-06-18
```

and add a new blank `[Unreleased]` block above it for future work. Stage `CHANGELOG.md` alongside `pyproject.toml` and `__init__.py`.

---

## 6. Release-gating preconditions

### Gate must be green
`make git-commit` refuses to run if `.gate-status` is missing, any phase is not `PASS`, or the epoch is older than 1800 seconds (30 min). `make git-tag-push` does NOT itself check the gate — the operator is responsible for ensuring the commit being tagged was gate-gated.

CI `build.yml` lines 31-54: the `gate` job runs on every push; the `release` job (`needs: [version, gate, linux, macos, windows, termux]`, line 225) only fires when the gate, all build jobs pass, AND the ref is a `v*` tag. CI enforces gating server-side.

**Current CI state (as of 2026-06-18):** The last four runs on master are `failure`. Gate must pass locally and CI must pass on the commit being tagged before the release tag is pushed. Do not push the tag until master CI is green.

Use `make ci-status-anon` to check CI status (public API, no auth needed).

### Force-push is forbidden
`AGENTS.md` policy + standard practice: never force-push. `make git-push-sandboxcom` uses a plain `git push -u`, no `--force`. The deploy key on `sandboxcom/gludd` should have branch protection enabled for `master`; even if it does not, agents must not issue force-push commands.

### Tag push pushes ONLY the tag
`make git-tag-push TAG=v0.1.0-alpha.2 MSG='...'` pushes only the named tag ref, not the branch. Run `make git-push-sandboxcom` first (to push the version-bump commit), then `make git-tag-push` (to push the tag that triggers the release job).

---

## 7. Ordered command sheet — how to cut v0.1.0-alpha.2

Run each step in sequence. Bash is make-only in this repo; version string edits use the Edit tool.

### Step 0 — verify the gate is green locally
```text
make gate-status
```
If `.gate-status` shows any `FAIL` or is older than 30 min, run `make gate` first (takes ~16 min).

### Step 1 — edit the two version files
Using the Edit tool (not shell), change both files:

**`src/general_ludd/__init__.py` line 3:**
```python
# from:
__version__ = "0.1.0-alpha.202606120000"
# to:
__version__ = "0.1.0-alpha.2"
```

**`pyproject.toml` line 3:**
```toml
# from:
version = "0.1.0-alpha.202606120000"
# to:
version = "0.1.0-alpha.2"
```

### Step 2 — update CHANGELOG.md
Using the Edit tool, rename `## [Unreleased] — next alpha — 2026-06-17` to `## [0.1.0-alpha.2] — 2026-06-18` and add a new `## [Unreleased]` block above it.

### Step 3 — verify the version string
```text
make version
```
Expected output: `general-ludd-agent 0.1.0-alpha.2`

### Step 4 — stage the three changed files
```text
make git-add FILES='pyproject.toml src/general_ludd/__init__.py CHANGELOG.md'
```

### Step 5 — commit (gate-guarded)
```text
make git-commit MSG='release: bump version to 0.1.0-alpha.2'
```
This will fail if the gate is not fresh and green. If it fails, re-run `make gate`, then retry.

### Step 6 — push master to sandboxcom
```text
make git-push-sandboxcom
```
Pushes the version-bump commit to `git@github.com:sandboxcom/gludd.git`. Triggers a CI branch run (gate + builds). Wait for that run to be green before tagging.

### Step 7 — wait for CI to be green on the pushed commit
```text
make ci-watch-head
```
Or poll manually:
```text
make ci-status-anon
```
Proceed to Step 8 ONLY when the run for this commit shows `success`.

### Step 8 — create and push the annotated tag
```text
make git-tag-push TAG=v0.1.0-alpha.2 MSG='v0.1.0-alpha.2 — first tagged alpha release'
```
This creates a local annotated tag and pushes it to `sandboxcom`. The push triggers the CI `release` job (`if: startsWith(github.ref, 'refs/tags/v')` — `build.yml` line 228), which builds Linux/macOS/Windows/Termux artifacts and publishes a prerelease GitHub Release.

### Step 9 — monitor the release CI run
```text
make ci-watch-head
```
The new run (triggered by the tag push) runs all jobs including `release`. It should end with `success`.

### Step 10 — confirm the GitHub Release was published
```text
make release-view TAG=v0.1.0-alpha.2
```
Expected output: `RELEASE: v0.1.0-alpha.2 | https://github.com/sandboxcom/gludd/releases/tag/v0.1.0-alpha.2` with `draft=False prerelease=True` and 4+ artifact files listed.

---

## 8. Push feasibility assessment

Push IS possible given:
- Remote `sandboxcom` is configured in `.git/config`.
- `sandboxcom_github_rsa` is tracked and present in the repo root.
- `make git-push-sandboxcom` and `make git-tag-push` both set `GIT_SSH_COMMAND` to use this key.
- The repo at `git@github.com:sandboxcom/gludd.git` is public (unauthenticated API calls succeed in CI runs listed above).

**Blocker: CI is currently red.** The last four pushes to master all resulted in `failure` (runs 27659731530, 27651456901, 27649531205, 27605235180 as of 2026-06-17). Do not push the `v0.1.0-alpha.2` tag until master CI is green on the version-bump commit. Fix the failing gate first (`make gate` locally, then push).

**No `gh` auth needed for push.** The SSH key handles authentication. `gh` is only needed for `make release-view` (which calls `gh release view`); `make ci-status-anon` works without auth.

---

## 9. Summary of files to change

| File | Change |
|------|--------|
| `pyproject.toml` | `version` field: `0.1.0-alpha.202606120000` → `0.1.0-alpha.2` |
| `src/general_ludd/__init__.py` | `__version__`: same change |
| `CHANGELOG.md` | Rename `[Unreleased]` section to `[0.1.0-alpha.2] — 2026-06-18` |

No other files require editing. CI injects the version into its own checkout from the tag name — the committed values are for local tooling (`make version`, `make dist`, etc.) only.
