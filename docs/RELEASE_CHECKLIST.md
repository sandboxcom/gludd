# Release Checklist

A step-by-step checklist for cutting a release. Every step is gated — do not
skip ahead. A release is an artifact, not a tag: it is not done until
`make verify-release-artifact` passes.

## 1. Pre-flight

- [ ] Version bumped **everywhere**: `pyproject.toml`, `src/general_ludd/__init__.py`, `CHANGELOG.md`.
- [ ] README status current: the **Feature & Task Completion Status** table and the
      `**Status as of <version>**` line reflect the version being cut.
      Verify with `make check-readme-status TAG='<tag>'`.
- [ ] `CHANGELOG.md` updated with the new version's entries.
- [ ] Working tree clean and committed (`make git-status`).

## 2. CI must be GREEN

- [ ] `make ci-verdict BRANCH=master` reports `conclusion: success`.
- [ ] The run's `headSha` **equals** the local HEAD of `master` (no `STALE RUN WARNING`).
- [ ] A stale run (headSha != branch tip) does NOT count — wait for a fresh green run.

## 3. Cut the release

- [ ] `make release-cut TAG='<tag>' MSG='<message>'`

  This runs, aborting on the first failure:
  1. `check-readme-status` — README stale = ABORT
  2. `require-ci-green` — CI not green = ABORT
  3. `git-push-sandboxcom` — push master
  4. `git-tag-push` — annotated tag + push (triggers the CI release job)
  5. `verify-release-artifact` — confirm published assets

## 4. Verify the artifact

- [ ] `make verify-release-artifact TAG='<tag>'` exits 0 (PASS).
- [ ] `gh release view <tag>` shows `isDraft: false` and `assets: N` (N >= 1).
- [ ] Record the artifact download URL(s) and the CI run id.

A tag with zero assets is **NOT shipped** — fix CI, cut a new release, do not
bump the version.

## 5. Post-release

- [ ] Tick the release row in `TASKS.md` with evidence: artifact URL, CI run id + `conclusion: success`, commit hash.
- [ ] Update `SESSION.md`: last commit hash, completed objective, next steps.
- [ ] Push any docs-only changes (`make batch-push`).
- [ ] Do NOT open the next-version epic until this version's artifact is confirmed.
