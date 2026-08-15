# Ship Execution Checklist — 2026-06-18

Trigger: run this the instant the 6063e51 ship gate returns green.

## VERIFIED LINEAGE (2026-06-18, confirmed this session)

| Ref | SHA | Notes |
|-----|-----|-------|
| Local master | `3223c67` | `make git-where` — confirmed |
| Remote master (`sandboxcom`) | `4314a6c` | PUBLISHED; not `3223c67` as previously assumed |
| Ship tip | `6063e51` | The ref being shipped |
| Next tag | `v0.1.0-alpha.2` | Remote already has `0.1.0-alpha.1` + `v0.1.0-alpha.1` |

**Ancestor chain (all verified exit=0):**
- `3223c67` (local master) → ancestor of `6063e51` ✓
- `4314a6c` (remote master) → ancestor of `6063e51` ✓ (given by session)
- `3223c67` is NOT an ancestor of `4314a6c` (exit=1) — the two are diverged siblings both under `6063e51`

**No force-push is ever needed.** Advancing master to `6063e51` is a clean fast-forward over BOTH the local `3223c67` and the remote `4314a6c` because `6063e51` is a descendant of both. The push of local master (→ `6063e51`) over remote `4314a6c` is a clean ff.

---

## CRITICAL FINDING: ship-async ALWAYS re-runs the full gate

`scripts/ship_async.sh` (invoked by `make ship-async`) runs `bash scripts/run_gate.sh`
unconditionally before doing the ff-only merge. There is no SKIP_GATE or pre-cleared
flag. The gate that already passed on 6063e51 is NOT honored — ship-async will run the
gate again from scratch on whatever is checked out when you invoke it. Budget 16–20 min
for the gate phase.

If you want to skip the re-gate (because you trust the already-passed gate on 6063e51),
you must do the ff-only merge manually in two steps (documented in step 2 below as
the "manual FF path"). The `ship-async` path is the safe/automated path but costs a
full gate re-run.

---

## Step 1 — Pre-ship safety: confirm master is STILL 3223c67 and is an ancestor of 6063e51

**Makefile target:** `git-is-ancestor` (line 604):
```text
make git-is-ancestor A=3223c67 B=6063e51
```
**Expected output:** `exit=0`
**Abort condition:** `exit=1` — master has moved ahead of 3223c67 OR 3223c67 is not an ancestor of 6063e51; investigate before proceeding. Do not ship.

To confirm the current master SHA (so you can verify it is still 3223c67):
```text
make git-where
```
(Makefile line 545: prints HEAD, master short SHA, all branches, worktree list.)

Or:
```text
make git-log
```
(Shows the 10 most recent commits; confirm master tip is 3223c67.)

---

## Step 2 — Ship 6063e51 to master (fast-forward only, no merge commit)

### Option A — Automated path via ship-async (recommended; re-runs full gate)

**Makefile target:** `ship-async` (line 360):
```text
make ship-async REF=6063e51 TARGET=master
```
`ship_async.sh` does:
1. Acquires `/tmp/gludd-ship.lock` (refuses a second concurrent ship).
2. Runs `bash scripts/run_gate.sh` in full (lint + typecheck + collect + test + smoke).
   **This takes 16–20 minutes.** It is NOT skipped even though a gate already passed on 6063e51.
3. On gate PASS: `git checkout master` then `git merge --ff-only 6063e51`.
4. Verifies `rev-parse master == rev-parse 6063e51`; writes `SHIP PASS 6063e51` to `.ship-status`.

**Expected output (final line of `.ship-status`):** `SHIP PASS 6063e51`
**Abort condition:** `SHIP FAIL gate` (gate failed; master untouched) or `SHIP FAIL not-ff` (ff-only merge left master != 6063e51, which should be impossible if step 1 passed).

### Option B — Manual FF path (skips gate re-run; use only if you trust the 6063e51 gate result)

No single target does a bare ff-only merge. You must chain two targets:
```text
make git-checkout MSG='master'
```
(Makefile line 1293: `git checkout "$(MSG)"` — switches to master.)

Then manually confirm the gate is still fresh (`.gate-status` < 30 min old and all phases PASS), then run:
```text
make git-is-ancestor A=master B=6063e51
```
(Confirm exit=0 — i.e. master is still an ancestor of 6063e51 and FF is valid.)

There is no `git-ff-merge` or `git-merge-ff-only` target in the Makefile. The only
targets that merge are:
- `git-merge MSG='<branch>'` — does `git merge --no-ff` (creates a merge commit; NOT appropriate here).
- `feature-done MSG='<branch>'` — does `git merge --no-ff` plus dist; also NOT appropriate.
- `ship-async` — the only target that does `--ff-only` (inside `ship_async.sh` line 138).

**If you need a true ff-only merge without re-running the gate, you must add a new Makefile target.** Until then, Option A (ship-async) is the only make-compliant path.

---

## Step 3 — Post-ship verify: confirm master == 6063e51

```text
make git-show MSG="master"
```
(Makefile line 633: `git show "$(MSG)"` — shows the commit. Confirm first line is `commit 6063e5176401e95d1c311013913c23d655571b40`.)

Or use:
```text
make git-where
```
Confirm the `--- master ---` line shows `6063e51`.

**Abort condition:** master SHA does not match 6063e51 — ship did not complete; do not proceed to cascade.

---

## Step 4 — Gated-merge cascade

**Makefile target:** `gated-merge` (line 376):
```text
BASE='$(BASE)' BRANCHES='$(BRANCHES)' MERGE_STRATEGY='$(MERGE_STRATEGY)' MANIFEST='$(MANIFEST)' bash scripts/gated_merge.sh
```

### What gated-merge does (NOT a gate re-run)

`gated_merge.sh` does `git merge --no-ff <ref>` for each branch, then runs `make test-count`
(collection check only) after each successful merge. It does NOT run the full gate
(no lint/typecheck/test/smoke). Run `make gate` manually after each merge group to fully
verify. On conflict or collection failure it resets hard to `BASE`.

**MANIFEST is required.** Omitting it causes the script to exit 1 immediately.

### 4a — Prerequisite: verify ship landed (runbook section 1)

```text
make git-is-ancestor A="6063e51" B="master"
```
Expected: `exit=0`. If `exit=1`, the ship has not landed; do not run merges.

### 4b — Step 2 of runbook: merge feature/batch3-security (85158c2)

This branch is ancestor-clean off 6063e51 (3 commits ahead, 0 commits unique to ship).

FF pre-check (confirm master is an ancestor of the branch, so merge is clean):
```text
make git-is-ancestor A="master" B="feature/batch3-security"
```
Expected: `exit=0`.

Merge:
```text
make gated-merge BASE=master BRANCHES='feature/batch3-security' MANIFEST='/tmp/gludd-cascade-batch3.txt'
```
**Expected:** manifest at `/tmp/gludd-cascade-batch3.txt` contains `feature/batch3-security MERGED`.
**Abort condition:** `CONFLICT` or `FAIL` in the manifest — do not proceed; check manifest for which ref failed.

Post-merge full gate:
```text
make gate
```
**Abort condition:** any phase shows FAIL in `.gate-status`.

### 4c — Steps 3, 4, 5 of runbook: branches not yet built (async)

The following branches finish asynchronously. Do NOT merge until they exist and pass the
ancestor check against current master (not 6063e51 — master will have moved after 4b).

**Re-check-ancestry-against-current-master protocol for each:**

For each branch when it lands, run:
```text
make git-is-ancestor A="master" B="<branch-name>"
```
- If `exit=0`: branch is ancestor-clean off current master; proceed with `make gated-merge`.
- If `exit=1`: master has moved ahead of the branch's base. The branch needs to be rebased or merged. Use `make git-merge MSG="<branch>"` (--no-ff, creates merge commit) then `make gate`; OR ask the build agent to rebase the branch onto current master.

**floor_controller-consolidated** (not yet created — mt-7 + feature/floor-gate-safe consolidation):
```text
make git-is-ancestor A="master" B="floor_controller-consolidated"
```
If exit=0:
```text
make gated-merge BASE=master BRANCHES='floor_controller-consolidated' MANIFEST='/tmp/gludd-cascade-floor.txt'
make gate
```
Conflict risk on Makefile: HIGH. See runbook section 5 for resolution notes.

**feature/security-batch4** (not yet created — being built off 6063e51):
```text
make git-is-ancestor A="master" B="feature/security-batch4"
```
If exit=0:
```text
make gated-merge BASE=master BRANCHES='feature/security-batch4' MANIFEST='/tmp/gludd-cascade-batch4.txt'
make gate
```
Conflict risk on gateway.py: MEDIUM. Verify no line-level overlap with 6063e51 hunk in `_notify_profile_change` before merging.

**mt-6-watchdog** (not yet created — exact branch name TBD; try `feature/mt-6-watchdog`):
```text
make git-is-ancestor A="master" B="feature/mt-6-watchdog"
```
If exit=0:
```text
make gated-merge BASE=master BRANCHES='feature/mt-6-watchdog' MANIFEST='/tmp/gludd-cascade-mt6.txt'
make gate
```
Conflict risk: LOW.

**floor-predictive-controller (68700c2):** Same protocol — check ancestry against current master when the branch exists.

---

## Step 5 — Meta-work commit (AGENTS.md, hooks, Makefile, scripts, SESSION.md, BUGS.md, guardrail hooks)

### FLAG: Repoint mt-6/mt-7 SHAs in backlog JSON BEFORE staging

`scripts/multitasking_backlog.json` contains placeholder or stale builder commit SHAs for
mt-6 and mt-7. These MUST be updated to the real builder commit SHAs (from the actual
branches when they land) before the commit is made. Do not commit with stale SHAs.

### Pre-check: review Makefile diff before staging

The ship commit (6063e51) already changed Makefile (added `VIRTUAL_ENV`/`UV_PROJECT_ENVIRONMENT`
exports, `venv-check`, `git-hard-reset` targets). The working-tree Makefile changes must not
conflict with those additions. Run:

```text
make git-diff
make git-staged
```

Review before staging.

### Commit commands

Stage (after backlog JSON SHAs are repointed):
```text
make git-add FILES='AGENTS.md Makefile scripts/multitasking_backlog.json SESSION.md BUGS.md .claude/hooks/agent_floor_stop.sh'
```

If additional guardrail hook files exist (e.g. new hooks under `.claude/hooks/`), add them
to the FILES list before committing.

`make git-commit` enforces the 30-min gate freshness window (`.gate-status` must exist, all
phases must be PASS, and the gate epoch must be < 1800 seconds old). Run `make gate` if
the last gate is stale.

```text
make git-commit MSG='chore(orchestration): meta-work — AGENTS.md policy + Makefile targets + backlog + guardrail hooks'
```

**Expected output:** `Gate fresh and green. Committing...` followed by the commit hash.
**Abort condition:** `ERROR: .gate-status is <N> seconds old (>30 min)` — re-run `make gate` first. Or `ERROR: Gate <phase> not PASS` — fix the failing phase.


---

## Quick-reference command sequence (copy-paste order)

```markdown
# 1. Pre-ship safety
make git-is-ancestor A=3223c67 B=6063e51         # expect exit=0
make git-where                                    # confirm local master=3223c67

# 2. Ship (re-runs full gate, ~16-20 min)
make ship-async REF=6063e51 TARGET=master         # expect: SHIP PASS 6063e51 in .ship-status

# 3. Post-ship verify
make git-where                                    # confirm master=6063e51

# 4a. Cascade pre-check
make git-is-ancestor A="6063e51" B="master"       # expect exit=0

# 4b. Batch-3 security merge
make git-is-ancestor A="master" B="feature/batch3-security"   # expect exit=0
make gated-merge BASE=master BRANCHES='feature/batch3-security' MANIFEST='/tmp/gludd-cascade-batch3.txt'
make gate

# 4c. Async branches — when each lands:
make git-is-ancestor A="master" B="<branch>"      # exit=0 required before merging
make gated-merge BASE=master BRANCHES='<branch>' MANIFEST='/tmp/gludd-cascade-<name>.txt'
make gate

# 5. Version bump + meta-work commit (AFTER cascade + gate green)
#    Edit these three files before staging:
#      pyproject.toml line 3:              version = "0.1.0-alpha.2"
#      src/general_ludd/__init__.py line 3: __version__ = "0.1.0-alpha.2"
#      CHANGELOG.md:                       [Unreleased] -> [0.1.0-alpha.2]
#    Repoint stale mt-6/mt-7 SHAs in scripts/multitasking_backlog.json FIRST.
#    Then stage (NEVER git-add-all; exclude .commit-msg-*.txt, nested/, proj-ok/):
make git-add FILES='pyproject.toml src/general_ludd/__init__.py CHANGELOG.md AGENTS.md Makefile scripts/multitasking_backlog.json SESSION.md BUGS.md .claude/hooks/agent_floor_stop.sh docs/'
#    If additional guardrail hooks exist under .claude/hooks/ add them to FILES.
#    Commit is gate-guarded (30-min freshness window; re-run make gate if stale):
make git-commit MSG='chore(release): v0.1.0-alpha.2 — version bump + meta-work (AGENTS.md, hooks, Makefile, backlog)'

# 6. Final gate on master RC
make gate                                         # must be ALL PASSED before push

# 7. Push master to sandboxcom (clean ff over remote 4314a6c; no force-push)
make git-push-sandboxcom                          # pushes local master (6063e51 + meta commit) -> remote

# 8. Tag and push to trigger CI release job
make git-tag-push TAG=v0.1.0-alpha.2 MSG='Release v0.1.0-alpha.2: integration batch 3, ship pipeline, security hardening'
# Creates annotated tag at current master HEAD; pushes to sandboxcom; triggers release job.

# 9. Confirm published release
make release-view TAG=v0.1.0-alpha.2
# Expected output: RELEASE: v0.1.0-alpha.2 | <url>; draft=False; prerelease=True; ASSETS(N): ...
```

---

## Step 6 — Version bump (detail)

Edit these files manually (Read tool / Edit tool — NOT shell):

| File | Line | Old value | New value |
|------|------|-----------|-----------|
| `pyproject.toml` | 3 | `version = "0.1.0-alpha.1"` | `version = "0.1.0-alpha.2"` |
| `src/general_ludd/__init__.py` | 3 | `__version__ = "0.1.0-alpha.1"` | `__version__ = "0.1.0-alpha.2"` |
| `CHANGELOG.md` | first `[Unreleased]` heading | `## [Unreleased]` | `## [0.1.0-alpha.2] - 2026-06-18` |

---

## Step 7 — Push to sandboxcom (why no force-push is needed)

Local master after step 2 = `6063e51` + meta commit on top.
Remote master (`sandboxcom`) = `4314a6c`.
`4314a6c` is an ancestor of `6063e51` (verified session exit=0).
Therefore local master is STRICTLY AHEAD of remote master — `git push -u sandboxcom master` is a clean fast-forward, no `--force` flag.

```text
make git-push-sandboxcom
```
(Makefile line 791: `git push -u sandboxcom master` — no force flag in the target.)

---

## Step 8 — Tag + release

```text
make git-tag-push TAG=v0.1.0-alpha.2 MSG='Release v0.1.0-alpha.2: integration batch 3, ship pipeline, security hardening'
```
(Makefile line 806: creates annotated tag at HEAD, pushes to sandboxcom.)

Remote already has: `0.1.0-alpha.1`, `v0.1.0-alpha.1` — next tag MUST be `v0.1.0-alpha.2` (with leading `v` per convention).

Confirm release published:
```text
make release-view TAG=v0.1.0-alpha.2
```
(Makefile line 817: calls `gh release view ... -R sandboxcom/gludd`.)

---

## Target existence verification

All targets below are confirmed present in the Makefile. Quoted lines:

| Target | Makefile line | Confirmed |
|--------|---------------|-----------|
| `git-is-ancestor` | line 604: `git-is-ancestor:` | YES |
| `git-where` | line 544: `git-where:` | YES |
| `git-log` | line 627: `git-log:` | YES |
| `git-show` | line 630: `git-show:` | YES |
| `ship-async` | line 360: `ship-async:` | YES |
| `gated-merge` | line 376: `gated-merge:` | YES |
| `gate` | line 235: `gate:` | YES |
| `git-checkout` | line 1292: `git-checkout:` | YES |
| `git-merge` | line 1296: `git-merge:` | YES (--no-ff only) |
| `git-add` | line 664: `git-add:` | YES |
| `git-commit` | line 1230: `git-commit:` | YES (gate-guarded) |
| `git-diff` | line 554: `git-diff:` | YES |
| `git-staged` | line 559: `git-staged:` | YES |
| `test-count` | line 218: `test-count:` | YES |
| `git-push-sandboxcom` | line 791: `git-push-sandboxcom:` | YES |
| `git-tag-push` | line 806: `git-tag-push:` | YES |
| `release-view` | line 817: `release-view:` | YES |

**Targets NOT in the Makefile:**
- `gate-tail` — does NOT exist. Use `make gate-status` or Read `.gate-status` with the Read tool.
- `git-ff-merge` / `git-merge-ff-only` — do NOT exist. The only ff-only merge is inside `ship_async.sh` via `make ship-async`.

---

## META-COMMIT APPLY SEQUENCE — v0.1.0-alpha.2 release bundle

**Ordering constraint:** Execute this section AFTER `make ship-async` lands master at `6063e51` AND
after any cascade merges (Step 4) are done AND a fresh `make gate` returns all PASS.
Execute BEFORE `make release-cut`.

---

### Prerequisites (verify before any edit)

```text
make git-where          # confirm master == 6063e51 (or 6063e51 + cascade merge commits)
make gate               # confirm all phases PASS and gate epoch < 1800 s old
```

---

### STEP A — Version bump (exact edits — verified 2026-06-18)

**File 1: `pyproject.toml` line 3**

Current line (read-verified):
```text
version = "0.1.0-alpha.202606120000"
```
Change to:
```text
version = "0.1.0-alpha.2"
```

**File 2: `src/general_ludd/__init__.py` line 3**

Current line (read-verified):
```text
__version__ = "0.1.0-alpha.202606120000"
```
Change to:
```text
__version__ = "0.1.0-alpha.2"
```

Use the Edit tool (NOT shell) to make both changes. Do not touch any other line.

---

### STEP B — CHANGELOG rename (exact edit — verified 2026-06-18)

**File: `CHANGELOG.md` line 5**

Current heading (read-verified):
```markdown
## [Unreleased] — next alpha — 2026-06-17
```
Change to:
```markdown
## [0.1.0-alpha.2] - 2026-06-18
```

Note: the release-notes agent has already drafted content under this heading (lines 7–end of
the unreleased section). Only the heading line itself needs to change. Use the Edit tool.

---

### STEP C — README status line confirmation (gate requirement)

**File: `README.md` line 70**

Current line (read-verified — gate requirement already met):
```text
**Status as of v0.1.0-alpha.2 — 2026-06-18**
```
No edit needed. `make release-cut` grepped for this string and it is present.
If the README was subsequently edited, re-verify this line exists before staging.

---

### STEP D — `.claude/` tracking decision (FLAG — resolve before staging)

`make git-status` shows `.claude/` as `??` (untracked). `.gitignore` does NOT contain `.claude/`.

**Decision required (make it once, before staging):**

| Option | What ships | Implication |
|--------|-----------|-------------|
| **A — Track `.claude/`** | hooks + settings.json ship in the release tarball; enforce-make policy is reproduced for every clone | Recommended if policy hooks are intentional project infrastructure (not user-local). Adds ~10–20 KB. |
| **B — Gitignore `.claude/`** | hooks stay local-only forever; clones get no make-only enforcement unless manually copied | Choose only if hooks are intentionally personal/IDE-local config. Add `.claude/` to `.gitignore` before committing. |

**Default assumption (act on this unless overridden):** Track `.claude/`. The `enforce-make.ts` /
`enforce-floor.ts` hooks are project policy codified in `CLAUDE.md` and `AGENTS.md` — they belong
in the repo. Include `.claude/` in the FILES list below.

If you choose Option B instead: add `.claude/` to `.gitignore`, stage `.gitignore`, and remove
`.claude/` from the FILES list.

---

### STEP E — Exact `make git-add` command

**IMPORTANT:** Stage in two passes if `scripts/multitasking_backlog.json` still has stale
mt-6/mt-7 placeholder SHAs — repoint those first (CLAUDE.md Step 5 FLAG), then stage.

**Files confirmed untracked/modified (from `make git-status` 2026-06-18):**

Modified (tracked, `M` status):
- `AGENTS.md`
- `BUGS.md`
- `Makefile`
- `README.md`
- `SESSION.md`
- `scripts/multitasking_backlog.json`

Untracked new files to include:
- `src/general_ludd/__init__.py` ← version bump (tracked file, modified after STEP A)
- `pyproject.toml` ← version bump (tracked file, modified after STEP A)
- `CHANGELOG.md` ← heading rename (tracked file, modified after STEP B)
- `scripts/check_readme_status_current.py`
- `scripts/gen_gate_safe_hook.py`
- `tests/unit/test_readme_status_gate.py`
- `docs/audit/BACKLOG_RECONCILED_2026-06-17.md`
- `docs/audit/BATCH2_SECURITY_PLAN_2026-06-18.md`
- `docs/audit/MEMORY_TO_HOOK_AUDIT_2026-06-18.md`
- `docs/audit/NEW_FINDINGS_2026-06-16.md`
- `docs/audit/NEW_FINDINGS_TRIAGE_2026-06-18.md`
- `docs/audit/SECURITY_AUDIT_BACKLOG_2026-06-17.md`
- `docs/audit/WAVE3_FIXPASS_PLAN_2026-06-18.md`
- `docs/audit/backlog_completeness_2026-06-16.md`
- `docs/audit/batch3_dedup_coherence.md`
- `docs/audit/feature_package_wiring_status.md`
- `docs/audit/floor_breach_rootcause_2026-06-17.md`
- `docs/audit/misconfig_detector_dedup_decision.md`
- `docs/audit/model_routing_coherence_check.md`
- `docs/design/connector_join_key_normalization.md`
- `docs/integration/BATCH3_APPLY_PLAN.md`
- `docs/integration/BATCH4_DEFERRED.md`
- `docs/integration/CASCADE_STATE_2026-06-18.md`
- `docs/integration/CYCLE_APPLY_PLAN_2026-06-17.md`
- `docs/integration/META_COMMIT_MANIFEST_2026-06-18.md`
- `docs/integration/NEXT_CYCLES_READY.md`
- `docs/integration/POSTSHIP_MERGE_CASCADE_2026-06-18.md`
- `docs/integration/POSTSHIP_RUNBOOK.md`
- `docs/integration/RELEASE_ALPHA2_MECHANICS_2026-06-18.md`
- `docs/integration/REVIEW_FINDINGS_2026-06-17.md`
- `docs/integration/SHIP_EXECUTION_CHECKLIST_2026-06-18.md` ← this file
- `docs/research/MODEL_ROUTING_RECOMMENDATION.md`
- `.opencode/plugin/enforce-floor.ts`
- `.claude/` ← IF Option A (track hooks); omit if Option B

**EXCLUDE (never stage):**
- `.commit-msg-batch2.txt`, `.commit-msg-batch3.txt`, `.commit-msg-batch3a.txt`,
  `.commit-msg-batch3b.txt`, `.commit-msg-cycleA.txt`, `.commit-msg-integration.txt`
- `nested/` (entire directory)
- `proj-ok/` (entire directory)
- `scripts/wave3_consolidate.sh` (shell script, not make-compliant — assess separately)
- `scripts/agent_liveness.py` ← not in `??` list from git-status; skip unless it appears
- Any `/tmp` artifacts

**Exact command (Option A — track `.claude/`):**

```text
make git-add FILES='pyproject.toml src/general_ludd/__init__.py CHANGELOG.md README.md AGENTS.md BUGS.md Makefile SESSION.md scripts/multitasking_backlog.json scripts/check_readme_status_current.py scripts/gen_gate_safe_hook.py tests/unit/test_readme_status_gate.py docs/audit/BACKLOG_RECONCILED_2026-06-17.md docs/audit/BATCH2_SECURITY_PLAN_2026-06-18.md docs/audit/MEMORY_TO_HOOK_AUDIT_2026-06-18.md docs/audit/NEW_FINDINGS_2026-06-16.md docs/audit/NEW_FINDINGS_TRIAGE_2026-06-18.md docs/audit/SECURITY_AUDIT_BACKLOG_2026-06-17.md docs/audit/WAVE3_FIXPASS_PLAN_2026-06-18.md docs/audit/backlog_completeness_2026-06-16.md docs/audit/batch3_dedup_coherence.md docs/audit/feature_package_wiring_status.md docs/audit/floor_breach_rootcause_2026-06-17.md docs/audit/misconfig_detector_dedup_decision.md docs/audit/model_routing_coherence_check.md docs/design/connector_join_key_normalization.md docs/integration/BATCH3_APPLY_PLAN.md docs/integration/BATCH4_DEFERRED.md docs/integration/CASCADE_STATE_2026-06-18.md docs/integration/CYCLE_APPLY_PLAN_2026-06-17.md docs/integration/META_COMMIT_MANIFEST_2026-06-18.md docs/integration/NEXT_CYCLES_READY.md docs/integration/POSTSHIP_MERGE_CASCADE_2026-06-18.md docs/integration/POSTSHIP_RUNBOOK.md docs/integration/RELEASE_ALPHA2_MECHANICS_2026-06-18.md docs/integration/REVIEW_FINDINGS_2026-06-17.md docs/integration/SHIP_EXECUTION_CHECKLIST_2026-06-18.md docs/research/MODEL_ROUTING_RECOMMENDATION.md .opencode/plugin/enforce-floor.ts .claude/'
```

**Alternate command (Option B — gitignore `.claude/`):**

First add `.claude/` to `.gitignore` and stage it, then:
```text
make git-add FILES='pyproject.toml src/general_ludd/__init__.py CHANGELOG.md README.md AGENTS.md BUGS.md Makefile SESSION.md scripts/multitasking_backlog.json scripts/check_readme_status_current.py scripts/gen_gate_safe_hook.py tests/unit/test_readme_status_gate.py docs/audit/BACKLOG_RECONCILED_2026-06-17.md docs/audit/BATCH2_SECURITY_PLAN_2026-06-18.md docs/audit/MEMORY_TO_HOOK_AUDIT_2026-06-18.md docs/audit/NEW_FINDINGS_2026-06-16.md docs/audit/NEW_FINDINGS_TRIAGE_2026-06-18.md docs/audit/SECURITY_AUDIT_BACKLOG_2026-06-17.md docs/audit/WAVE3_FIXPASS_PLAN_2026-06-18.md docs/audit/backlog_completeness_2026-06-16.md docs/audit/batch3_dedup_coherence.md docs/audit/feature_package_wiring_status.md docs/audit/floor_breach_rootcause_2026-06-17.md docs/audit/misconfig_detector_dedup_decision.md docs/audit/model_routing_coherence_check.md docs/design/connector_join_key_normalization.md docs/integration/BATCH3_APPLY_PLAN.md docs/integration/BATCH4_DEFERRED.md docs/integration/CASCADE_STATE_2026-06-18.md docs/integration/CYCLE_APPLY_PLAN_2026-06-17.md docs/integration/META_COMMIT_MANIFEST_2026-06-18.md docs/integration/NEXT_CYCLES_READY.md docs/integration/POSTSHIP_MERGE_CASCADE_2026-06-18.md docs/integration/POSTSHIP_RUNBOOK.md docs/integration/RELEASE_ALPHA2_MECHANICS_2026-06-18.md docs/integration/REVIEW_FINDINGS_2026-06-17.md docs/integration/SHIP_EXECUTION_CHECKLIST_2026-06-18.md docs/research/MODEL_ROUTING_RECOMMENDATION.md .opencode/plugin/enforce-floor.ts .gitignore'
```

---

### STEP F — Commit message

```text
make git-commit MSG='chore(release): v0.1.0-alpha.2 — version bump + session meta-work (README status table, CHANGELOG cut, AGENTS.md policy, Makefile targets, guardrail hooks, planning docs, audit docs, new tests)'
```

`make git-commit` is gate-guarded: it checks `.gate-status` is < 1800 s old and all phases PASS.
**If the gate is stale:** run `make gate` first (16–20 min), then re-run the commit.

Expected output: `Gate fresh and green. Committing...` followed by the new commit SHA.

---

### STEP G — Post-commit: run `make release-cut`

```text
make release-cut
```

This target requires `README.md` to contain `Status as of v0.1.0-alpha.2` (line 70, verified).
Expected: exits 0. If it fails on the README gate, re-verify line 70 was not clobbered.

---

### Full apply sequence (copy-paste order for post-gate execution)

```markdown
# 0. Gate freshness check
make gate                        # skip if .gate-status < 30 min old and all PASS

# A. Version bump (Edit tool — NOT shell)
#    pyproject.toml line 3:                version = "0.1.0-alpha.2"
#    src/general_ludd/__init__.py line 3:  __version__ = "0.1.0-alpha.2"

# B. CHANGELOG rename (Edit tool)
#    CHANGELOG.md line 5: ## [Unreleased] — next alpha — 2026-06-17
#                      -> ## [0.1.0-alpha.2] - 2026-06-18

# C. README — no edit needed (Status as of v0.1.0-alpha.2 already at line 70)

# D. Resolve .claude/ tracking (default: include; see FLAG above)

# E. Repoint stale mt-6/mt-7 SHAs in scripts/multitasking_backlog.json if needed

# F. Stage (Option A — track .claude/):
make git-add FILES='pyproject.toml src/general_ludd/__init__.py CHANGELOG.md README.md AGENTS.md BUGS.md Makefile SESSION.md scripts/multitasking_backlog.json scripts/check_readme_status_current.py scripts/gen_gate_safe_hook.py tests/unit/test_readme_status_gate.py docs/audit/BACKLOG_RECONCILED_2026-06-17.md docs/audit/BATCH2_SECURITY_PLAN_2026-06-18.md docs/audit/MEMORY_TO_HOOK_AUDIT_2026-06-18.md docs/audit/NEW_FINDINGS_2026-06-16.md docs/audit/NEW_FINDINGS_TRIAGE_2026-06-18.md docs/audit/SECURITY_AUDIT_BACKLOG_2026-06-17.md docs/audit/WAVE3_FIXPASS_PLAN_2026-06-18.md docs/audit/backlog_completeness_2026-06-16.md docs/audit/batch3_dedup_coherence.md docs/audit/feature_package_wiring_status.md docs/audit/floor_breach_rootcause_2026-06-17.md docs/audit/misconfig_detector_dedup_decision.md docs/audit/model_routing_coherence_check.md docs/design/connector_join_key_normalization.md docs/integration/BATCH3_APPLY_PLAN.md docs/integration/BATCH4_DEFERRED.md docs/integration/CASCADE_STATE_2026-06-18.md docs/integration/CYCLE_APPLY_PLAN_2026-06-17.md docs/integration/META_COMMIT_MANIFEST_2026-06-18.md docs/integration/NEXT_CYCLES_READY.md docs/integration/POSTSHIP_MERGE_CASCADE_2026-06-18.md docs/integration/POSTSHIP_RUNBOOK.md docs/integration/RELEASE_ALPHA2_MECHANICS_2026-06-18.md docs/integration/REVIEW_FINDINGS_2026-06-17.md docs/integration/SHIP_EXECUTION_CHECKLIST_2026-06-18.md docs/research/MODEL_ROUTING_RECOMMENDATION.md .opencode/plugin/enforce-floor.ts .claude/'

# G. Commit (gate-guarded):
make git-commit MSG='chore(release): v0.1.0-alpha.2 — version bump + session meta-work (README status table, CHANGELOG cut, AGENTS.md policy, Makefile targets, guardrail hooks, planning docs, audit docs, new tests)'

# H. Release cut gate:
make release-cut

# I. Final gate before push:
make gate

# J. Push master:
make git-push-sandboxcom

# K. Tag + publish:
make git-tag-push TAG=v0.1.0-alpha.2 MSG='Release v0.1.0-alpha.2: integration batch 3, ship pipeline, security hardening, meta-work'

# L. Confirm:
make release-view TAG=v0.1.0-alpha.2
```

---

### Ambiguities and flags (resolve before execution)

| # | Flag | Default action | Override |
|---|------|---------------|---------|
| 1 | `.claude/` untracked — not gitignored | Include in commit (Option A) | Add `.claude/` to `.gitignore` for Option B |
| 2 | `scripts/wave3_consolidate.sh` is a raw shell script — not make-compliant | Exclude from commit | If reviewed and approved, add to FILES |
| 3 | `scripts/multitasking_backlog.json` has stale mt-6/mt-7 SHAs | Repoint before staging | If branches are not yet created, use placeholder SHA `0000000` and note it |
| 4 | `scripts/agent_liveness.py` not in `??` list (possibly already tracked or absent) | Skip — not in FILES | `make git-status` to confirm; add if it appears as `??` or `M` |
| 5 | `nested/` and `proj-ok/` directories | Exclude | Never include — they are local scaffolding |
