# CASCADE_STATE_2026-06-18 — Definitive Post-Ship Merge-Cascade State (v2)

Generated 2026-06-18 (rev 2 — adds session branches built after the initial doc).
All SHAs verified with `make git-show`, `make git-is-ancestor`, and
`make git-revlist-count` from the main repo (`/Users/shawnwilson/gludd`).
This document supersedes `POSTSHIP_MERGE_CASCADE_2026-06-18.md` for branch-state
facts; v2 supersedes the earlier version of this file.

---

## Context

Master tip: `3223c67` ("Add ai_parallel_dispatch role"). The ship commit is
`6063e51` ("fix(wave3): 5 mypy + SpendLimiter mock-tolerant guards + Makefile
venv guard").

**Critical: master is BEHIND 6063e51.**
`make git-is-ancestor A=3223c67 B=6063e51` → exit=0, confirming master is an
ancestor of 6063e51. The ship commit has NOT yet landed on master; it sits on
`feature/wave3-ship-final` (tip `bd4cddb`). All "ancestor-clean off 6063e51?"
checks below answer the question: "does this branch already contain 6063e51 in
its history?" A YES (exit=0) means the branch was built after or on top of the
ship commit. A NO (exit=1) means it was built before/parallel-to the ship commit
and needs a rebase or merge from the ship commit before landing on master.

`make git-revlist-count A=6063e51 B=master` → 0 unique to master side, 85 unique
to 6063e51 side (i.e., master is 85 commits behind the ship commit).

---

## A. Branch State Table

### A1. Known Branches (from previous session)

| # | Branch | Tip SHA | Ancestor-clean off 6063e51? | Alpha scope |
|---|--------|---------|----------------------------|-------------|
| 1 | `feature/batch3-security` | `85158c2` | **YES** (exit=0) | alpha.2 |
| 2 | `feature/floor-predictive-controller` | `68700c2` | **NO** (exit=1; 1 unique on 6063e51 side) | alpha.3 |
| 3 | `fix/security-D12-D33-spend-limiter` | `3e81a8d` | **YES** (exit=0) | alpha.3 |
| 4 | `security/D-07-D-08-D-34-webhook-hardening` | `5521cd6` | **NO** (exit=1; 1 unique on 6063e51 side) | alpha.3 |
| 5 | `worktree-agent-a247bddf57320b418` (batch-4 D-04/05/06/29/30/31) | `9456aed` | **NO** (exit=1; 1 unique on 6063e51 side) | alpha.3 |
| 6 | mt-6 at-rest watchdog | N/A | **STRANDED** (no branch found) | alpha.3 |

### A2. New Branches Built This Session

| # | Branch | Tip SHA | Ancestor-clean off 6063e51? | Notes | Alpha scope |
|---|--------|---------|----------------------------|-------|-------------|
| 7 | `feat/pg-0-1-2-guardrail-ports` | `93cb415` | **NO** (exit=1; 1 unique on 6063e51 side, 1 ahead) | PG-0 renderer wiring + PG-1 never-block-on-questions + PG-2 repair-not-disable in AgentBehavior + engine | alpha.3 |
| 8 | `feature/dogfood-e2e-alpha3` | `d896ddb` | **NO** (exit=1; 1 unique on 6063e51 side, 1 ahead) | Dogfood E2E harness scaffold + zai secrets bugfixes (uppercase env fallback, ZAI_BASE_URL alias) | alpha.3 |
| 9 | `feature/mainthread-budget-hook` | `86d037a` | **NO** (exit=1; 85 unique on 6063e51 side, 1 ahead) | Main-thread delegation-budget forcing function hook + scripts/agent_liveness.py + settings.json + Makefile | alpha.3 |
| 10 | `integration/batch45-testfix` | `d9b426d` | **NO** (exit=1; 85 unique on 6063e51 side, 37 ahead) | Stale test updates for batch-5 behavior (status host-path removal, webhook SSRF 422, asyncio loop fix) | alpha.3 |
| 11 | `fix/web-toolkit-review` | `2edfde3` | **NO** (exit=1; 85 unique on 6063e51 side, 39 ahead) | Web toolkit: SSRF/offline/crawler review fixes; Makefile typecheck-web + git-checkout-sha targets | alpha.3 |
| 12 | `feature/fix-budget-failclosed` | `e195bf9` | **NO** (exit=1; 16 unique on 6063e51 side, 1 ahead) | **SUPERSEDED** — `52ed89e` in wave3-ship-final explicitly supersedes this branch; do NOT merge independently | SUPERSEDED |
| 13 | `feat/ssrf-local-model-opt-in` | `bd4cddb` | N/A | **STRANDED** — tip is identical to feature/wave3-ship-final; no unique commits. Work not committed to a separate branch | alpha.3 |
| 14 | `feature/D-09-D-10-D-35-worker-hardening` | `bd4cddb` | N/A | **STRANDED** — tip is identical to feature/wave3-ship-final; no unique commits | alpha.3 |
| 15 | `feature/hardening-D44-D45-D46-D47` | `bd4cddb` | N/A | **STRANDED** — tip is identical to feature/wave3-ship-final; no unique commits | alpha.3 |
| 16 | `feature/p3-skill-id-benchmark` | `bd4cddb` | N/A | **STRANDED** — tip is identical to feature/wave3-ship-final; no unique commits | alpha.3 |
| 17 | `feature/security-batch4-rooted` | `cbefcf9` | **NO** | Contains only Makefile tooling commit (wait-pytest-idle); actual D-06/29/30/31 work is in row 5 (`9456aed`) | tooling-only |

### Branch Notes

**Branch 7 — `feat/pg-0-1-2-guardrail-ports` (93cb415).**
Files: `src/general_ludd/agents/behavior.py` (never_block_on_questions + repair_not_disable
fields + BehaviorRenderer sections), `src/general_ludd/execution/engine.py`
(`_build_system_prompt` wires `AgentBehavior` via `BehaviorRenderer`),
`tests/unit/test_agent_behavior.py` (TestNeverBlockOnQuestions + TestRepairNotDisable),
`tests/unit/test_engine_behavior_wiring.py` (new). Rooted 1 commit behind 6063e51;
trivial rebase.

**Branch 8 — `feature/dogfood-e2e-alpha3` (d896ddb).**
Files: `src/general_ludd/secrets/env.py` (uppercase env fallback + ZAI_BASE_URL alias
in `_ENV_VAR_ALIASES`), `.gitignore` (add `/.secrets/`), new
`tests/e2e/dogfood/__init__.py/_gateway.py/_secrets.py/_site.py/conftest.py/test_dogfood_todo_site.py/test_zai_connection.py`,
`tests/unit/test_zai_secrets_resolution.py`. Rooted 1 commit behind 6063e51.
`secrets/env.py` is a potential hotspot — see Section B.

**Branch 9 — `feature/mainthread-budget-hook` (86d037a).**
Files: `.claude/hooks/mainthread_budget.sh` (new), `.claude/settings.json` (new;
rewires all hook events including agent_floor_stop.sh as BLOCKING), `Makefile`
(adds `seed-mainthread-hook` target + `.PHONY` addition), `scripts/agent_liveness.py`
(new; upgraded with PROBE/TAIL logic and FLOOR_LIVE_OVERRIDE test seam),
`tests/unit/test_mainthread_budget_hook.py` (new; 18 tests). Rooted 85 commits
behind 6063e51 — built on a pre-ship base (3223c67 / batch5 era). Needs
rebase onto master post-ship. The `.claude/settings.json` is a NEW file (harness
config) — not a Python source conflict, but must be reviewed against any existing
settings that master carries.

**Branch 10 — `integration/batch45-testfix` (d9b426d).**
Files: `tests/e2e/test_hot_reload_e2e.py` (webhook header fix),
`tests/integration/test_enhanced_status_real.py` (filestore_available/config_file_count
field rename), `tests/integration/test_local_inference_integration.py`
(startup_timeout=0, killpg patch), `tests/unit/test_daemon.py` (field renames),
`tests/unit/test_daemon_coverage_lift.py` (field renames),
`tests/unit/test_daemon_endpoint_coverage.py` (URL updates),
`tests/unit/test_dispatch_permission_gate.py` (asyncio.run fix).
Rooted 85 commits behind 6063e51; built on a pre-ship base. 37 commits ahead of
6063e51 — this branch is large and may conflict with the ship commit's
daemon/status changes. Must verify no double-edit of the same test lines.

**Branch 11 — `fix/web-toolkit-review` (2edfde3).**
Files: `src/general_ludd/web/crawl.py` (PSL approximation documented + SSRF fixes),
`src/general_ludd/web/*.py` (SSRF/offline guards), `Makefile` (typecheck-web target,
git-checkout-sha target). Rooted 85 commits behind 6063e51; 39 commits ahead. Large
base divergence — rebase required post-ship. No known file overlap with other
alpha.3 branches.

**Branch 12 — `feature/fix-budget-failclosed` (e195bf9).**
Introduces `src/general_ludd/budget_guard_check.py` and wires it into
`review/reviewer.py` and `models/job_invocation.py`. The wave3-ship-final branch
already carries `52ed89e` which is explicitly documented as "superseding e195bf9"
and also carries `budget_guard_check.py` with an improved implementation (adds
`cast(Any, guard)` mypy fix). Merging e195bf9 on top of the ship commit would
re-introduce the older, weaker implementation. **Do not merge; mark CLOSED.**

**Branches 13-16 — STRANDED (tip = bd4cddb = feature/wave3-ship-final).**
`feat/ssrf-local-model-opt-in`, `feature/D-09-D-10-D-35-worker-hardening`,
`feature/hardening-D44-D45-D46-D47`, `feature/p3-skill-id-benchmark` all point
to the wave3-ship-final merge commit. The agents working these tasks either
(a) did not yet commit their work, (b) committed work directly to the wave3-ship-final
worktree branch without creating a named feature branch, or (c) were killed
before their gate-and-commit completed. Recovery steps per branch:
1. Locate the worktree: `make git-where` — find the `+` (checked-out) entry
   for the branch.
2. Enter the worktree and `make git-status` — check for uncommitted/unstaged changes.
3. If changes present: stage, gate, commit on the feature branch.
4. If no changes present: the work was not started or was rolled back.

**D-27/D-28 repository hardening** — Task description listed these but no named
branch was found. The batch-5 worktree branches include relevant commits:
- `worktree-agent-a911ef87b42afa0f3` (`1e37c1f`) "batch-5: db repo create
  field-allowlist + text size cap + optional list limit"
- `worktree-agent-a6c0523f2d048fe87` (`0a7af7f`) "batch-5: db models constraints
  (version_id_col + return_id FK/unique + blob CheckConstraints) — re-do of no-op
  branch"
These are worktree-agent branches (not yet promoted to named feature branches).

**D-16/D-19 dispatch/router** — `worktree-agent-a07182ad1ca03ddc5` (`65466a8`)
"batch-5: dispatch per-request call cap (422) + truncate caller name/kind
(log-injection)" appears to cover D-16/D-19.

**P4 spend-dispatch** — Not found as a named feature branch. Possibly covered by
`fix/security-D12-D33-spend-limiter` (row 3) or in an unnamed worktree branch.

---

## B. File-Overlap Conflict Matrix

Files touched by two or more in-scope branches, or by the ship commit (6063e51).
Branches 13–16 are stranded (no unique content) and excluded from this matrix.

| File | Branches touching it | Risk | Notes |
|------|---------------------|------|-------|
| `src/general_ludd/secrets/env.py` | Branch 8 (dogfood-e2e-alpha3) only | MEDIUM | Ship commit 6063e51 does NOT touch this file, but branch 8 is rooted 1 commit behind 6063e51. The rebase is trivial; no cross-branch conflict with other alpha.3 branches. |
| `src/general_ludd/agents/behavior.py` | Branch 7 (pg-0-1-2) only | LOW-SOLO | Additive fields + renderer sections. No other branch touches this. |
| `src/general_ludd/execution/engine.py` | Branch 7 (pg-0-1-2) only | LOW-SOLO | _build_system_prompt signature change. No other branch touches this. |
| `scripts/agent_liveness.py` | Branch 9 (mainthread-budget-hook) only | LOW-SOLO | New file. No other branch in scope creates this path. |
| `.claude/settings.json` | Branch 9 (mainthread-budget-hook) only | **MEDIUM** | New file introduced by branch 9. Master currently carries no `.claude/settings.json` (the file is untracked in master's working tree). If any other branch or manual session-level settings.json has been applied to the main worktree, a merge conflict can occur at the session config level (not a Python conflict). Verify master's `.claude/` state before merging branch 9. |
| `Makefile` | Branch 9 (adds seed-mainthread-hook + .PHONY) + ship commit 6063e51 (adds venv-check/git-hard-reset) + branch 11 (adds typecheck-web + git-checkout-sha) | **HIGH** | Three branches touch Makefile. After rebasing branches 9 and 11 onto post-ship master, the .PHONY list and target bodies must be hand-verified. No two branches touch the same targets, but .PHONY list merges are adjacent-prone. |
| `src/general_ludd/web/crawl.py` | Branch 11 (web-toolkit-review) only | LOW-SOLO | No other branch touches `src/general_ludd/web/`. |
| `tests/unit/test_daemon.py` | Branch 10 (batch45-testfix) + ship commit 6063e51 | **MEDIUM** | Ship commit touches `src/general_ludd/worker/server.py` (not the test file), but the batch45-testfix branch updates field-name assertions in `test_daemon.py` to match batch-5 behavior. Verify the ship commit's daemon router changes are compatible with the test field renames. |
| `tests/integration/test_enhanced_status_real.py` | Branch 10 (batch45-testfix) + ship commit path | **MEDIUM** | Branch 10 renames `config_dir/filestore_root` → `config_file_count/filestore_available`. The ship commit (6063e51) contains wave3 fixes; verify the status endpoint shape matches what branch 10 expects. |
| `src/general_ludd/db/repository.py` | Branch 1 (batch3-security) only | NONE | Fully isolated. |
| `src/general_ludd/routers/todos.py` | Branch 1 (batch3-security) only | NONE | Fully isolated. |
| `src/general_ludd/controllers/spend_limiter.py` | Branch 3 (D12-D33) + ship commit 6063e51 | MEDIUM | As documented in v1: branch 3 is ancestor-clean (exit=0), so this is already resolved. |
| `src/general_ludd/events/hooks.py` | Branch 4 (D-07-D-08-D-34) only | LOW-SOLO | Isolated. |
| `src/general_ludd/connectors/` (base.py/normalize.py/registry.py) | Branch 5 (batch-4) only | LOW-SOLO | Isolated. |
| `src/general_ludd/budget_guard_check.py` | Branch 12 SUPERSEDED + ship commit | **CLOSED** | Branch 12 (e195bf9) must NOT be merged; ship commit path already carries the authoritative implementation. |
| `tests/e2e/dogfood/` (new files) | Branch 8 (dogfood-e2e-alpha3) only | NONE | All new files; no conflict possible. |
| `tests/unit/test_zai_secrets_resolution.py` | Branch 8 (dogfood-e2e-alpha3) only | NONE | New file. |
| `tests/unit/test_mainthread_budget_hook.py` | Branch 9 only | NONE | New file. |

**Cross-branch conflict summary:**
- No two of the confirmed (non-stranded, non-superseded) new branches (7–11)
  touch the same Python source file as each other.
- Makefile is the only cross-branch conflict surface: branches 9 and 11 both
  add Makefile targets (and the ship commit does too). Sequence them adjacently
  in Section C.
- `tests/unit/test_daemon.py` and `tests/integration/test_enhanced_status_real.py`
  in branch 10 may conflict with the ship commit's daemon behavior — verify after
  merge.

---

## C. Recommended Gated-Merge Order for alpha.3

Pre-step (same as v1): confirm `make git-is-ancestor A='6063e51' B='master'` →
exit=0 (ship has landed). All rebases target the CURRENT master tip post-ship.

### Pre-step: Rebase Branches NOT Ancestor-Clean

Branches 2, 4, 5, 7, 8 need trivial rebases (each has only 1 commit unique to
the 6063e51 side). Branches 9, 10, 11 need rebases from 85-commit-behind bases
(larger surface; potential Makefile conflicts for 9 and 11, test field conflicts
for 10).

| Branch | Tip | Commits unique to 6063e51 side | Expected conflict |
|--------|-----|-------------------------------|-------------------|
| `feature/floor-predictive-controller` | 68700c2 | 1 | Makefile .PHONY — LOW |
| `security/D-07-D-08-D-34-webhook-hardening` | 5521cd6 | 1 | None expected |
| `worktree-agent-a247bddf57320b418` | 9456aed | 1 | None expected |
| `feat/pg-0-1-2-guardrail-ports` | 93cb415 | 1 | None expected |
| `feature/dogfood-e2e-alpha3` | d896ddb | 1 | None expected |
| `feature/mainthread-budget-hook` | 86d037a | 85 | Makefile + `.claude/settings.json` |
| `integration/batch45-testfix` | d9b426d | 85 | Daemon test field names |
| `fix/web-toolkit-review` | 2edfde3 | 85 | Makefile typecheck-web/git-checkout-sha |

### Step 1 — Merge `feature/batch3-security` (alpha.2, already ancestor-clean)

```text
make gated-merge BASE=master BRANCHES='feature/batch3-security' MANIFEST='/tmp/cascade-batch3.txt'
make gate
```

Risk: NONE. Isolated files.

### Step 2 — Merge `fix/security-D12-D33-spend-limiter` (already ancestor-clean)

```text
make gated-merge BASE=master BRANCHES='fix/security-D12-D33-spend-limiter' MANIFEST='/tmp/cascade-spend.txt'
make gate
```

Risk: LOW. Verify spend_limiter.py post-merge (additive to ship commit's record() guard).

### Step 3 — Merge `security/D-07-D-08-D-34-webhook-hardening` (rebase first)

Rebase onto post-ship master, then:

```text
make gated-merge BASE=master BRANCHES='security/D-07-D-08-D-34-webhook-hardening' MANIFEST='/tmp/cascade-webhook.txt'
make gate
```

Risk: NONE (hooks.py not touched by any other branch or ship commit).

### Step 4 — Merge `feat/pg-0-1-2-guardrail-ports` (rebase first, 1 commit behind)

Rebase onto post-ship master, then:

```text
make gated-merge BASE=master BRANCHES='feat/pg-0-1-2-guardrail-ports' MANIFEST='/tmp/cascade-pg012.txt'
make gate
```

Risk: NONE (behavior.py, engine.py isolated from all other branches).

### Step 5 — Merge `feature/dogfood-e2e-alpha3` (rebase first, 1 commit behind)

Rebase onto post-ship master. Post-rebase: verify `secrets/env.py` uppercase
fallback logic is additive (ship commit does NOT touch env.py), then:

```text
make gated-merge BASE=master BRANCHES='feature/dogfood-e2e-alpha3' MANIFEST='/tmp/cascade-dogfood.txt'
make gate
```

Risk: LOW (secrets/env.py rebase trivial; test files all new).

### Step 6 — Merge batch-4 connector/gateway (`worktree-agent-a247bddf57320b418`, rebase first)

See v1 Section C Step 4 for procedure. Rename to `feature/security-batch4-final` if desired.

Risk: NONE (connectors/* isolated).

### Step 7 — Merge `integration/batch45-testfix` (rebase first, large base divergence)

Rebase from 85-commit-behind base. Pay close attention to test files that
overlap with ship commit's daemon status changes:
- `tests/unit/test_daemon.py` — field rename assertions (config_file_count /
  filestore_available). These must match the ship commit's actual field names.
- `tests/integration/test_enhanced_status_real.py` — same field renames.

After rebase, run `make test-unit TESTFILE='tests/unit/test_daemon.py'` before
gated-merge.

```text
make gated-merge BASE=master BRANCHES='integration/batch45-testfix' MANIFEST='/tmp/cascade-b45test.txt'
make gate
```

Risk: MEDIUM (test field names vs. ship commit behavior).

### Step 8 — Merge `fix/web-toolkit-review` (rebase first, large base divergence) — sequence BEFORE branch 9

Web toolkit touches Makefile (typecheck-web + git-checkout-sha). Do this before
the mainthread-budget-hook branch so Makefile conflicts are resolved in two
smaller steps rather than one.

```text
make gated-merge BASE=master BRANCHES='fix/web-toolkit-review' MANIFEST='/tmp/cascade-webtoolkit.txt'
make gate
```

Risk: LOW-MEDIUM. Makefile additions are additive; web/crawl.py isolated.

### Step 9 — Merge `feature/mainthread-budget-hook` (rebase first, large base divergence) — HIGHEST RISK

Rebase from 85-commit-behind base. After rebase, verify:
1. Makefile: `seed-mainthread-hook` target + `.PHONY` list entry is present and
   not duplicated.
2. `.claude/settings.json`: confirm the new file is not clobbering any existing
   session settings on master. If master already has `.claude/settings.json` from
   another source, do a manual merge.
3. `scripts/agent_liveness.py`: new file — no conflict.
4. `tests/unit/test_mainthread_budget_hook.py`: new file — no conflict.

```text
make gated-merge BASE=master BRANCHES='feature/mainthread-budget-hook' MANIFEST='/tmp/cascade-mainthread.txt'
make gate
```

Risk: MEDIUM-HIGH (Makefile + `.claude/settings.json` require post-rebase review).

### Step 10 — Merge `feature/floor-predictive-controller` (rebase first)

Same as v1 Section C Step 5. Last in sequence because it also touches Makefile.

```text
make gated-merge BASE=master BRANCHES='feature/floor-predictive-controller' MANIFEST='/tmp/cascade-floor.txt'
make gate
```

Risk: MEDIUM (Makefile). All other files isolated.

### Step 11 — Recover STRANDED branches (manual intervention required)

See Section D for recovery procedure for branches 13–16 and mt-6 watchdog.

---

## D. Flagged Issues

### FLAG 1: Ship commit NOT on master — cascade cannot start until ship lands

Master (`3223c67`) is an ancestor of the ship commit (`6063e51`). The ship commit
lives on `feature/wave3-ship-final` (tip `bd4cddb`). Before ANY cascade step,
the ship must fast-forward master:

```text
make git-checkout MSG='master'
make git-ff-only   # or: make ship-ff REF=feature/wave3-ship-final
make git-is-ancestor A='6063e51' B='master'   # expect exit=0
```

### FLAG 2: `feature/fix-budget-failclosed` (e195bf9) — CLOSED/SUPERSEDED

The wave3-ship-final branch already carries `52ed89e` which explicitly supersedes
`e195bf9`. Do NOT merge. Close the branch.

### FLAG 3: Four STRANDED branches (tips = bd4cddb)

`feat/ssrf-local-model-opt-in`, `feature/D-09-D-10-D-35-worker-hardening`,
`feature/hardening-D44-D45-D46-D47`, `feature/p3-skill-id-benchmark` all point
to the wave3-ship-final merge commit. Each must be:
1. Located: `make git-where` — find the `+` entry (active worktree).
2. Inspected: enter the worktree, `make git-status` — check for uncommitted work.
3. Either committed/rebased onto a feature branch, or written off as not-started.

### FLAG 4: mt-6 at-rest watchdog (from v1) — still STRANDED

No branch or commit message matching "watchdog", "mt-6", or "at-rest" found in
the full branch list. Work is permanently stranded or never started.

### FLAG 5: Branches NOT ancestor-clean (trivial 1-commit rebase)

Branches 2, 4, 5, 7, 8 each have exactly 1 commit unique to the 6063e51 side:
`6063e51` itself ("fix(wave3): 5 mypy + SpendLimiter mock-tolerant guards +
Makefile venv guard"). Once master fast-forwards to 6063e51 (FLAG 1 resolved),
the rebase for these five branches will be trivial — git will recognize the
commit as already reachable and skip it.

### FLAG 6: Branches with large base divergence (85 commits behind)

Branches 9, 10, 11 were rooted on pre-ship master (`3223c67` / batch-5 era).
They have 85 commits unique to the 6063e51 side. The rebases are riskier:
- Branch 9: `.claude/settings.json` + Makefile.
- Branch 10: Test field names (filestore_root → filestore_available, etc).
- Branch 11: Makefile.

Run `make test-unit` targeted at affected test files after each rebase before
executing the gated-merge.

### FLAG 7: Batch name mismatch (from v1) — `worktree-agent-a247bddf57320b418`

The actual D-04/05/06/29/30/31 work is in `worktree-agent-a247bddf57320b418`
(`9456aed`), not in `feature/security-batch4-rooted` (`cbefcf9`). Confirm with
the agent author that both "batch4 commit 1" (D-04/05) and "commit 2" (D-06/29/30/31)
are present on the `9456aed` branch before merging.

---

## E. Current master state vs. 6063e51

Master tip as of this report: `3223c67`. Master is an ancestor of `6063e51`
(confirmed via `make git-is-ancestor A=3223c67 B=6063e51` → exit=0). The cascade
cannot begin until master fast-forwards to `6063e51` (via `make ship-ff` or a
manual fast-forward from `feature/wave3-ship-final`).

After the ship, the rebase target for all NOT-clean branches is the post-ship
master tip, not just `6063e51`.

---

## F. Evidence Table

| Claim | Command | Result |
|-------|---------|--------|
| master is ancestor of 6063e51 | `make git-is-ancestor A=3223c67 B=6063e51` | exit=0 |
| master is 85 commits behind 6063e51 | `make git-revlist-count A=6063e51 B=3223c67` | 85 unique to A, 0 ahead |
| 85158c2 ancestor-clean | `make git-is-ancestor A=6063e51 B=85158c2` | exit=0 |
| 3e81a8d ancestor-clean | `make git-is-ancestor A=6063e51 B=3e81a8d` | exit=0 |
| 68700c2 NOT ancestor-clean | `make git-is-ancestor A=6063e51 B=68700c2` | exit=1 |
| 5521cd6 NOT ancestor-clean | `make git-is-ancestor A=6063e51 B=5521cd6` | exit=1 |
| 9456aed NOT ancestor-clean | `make git-is-ancestor A=6063e51 B=9456aed` | exit=1 |
| 93cb415 NOT ancestor-clean | `make git-is-ancestor A=6063e51 B=93cb415` | exit=1 |
| 93cb415 divergence | `make git-revlist-count A=6063e51 B=93cb415` | 1 unique to A, 1 ahead |
| d896ddb NOT ancestor-clean | `make git-is-ancestor A=6063e51 B=d896ddb` | exit=1 |
| d896ddb divergence | `make git-revlist-count A=6063e51 B=d896ddb` | 1 unique to A, 1 ahead |
| 86d037a NOT ancestor-clean | `make git-is-ancestor A=6063e51 B=86d037a` | exit=1 |
| 86d037a divergence | `make git-revlist-count A=6063e51 B=86d037a` | 85 unique to A, 1 ahead |
| e195bf9 NOT ancestor-clean | `make git-is-ancestor A=6063e51 B=e195bf9` | exit=1 |
| e195bf9 divergence | `make git-revlist-count A=6063e51 B=e195bf9` | 16 unique to A, 1 ahead |
| d9b426d NOT ancestor-clean | `make git-is-ancestor A=6063e51 B=d9b426d` | exit=1 |
| d9b426d divergence | `make git-revlist-count A=6063e51 B=d9b426d` | 85 unique to A, 37 ahead |
| 2edfde3 NOT ancestor-clean | `make git-is-ancestor A=6063e51 B=2edfde3` | exit=1 |
| 2edfde3 divergence | `make git-revlist-count A=6063e51 B=2edfde3` | 85 unique to A, 39 ahead |
| feat/ssrf-local-model-opt-in tip = bd4cddb | `make git-where` branch list | tip = bd4cddb (same as wave3-ship-final) |
| feature/D-09-D-10-D-35-worker-hardening tip = bd4cddb | `make git-where` branch list | tip = bd4cddb (same as wave3-ship-final) |
| feature/hardening-D44-D45-D46-D47 tip = bd4cddb | `make git-where` branch list | tip = bd4cddb (same as wave3-ship-final) |
| feature/p3-skill-id-benchmark tip = bd4cddb | `make git-where` branch list | tip = bd4cddb (same as wave3-ship-final) |
| 6063e51 content | `make git-show MSG=6063e51` | fix(wave3): 5 mypy + SpendLimiter mock-tolerant guards + Makefile venv guard |
| bd4cddb content | `make git-show MSG=bd4cddb` | Merge commit 'b4598b3' into feature/wave3-ship-final |
| 93cb415 content | `make git-show MSG=93cb415` | feat(agents): port harness guardrails to gludd AgentBehavior |
| d896ddb content | `make git-show MSG=d896ddb` | feat(e2e): dogfood harness alpha.3 scaffold + zai secrets bugfixes |
| 86d037a content | `make git-show MSG=86d037a` | feat(hooks): main-thread delegation-budget forcing function + tests |
| d9b426d content | `make git-show MSG=d9b426d` | test: update stale tests to batch-5 secure behavior |
| e195bf9 content | `make git-show MSG=e195bf9` | fix(budget): shared budget_pre_check closes try_charge-only fail-open |
| 52ed89e content (supersedes e195bf9) | in wave3-ship-final revlist | fix(budget): budget_pre_check uses real non-mutating guard signatures + real-instance tests superseding e195bf9 |
