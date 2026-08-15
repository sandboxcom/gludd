# Development → master merge plan

Condensed copy-paste plan for promoting `development` → `master` and cutting
`v0.1.0-beta.2` once CI goes green. Canonical reference:
`docs/RELEASE_RUNBOOK.md` (this file is a quick-reference, not a replacement).

## Preconditions

- Working tree clean: `make git-status` shows nothing.
- Version bumped to `0.1.0-beta.2` in `pyproject.toml`,
  `src/general_ludd/__init__.py`, `CHANGELOG.md`, and the README status table
  (`**Status as of v0.1.0-beta.2 — <date>**`).
- `make check-readme-status TAG=v0.1.0-beta.2` exits 0.

## Execution order (run on the main checkout `/Users/shawnwilson/gludd`)

### 1. Verify CI is GREEN on `development`

```text
make ci-verdict-safe BRANCH=development
```

Require `conclusion: success` **and** `headSha == development tip`. If PENDING
→ resume other work, re-check at the next natural break (10-min cooldown).
Cancelled/no-run = treat as RED. If RED → fix-forward on `development`, do not
proceed.

### 2. Merge `development` → `master`

```text
make development-merge-to-master
```

Performs a `--no-ff` merge on the main checkout (never inside a worktree).
Requires CI green on `development`. After completion, verify the push landed:

```text
make verify-remote BRANCH=master SHA=$(make git-rev-parse REF=HEAD)
```

Expect `VERIFIED master@<sha>`. `REMOTE MISMATCH` → re-run the push, do not
proceed.

### 3. Cut the release

```text
make release-cut TAG=v0.1.0-beta.2 MSG='v0.1.0-beta.2: <one-line summary>'
```

The only sanctioned release path. Fail-closed at every step:
1. `require-ci-green` — aborts unless CI GREEN for the exact tagged SHA.
2. `check-readme-status` — README status table current for this tag.
3. push + annotated tag + push tag (triggers the CI release job).
4. `release-view` — confirm the GitHub Release exists.
5. poll, then `verify-release-completeness` — its exit code is the verdict.

`release-cut`'s local poll gives up after ~10 min. A cold tag-triggered
full-matrix build runs 30–60 min, so poll timeout means **still building**,
not failure. Proceed to step 4.

### 4. Verify completeness (the real gate)

```text
make verify-release-completeness TAG=v0.1.0-beta.2
```

Requires all 12 artifact categories (4 platform binaries, 4 packages, 4
metadata), prerelease flag matching `-beta`, version-stamped asset names, no
zero-size assets. Expect:

```text
COMPLETENESS CHECK: PASS — all 16 checks passed.
```

If CI is still building → wait and re-run. If CI is complete and assets
missing → broken release; see runbook §"If CI is red for the tag".

### 5. Confirm + record evidence

```text
make release-view TAG=v0.1.0-beta.2
```

Expect `isDraft: false`, `isPrerelease: true`, ≥12 assets. Paste the release
URL **and** the `COMPLETENESS CHECK: PASS` line into TASKS.md — without both,
the release task is not done.

## Rollback

Tagged SHA turned red after tagging, or assets incomplete on completed CI:
**do not back-fill locally-built binaries.** Either
`make release-recut TAG=v0.1.0-beta.2` (requires CI-green on the tag) or cut
`v0.1.0-beta.3` from a green SHA and mark beta.2 superseded in its notes.
