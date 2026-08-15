# Ship Runbook — `fix/self-update-sec`

**Branch:** `fix/self-update-sec`
**Prepared:** 2026-06-22
**Target merge:** `integration/alpha3-rc` via PR #2
**Release context:** these changes are post-ship relative to `v0.1.0-alpha.3`; they land after the alpha.3 tag is cut and its artifact is confirmed.

---

## 1. Ordered Steps to Get a Green CI Run and Ship

### Step 0 — Verify no local collection errors before pushing

Run `make test-count` in the worktree.  Zero collection errors is the minimum bar before any push.  A collection error means the CI gate will produce "0 failures" on a broken suite (the known `test-failures` mask trap documented in CLAUDE.md).

### Step 1 — Push via SSH to sandboxcom

The branch touches `.github/` (workflow files), so it must be pushed to the `sandboxcom` remote (the GitHub-connected remote), not a local-only mirror.

```text
make git-push-sandboxcom
```

After push, confirm the remote tip matches the local HEAD:

```text
make verify-remote BRANCH=fix/self-update-sec SHA=<local-HEAD>
```

A "VERIFIED" line is required.  "Everything up-to-date" without a SHA match is a silent no-op and must be treated as a failure.

Current HEAD as of this document (from `make git-log`):

```text
9d7bdb1  test+docs: provider_presets + adaptive_router unit tests, ci-live-failure-detection doc
```

### Step 2 — Open / update PR #2

PR #2 targets `integration/alpha3-rc`.  It was open at handoff (CI run `27919075093` — greenness unconfirmed).  After the push from Step 1, a new CI run will be triggered automatically.

If PR #2 needs to be opened:

```text
gh pr create --base integration/alpha3-rc --head fix/self-update-sec \
  --title "fix/self-update-sec: completion-integrity, security, hooks hardening" \
  --body "Post-ship fixes: budget_guard wiring, SSRF guard, daemon default_registry, no-wait hook, model-ratio enforcer."
```

### Step 3 — Watch for CI failures live

CI now runs sharded test jobs (commit `b99f205`) and emits per-test failure annotations mid-run (commit `608bd3a`).  The fastest path to a failing test is:

```text
make ci-poll         # streams job-level status; shows first failing shard within minutes
make ci-annotations  # polls check-run annotations every 30s for per-test ::error:: lines
```

Do NOT trust a single annotation fetch mid-run; poll repeatedly until the job reaches a terminal state.  The live-log REST endpoint returns 404 while a job is `in_progress` — log viewing only works post-completion (see `docs/ci-live-failure-detection.md`).

The concurrency group in `build.yml` (commit `666f168`) auto-cancels any superseded in-progress run when a new push lands.  Only the latest push's run counts as the verdict.

### Step 4 — Confirm green before merge

```text
make ci-verdict BRANCH=fix/self-update-sec
```

This prints the latest run's `headSha` alongside its conclusion.  A `STALE RUN WARNING` means the run predates the latest push — discard it and wait for the new run.  A run only counts when `headSha == local HEAD`.

Gate jobs CI runs on `fix/self-update-sec` include:
- **lint** — `ruff` check
- **typecheck** — `mypy` (baseline ≤ 0)
- **test-count** — collection-error gate
- **test-shard-{0..N}** — sharded pytest matrix (`fail-fast: false` so all shards surface failures)

All must be `conclusion: success` before merge.

### Step 5 — Merge into `integration/alpha3-rc`

Once `ci-verdict` shows green for the exact HEAD SHA, merge via the PR:

```text
gh pr merge 2 --squash --delete-branch
```

Or merge-commit if the project convention requires `--merge`.

After merge, run `make verify-remote BRANCH=integration/alpha3-rc SHA=<new-tip>` to confirm the merge landed.

---

## 2. CI Gates (what must be green)

| Gate | Tool | Fail means |
|------|------|-----------|
| `lint` | `ruff check src/ tests/` | Ruff violations — run `make lint-fix` then re-push |
| `typecheck` | `mypy` | Type errors — zero allowed (baseline is 0 on this branch) |
| `test-count` | `pytest --collect-only` | Collection error — test file imports broken |
| `test-shard-N` | `pytest` (sharded matrix) | Failing tests in that shard |

The shard matrix was introduced in commit `b99f205` so that the first failing shard surfaces within minutes of its tests running rather than after the full monolithic suite.  Individual shard conclusions are visible in `make ci-poll` output.

---

## 3. GHA-Minute Discipline

- **Push only believed-green candidates.** Run `make lint`, `make typecheck`, and `make test-count` locally before every push.  A red push wastes GHA minutes and is cancelled by the concurrency group the moment a fix-push follows.
- **Concurrency auto-cancel is live** (commit `666f168`): `build.yml` has a concurrency group scoped to the branch, so pushing a new SHA while a run is in progress auto-cancels the old run.  This means a series of small fix-pushes costs only the final run's minutes.
- **Sharding limits blast radius**: a failure in shard 2 cancels nothing by default (`fail-fast: false`) but surfaces immediately so you can diagnose without waiting for the other shards.
- **Rate-limit discipline for `ci-poll`/`ci-annotations`**: poll at 30s intervals.  Budget is ~5,000 req/hr; 30s polling burns ~1.2% of that per 30-minute run.  Back off to 60s if `X-RateLimit-Remaining` drops below ~500.

---

## 4. Known-Open Follow-Ups (not blocking ship of this branch)

These are in `docs/SESSION_HANDOFF_2026-06-22.md` § 4 and are tracked there.  Summary for completeness:

| Item | File | Status |
|------|------|--------|
| **D9 FK migration deploy precheck** | `alembic/versions/` (migration-002) | Open — `batch_alter_table` wrapper needed for SQLite; `task_decisions.return_id` FK + orphan precheck; cherry-pick 002-005 from `integration/alpha3-rc`, do not re-author |
| **Worker full tool dispatch** | `worker/app.py:99-107` | Open — `dispatcher=` never wired into EventLoop in the worker; mirror the daemon's `build_event_loop_mcp_dispatcher()` path |
| **Self-improve full wiring** | `loop.py`, `daemon.py`, `harness.py`, `applier.py` | Open (large) — `_phase_self_improve` uses static gap-analysis only; generation→`UpdateApplier.apply`+`SafeWriter`+`set_code_target`+`auto_queue:true` all unwired |
| **Registry seal** | `agents/registry.py:21-22` | Open — `register()` unsealed; `default_registry()` does not call `seal()` |
| **call_model_with_fallback health gate** | `models/gateway.py:715-740` | Open — health gate missing before `_try_call_model` in fallback path |

None of these block the `fix/self-update-sec` merge.  They are post-ship items for the next PR series.

---

## 5. Quick-Reference: Key Commits on This Branch

From `make git-log` (tip → base):

| SHA | Summary |
|-----|---------|
| `9d7bdb1` | test+docs: provider_presets + adaptive_router unit tests, ci-live-failure-detection doc |
| `c9ca19a` | fix(mcp): restore collision guard and align registry tests with security gate |
| `386d8ee` | fix(ci): fix 11 test failures from security hardening |
| `a1c160a` | docs+hooks: codify constraints-are-to-engineer-around at AGENTS.md + no-wait hook + tests |
| `b99f205` | ci: shard test job into parallel shards so failures surface per-shard in minutes |
| `608bd3a` | ci: live per-test failure annotations + annotations poller for in-progress runs |
| `8f71021` | ci: incremental job-level poller — surface the first failing job immediately |
| `137b4ed` | feat(hooks): opt-in force-delegate PreToolUse hook |
| `666f168` | ci: auto-cancel superseded in-progress runs via concurrency group |
| `22c9b6f` | chore: batch-2 — D9 FK migration, session handoff, AGENTS.md pipeline+codify guardrails |

---

## 6. Never

- Never call the merge "done" based on a tag alone — verify `make ci-verdict` headSha matches local HEAD.
- Never merge while any shard shows `conclusion: failure` or `status: in_progress`.
- Never skip `make verify-remote` after pushing — "Everything up-to-date" can silently no-op.
- Never skip `make test-count` before pushing — a collection error gives a false "0 failures" verdict.
