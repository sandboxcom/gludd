---
name: enforce-bootstrap
description: Mechanical escape-hatch when ALL enforcement plugins are blocking legitimate work. Use only after exhausting normal paths.
---

# Enforce Bootstrap — Enforcement Escape Hatch

When every enforcement plugin (enforce-stop, enforce-floor, enforce-delegate,
enforce-make) is blocking legitimate work, use this mechanical escape hatch.
This is the **last resort** — use only after normal `make` targets have been
tried and blocked, and only when the work is genuinely blocked (not when a
guardrail is correctly blocking a policy violation).

---

## Diagnosis Flowchart

```
Enforcement blocking your tool call?
│
├─ Is the block correct? (You ARE violating policy)
│  └─ YES → FIX the violation. Do NOT use the escape hatch.
│     Example: committing with red gate → fix the gate first.
│     Example: non-make bash command → add a make target.
│
├─ Is the block a false positive? (You are NOT violating policy)
│  └─ Continue below...
│
├─ Have you tried the normal target?
│  ├─ NO → Try it. Most blocks are from using the wrong target.
│  │  Example: `make git-commit MSG=...` blocked → try `make git-commit-file FILE=/tmp/msg.txt`
│  │  Example: `make git-push-sandboxcom` blocked → try `make batch-push`
│  │  Example: `make ci-verdict` blocked → try `make ci-verdict-safe`
│  │
│  └─ YES, normal targets also blocked → Continue...
│
├─ Is this a state-file problem? (Stale /tmp/gludd-* files)
│  ├─ Run `make crash-recovery` → resets enforcement state
│  ├─ Run `make clean-tmp` → removes stale temp files
│  └─ Retry the normal target.
│
├─ Is this a CI cooldown block?
│  └─ Use `make ci-verdict-safe FORCE=1` (release-cut ONLY)
│     For non-release work: the cooldown is correct — check later.
│
├─ ALL plugins blocking with false positives?
│  └─ → Escalate to full emergency disengage (Steps 1-6 below).
│
└─ Still blocked after all of the above?
   └─ → The guardrail may need a code fix. See "Plugin-by-Plugin Guide" below.
```

---

## Scenario Playbooks

### Scenario A: "I need to commit but enforce-todo blocks me"

**Symptom:**
```
make git-commit MSG='fix: update worktree health check'
→ BLOCKED: Uncompleted todowrite items exist
```

**Root cause:** The todowrite list has `pending` or `in_progress` items that
the commit message doesn't reference. The guardrail correctly prevents
committing when work items would be forgotten.

**Legitimate fix (do this first):**
1. Run `make git-status` to see what's changed.
2. Review todowrite items. For each one:
   - If completed: mark it done in TASKS.md
   - If not relevant to this commit: add a TASKS.md note explaining why
   - If partially done: update TASKS.md with current status
3. Stage the TASKS.md update: `make git-add FILES='TASKS.md'`
4. Commit with a message that references the TASKS.md update:
   ```
   make git-commit MSG='fix: worktree health check (TASKS.md updated)'
   ```

**Escape hatch (only if the above fails):**
```bash
make disengage-enforcement
make git-commit-file FILE=/tmp/msg.txt
```

**Commit message file (`/tmp/msg.txt`):**
```
fix: worktree health check

Enforcement plugins producing false-positive blocks on commit.
Todowrite items were stale from a prior crashed session.
State reset via make crash-recovery confirmed.
```

---

### Scenario B: "I need to push but an explicit floor blocks me"

**Symptom:**
```
make git-push-sandboxcom
→ BLOCKED: Configured floor breach.
```

**Root cause:** A positive operator override or stale state enabled a mandatory
floor. The project default is zero; the hard ceiling is three.

**Legitimate fix (do this first):**
1. Inspect state with `make enforcement-status`.
2. Reset stale state to the canonical default with
   `make reload-enforcement CLAUDE_AGENT_FLOOR=0 CLAUDE_AGENT_CEILING=3`.
3. If an operator intentionally configured a positive floor, satisfy it only
   with concrete independent deliverables; never add filler.
4. Use the normal guarded push target for the current branch.

**Guarded push:**
```bash
make push-guarded BRANCH=development
make verify-remote BRANCH=development
```

---

### Scenario C: "I need to edit a file but enforce-tdd blocks me"

**Symptom:**
```
Edit to src/general_ludd/foo.py
→ BLOCKED: No test file exists at tests/unit/test_foo.py
```

**Root cause:** The TDD plugin requires a test file before any `src/` edit.
This is almost always correct — you should write the test first.

**Legitimate fix (do this first):**
1. Write the test file first: `tests/unit/test_foo.py`
2. Run it: `make test-specific TESTFILE='tests/unit/test_foo.py'` (should FAIL — red)
3. Now edit `src/general_ludd/foo.py` (allowed — test file exists)
4. Run test again: should PASS (green)

There is no empty-test or disable-the-guard shortcut. For legacy code, add a
real failing characterization test, observe the failure, then change the
implementation.

---

### Scenario D: "All plugins blocking with false positives — full emergency"

**Symptom:** Every tool call is denied by a different plugin. Normal targets
also denied. Crash recovery didn't help. This is a full enforcement deadlock.

**Step-by-step recovery procedure:**

**Step 1: Restore canonical state**
```bash
make crash-recovery
make reload-enforcement CLAUDE_AGENT_FLOOR=0 CLAUDE_AGENT_CEILING=3
make enforcement-status
```

Use `make disengage-enforcement` only if canonical state recovery itself cannot
run. Disengage skips heuristic checks temporarily; it does not waive pending
work, test, exact-head gate, branch, or release requirements.

**Step 2: Reproduce and fix the false positive**
```bash
make test-hook-runtime
make verify-enforcement
```

Add a failing regression for the specific deadlock before changing plugin code.

**Step 3: Run the exact candidate gate and commit normally**
```bash
make gate
make git-commit MSG='fix: resolve enforcement deadlock'
```

**Step 4: Push through the guarded branch workflow**
```bash
make verify-state
make push-guarded BRANCH=development
```

**Step 5: Verify the remote**
```bash
make verify-remote BRANCH=development
```

NEVER claim a push succeeded until the matching `VERIFIED` evidence is printed.

**Step 6: Re-arm and verify**
```bash
make rearm-enforcement
make enforcement-status
```

---

### Scenario E: "CI verdict check blocked by cooldown"

**Symptom:**
```
make ci-verdict
→ CI-COOLDOWN: 7m23s remaining
```

**Root cause:** The CI check cooldown is active. This is correct behavior —
polling CI more than once every 10 minutes is wasteful.

**Legitimate fix (do this first):**
- If you need the CI status for non-release work: wait for the cooldown to
  expire, or check at the next natural break (15+ minutes).
- If you need it for release-cut: use `FORCE=1`.

**Escape hatch:**
```bash
make ci-verdict-safe FORCE=1
```

**Restriction:** `FORCE=1` is for **release-cut only**. Using it to bypass
the cooldown for routine CI checks is a policy violation.

---

## Plugin-by-Plugin Blocking Guide

For each enforcement plugin: what it blocks, the legitimate fix, and the escape
hatch when the fix is impossible.

### enforce-make.ts

| | |
|---|---|
| **What it blocks** | Non-`make` bash commands, shell metacharacters in `make` commands |
| **Legitimate fix** | Add a `make` target for what you need, then `make <target>` |
| **Escape hatch** | None. Add a narrow, tested, contract-registered Make target when a capability is genuinely missing. |
| **Disable env var** | (none — hard-coded) |

### enforce-stop.ts

| | |
|---|---|
| **What it blocks** | Text-only responses when pending work exists; false-done claims; QA summaries at session start; commits with stale/red gate |
| **Legitimate fix** | Include a tool call in every response while work exists. Cite evidence for any completion claim. |
| **Escape hatch** | `make disengage-enforcement` — but note: as of 2026-07-15, disengage only skips heuristic checks (COMPLETION_SMELL, COMPLETION_WORDS, QA patterns). The fundamental `hasRealPendingWork()` text-only block is NEVER bypassed by disengage. You must include a tool call regardless. |
| **Disable env var** | (hard-coded ON for text-only block) |

### enforce-floor.ts

| | |
|---|---|
| **What it blocks** | Non-dispatch tool calls only when an explicitly configured one-to-three floor is unmet; the default floor is zero |
| **Legitimate fix** | Reset stale state or assign concrete independent work up to the configured bounded floor. |
| **Escape hatch** | `GLUDD_FLOOR_ENFORCE=0 make <target>` |
| **Disable env var** | `GLUDD_FLOOR_ENFORCE=0` |

### enforce-delegate.ts

| | |
|---|---|
| **What it blocks** | Inline mutation/read streaks only when an explicit positive floor is active; the zero-floor default permits inline progress |
| **Legitimate fix** | Reset stale state or satisfy the configured floor with a real independent deliverable; never dispatch filler. |
| **Escape hatch** | `GLUDD_MAINTHREAD_STREAK_ENFORCE=0 make <target>` |
| **Disable env var** | `GLUDD_MAINTHREAD_STREAK_ENFORCE=0` |

### enforce-clean-tree.ts

| | |
|---|---|
| **What it blocks** | `task`/`agent`/`workflow` dispatch when `git status --porcelain` is non-empty |
| **Legitimate fix** | Commit or stash changes first: `make ship-commit MSG='...'` or `make git-stash` |
| **Escape hatch** | `GLUDD_CLEAN_TREE_ENFORCE=0 make <target>` |
| **Disable env var** | `GLUDD_CLEAN_TREE_ENFORCE=0` |

### enforce-tdd.ts

| | |
|---|---|
| **What it blocks** | `edit`/`write` to `src/general_ludd/**/*.py` when no corresponding test file exists |
| **Legitimate fix** | Write the test file first. |
| **Escape hatch** | `GLUDD_TDD_ENFORCE=0 make <target>` |
| **Disable env var** | `GLUDD_TDD_ENFORCE=0` |

### enforce-no-suppressions.ts

| | |
|---|---|
| **What it blocks** | Edits that introduce `# noqa`, `# type: ignore`, `# pylint: disable`, `# fmt: off/on`, `# isort:skip` |
| **Legitimate fix** | Fix the underlying issue (reflow the line, add the type, delete the unused import). |
| **Escape hatch** | `GLUDD_NO_SUPPRESSIONS_ENFORCE=0 make <target>` |
| **Disable env var** | `GLUDD_NO_SUPPRESSIONS_ENFORCE=0` |

### enforce-no-wait.ts

| | |
|---|---|
| **What it blocks** | `sleep` + `make` chains, `make gate-tail`, `make ci-wait` on main thread; CI-poll subagent dispatch |
| **Legitimate fix** | Use `make gate-background` + poll from subagent. For CI: check at natural breaks. |
| **Escape hatch** | `GLUDD_NO_WAIT_ENFORCE=0 make <target>` |
| **Disable env var** | `GLUDD_NO_WAIT_ENFORCE=0` |

### enforce-deadline.ts

| | |
|---|---|
| **What it blocks** | Task/agent dispatch when prior tasks exceed timeout (default 5 min) |
| **Legitimate fix** | Re-split the timed-out task into smaller units. Kill stalled child processes via `make task-watchdog-start`. |
| **Escape hatch** | `GLUDD_TASK_DEADLINE_ENFORCE=0` |
| **Disable env var** | `GLUDD_TASK_DEADLINE_ENFORCE=0` |

### enforce-session-start.ts

| | |
|---|---|
| **What it blocks** | Non-dispatch tool calls only when an operator configured a positive session-start floor and that bounded floor is unmet |
| **Legitimate fix** | Satisfy the configured one-to-three floor with concrete independent work, or keep the default zero floor for inline work. |
| **Escape hatch** | `GLUDD_SESSION_START_ENFORCE=0 make <target>` |
| **Disable env var** | `GLUDD_SESSION_START_ENFORCE=0` |

### enforce-multitask.ts

| | |
|---|---|
| **What it blocks** | Non-dispatch tools only when an explicit one-to-three minimum is unmet; the default zero floor never forces delegation |
| **Legitimate fix** | Dispatch only concrete independent work up to the three-agent ceiling; never add filler. |
| **Escape hatch** | `GLUDD_MULTITASK_FLOOR_ENFORCE=0 make <target>` |
| **Disable env var** | `GLUDD_MULTITASK_FLOOR_ENFORCE=0` |

### enforce-enhancement-ratio.ts

| | |
|---|---|
| **What it blocks** | Task/agent dispatch when fix% > 50% in the current wave |
| **Legitimate fix** | Replace some fix dispatches with enhancement dispatches (new tests, features, docs, tooling). |
| **Escape hatch** | `GLUDD_ENHANCEMENT_RATIO_ENFORCE=0 make <target>` |
| **Disable env var** | `GLUDD_ENHANCEMENT_RATIO_ENFORCE=0` |

### enforce-verified-claims.ts

| | |
|---|---|
| **What it blocks** | Text output containing "done" words without machine-produced evidence |
| **Legitimate fix** | Include evidence tokens in the response (commit hash, test count, CI verdict). |
| **Escape hatch** | `GLUDD_VERIFIED_CLAIMS_ENFORCE=0` |
| **Disable env var** | `GLUDD_VERIFIED_CLAIMS_ENFORCE=0` |

---

## State File Reference

Every enforcement-related state file in `/tmp/gludd-*`, what it controls,
and how to inspect/reset it.

### `/tmp/gludd-floor-override`

```json
0
```

| | |
|---|---|
| **Controls** | Floor value for subagent count enforcement |
| **Inspect** | `make enforcement-status` |
| **Reset** | `make reload-enforcement CLAUDE_AGENT_FLOOR=0 CLAUDE_AGENT_CEILING=3` |
| **Default** | 0, with a hard ceiling of 3 |

### `/tmp/gludd-watchdog-disengage.json`

```json
{
  "disengage_until_epoch_ms": 1753462800000,
  "disengage_by": "make disengage-enforcement"
}
```

| | |
|---|---|
| **Controls** | Disengage signal — tells all enforcement plugins to skip heuristic checks |
| **Inspect** | `make enforcement-status` |
| **Reset** | `make rearm-enforcement` |
| **Expiry** | 1 hour after creation |

### `/tmp/gludd-session-start.json`

```json
{
  "pid": 12345,
  "dispatchCount": 0,
  "dispatchesRequired": 0,
  "sessionStartedAt": 1753459200000,
  "firstDispatchMade": true,
  "lastDispatchAt": 1753459260000
}
```

| | |
|---|---|
| **Controls** | Session-start protocol — tracks dispatch count, enforces the minimum |
| **Inspect** | Read the file |
| **Reset** | `make crash-recovery` (resets all enforcement state files including this one) |
| **Stale detection** | PID mismatch (stored PID != current process PID) | Age > 300s (5 min) |

### `/tmp/gludd-ci-check-state.json`

```json
{
  "last_check_epoch": 1753459300,
  "last_push_epoch": 1753459200,
  "last_head_sha": "abc123def456",  <!-- pragma: allowlist secret -->
  "check_count": 3
}
```

| | |
|---|---|
| **Controls** | CI check cooldown — enforces minimum interval between `ci-verdict` calls |
| **Inspect** | Read the file |
| **Reset** | Delete the file |
| **Cooldown** | 10 minutes (600s) default. Override: `CI_CHECK_COOLDOWN_SEC=<N>` |

### `/tmp/gludd-enhancement-ratio.json`

```json
{
  "currentWave": [
    {"prompt": "fix: ...", "classification": "fix"},
    {"prompt": "enhancement: ...", "classification": "enhancement"}
  ],
  "sessionTotal": {"fix": 15, "enhancement": 10}
}
```

| | |
|---|---|
| **Controls** | Enhancement ratio enforcement — tracks fix vs enhancement dispatches per wave |
| **Inspect** | `make check-enhancement-ratio` (read-only diagnostic) |
| **Reset** | Delete the file |

### `/tmp/gludd-tool-streak.json`

```json
{
  "consecutiveNonDispatchCalls": 3,
  "lastDispatchTimestamp": 1753459200000,
  "totalNonDispatchCalls": 12
}
```

| | |
|---|---|
| **Controls** | Anti-grinding — blocks non-dispatch calls after a streak threshold |
| **Inspect** | Read the file |
| **Reset** | Delete the file (or dispatch a subagent — resets counter to 0) |

### `/tmp/gludd-task-deadlines.json`

```json
{
  "tasks": {
    "task-abc123": {
      "startedAt": 1753459200000,
      "timeoutMs": 300000,
      "label": "fix: worktree health check"
    }
  }
}
```

| | |
|---|---|
| **Controls** | Task deadline enforcement — tracks running tasks, flags those over timeout |
| **Inspect** | Read the file |
| **Reset** | Delete the file (or let tasks complete — completed tasks are removed) |

### `/tmp/gludd-block-counter.json`

```json
{
  "blockCount": 0,
  "lastBlockAt": 0
}
```

| | |
|---|---|
| **Controls** | Tracks consecutive block events for escalation |
| **Inspect** | Read the file |
| **Reset** | Delete the file (or `make disengage-enforcement` — sets counter to 0) |

---

## Disengage Deep-Dive

### What happens at the filesystem level

When you run `make disengage-enforcement`, the Makefile target:

1. Writes `/tmp/gludd-watchdog-disengage.json` with:
   ```json
   {
     "disengage_until_epoch_ms": <current_epoch_ms + 3600000>,
     "disengage_by": "make disengage-enforcement"
   }
   ```

2. Writes `/tmp/gludd-block-counter.json` with:
   ```json
   {
     "blockCount": 0,
     "lastBlockAt": 0
   }
   ```

3. Writes `/tmp/gludd-watchdog-ci.json` with:
   ```json
   {
     "cached_ci_status": "GREEN",
     "cached_at_epoch": <current_epoch_ms>,
     "cached_by": "make disengage-enforcement"
   }
   ```

### Which plugins respect it

| Plugin | Respects disengage? | What it skips |
|---|---|---|
| enforce-stop.ts | **Partial** | Skips heuristic checks (COMPLETION_SMELL, COMPLETION_WORDS, QA patterns). NEVER skips `hasRealPendingWork()` text-only block. |
| enforce-floor.ts | **Yes** | Skips all non-dispatch tool call blocks |
| enforce-delegate.ts | **Yes** | Skips main-thread streak enforcement |
| enforce-make.ts | **No** | Not affected (hard-coded) |
| enforce-clean-tree.ts | **Yes** | Skips dirty-tree dispatch block |
| enforce-tdd.ts | **Yes** | Skips test-first edit block |
| enforce-no-suppressions.ts | **Yes** | Skips suppression comment block |
| enforce-no-wait.ts | **Yes** | Skips sleep/ci-wait block |
| enforce-multitask.ts | **Yes** | Skips floor-count block |
| enforce-session-start.ts | **Yes** | Skips session-start gate |
| enforce-enhancement-ratio.ts | **Yes** | Skips ratio check |
| enforce-verified-claims.ts | **Yes** | Skips done-word evidence check |

### Expiry

The disengage signal expires after `MAX_DISENGAGE_MS` (typically 1 hour /
3,600,000 ms). After expiry, all plugins resume normal enforcement.

### Manual verification

```bash
make enforcement-status
```

---

## Make Target Reference for Recovery

| Blocked operation | Recovery path |
|---|---|
| Commit or ship | Fix the reported task/gate violation, run `make crash-recovery` only for stale state, then retry the ordinary guarded target |
| Push | Run `make verify-state`, preserve branch discipline, then use `make push-guarded BRANCH=development` |
| CI verdict cooldown | Check at a natural break; use `make ci-verdict-safe FORCE=1` only inside an authorized release cut |
| Batch threshold | Accumulate the required local commits; do not lower the threshold to manufacture a push |
| Suspected plugin deadlock | Add a failing hook regression, fix the plugin, run `make test-hook-runtime`, and restart OpenCode after committing |

Recovery targets do not waive a red gate, incomplete task evidence, branch
discipline, or release prerequisites.

---

## Recovery Procedures

### Recovery 1: Stale state files from a crashed session

**Symptoms:** Enforcement blocks every tool call at session start. `make crash-recovery`
reports PID mismatch or age-gated stale state.

**Procedure:**
```bash
make crash-recovery
make clean-tmp
# Restart opencode (enforcement plugins load at startup)
# Verify: dispatch a test subagent — should not be blocked
```

### Recovery 2: Watchdog disengage expired mid-commit

**Symptoms:** Started committing during a disengage window. The commit went
through, but the push is now blocked because the disengage expired.

**Procedure:**
```bash
# Run a fresh disengage
make disengage-enforcement

# Use the guarded push; disengage does not waive its preconditions
make push-guarded BRANCH=development

# Verify
make verify-remote BRANCH=development

# Re-arm immediately
make rearm-enforcement
```

### Recovery 3: Push that was blocked but actually succeeded

**Symptoms:** Push was denied by a plugin, but `git push` had already succeeded
before the plugin fired (race condition). The remote has the commit.

**Procedure:**
```bash
# Verify what's actually on the shared development branch
make verify-remote BRANCH=development

# If VERIFIED: the push DID land. The plugin was a false positive.
# Check CI status:
make ci-verdict-safe

# If NOT VERIFIED: the push did NOT land. Retry with escape hatch.
make disengage-enforcement
make push-guarded BRANCH=development
make verify-remote BRANCH=development
make rearm-enforcement
```

### Recovery 4: CI green cache is stale

**Symptoms:** `make disengage-enforcement` wrote a green CI cache, but you know
CI is actually red or running.

**Procedure:**
```bash
# Reset project-owned enforcement state through its bounded target
make crash-recovery

# Check real CI state (force past cooldown)
make ci-verdict-safe FORCE=1
```

---

## Anti-Patterns

### AP-1: Using the escape hatch for a correct guardrail block

```bash
# WRONG — guardrail is correctly blocking a policy violation
make git-commit MSG='quick fix'  # BLOCKED: gate is RED
# Agent: "Oh, I'll use the escape hatch"
make disengage-enforcement
make git-commit-file FILE=/tmp/msg.txt  # BUG: committed with red gate
```

```bash
# RIGHT — fix the violation, don't bypass the guardrail
make gate  # fix the red gate first
# ... fix failures ...
make gate  # confirm PASS
make git-commit MSG='quick fix'  # now allowed — gate is green
```

### AP-2: Creating a temporary push bypass target

```bash
# WRONG — adding a direct-push Make target bypasses branch and pre-push guards.
# Do not create or retain temporary push targets.
```

```bash
# RIGHT — use the existing guarded target and verify the same branch.
make push-guarded BRANCH=development
make verify-remote BRANCH=development
```

### AP-3: Using FORCE=1 for routine CI checks

```bash
# WRONG — bypassing the cooldown for a routine check
make ci-verdict-safe FORCE=1  # cooldown was 3m, agent forced through
# Result: CI polled too frequently, no code changes made between checks
```

```bash
# RIGHT — respect the cooldown for routine checks
# Cooldown says 3m left → dispatch real work for 3+ minutes, then check
make ci-verdict-safe  # returns CI-COOLDOWN: 3m remaining
# ... advance concrete work inline or with up to three bounded subagents ...
# ... 5 minutes later ...
make ci-verdict-safe  # cooldown expired → returns actual CI state
```

### AP-4: Disengaging for routine operations

```bash
# WRONG — disengaging to avoid a minor inconvenience
make disengage-enforcement
make git-commit MSG='fix: update TASKS.md'
# Result: heuristic checks were bypassed without fixing their false positive.
```

```bash
# RIGHT — try normal targets first. Only disengage when genuinely blocked.
make git-commit MSG='fix: update TASKS.md'  # try normal first
# If blocked: read the error message, fix the violation.
# If blocked AND violation is a false positive: THEN disengage.
make crash-recovery  # try state reset first
make git-commit MSG='fix: update TASKS.md'  # retry normal
# Still blocked? NOW disengage.
make disengage-enforcement
make git-commit MSG='fix: update TASKS.md'
make rearm-enforcement
```

### AP-5: Pushing without verifying the result

```bash
# WRONG — a push target returned but the agent doesn't verify
make push-guarded BRANCH=development
# Agent: "Pushed!" — but the push was a no-op (remote already had the commit)
# Agent starts next work assuming CI will pick up the push — CI sees nothing new
```

```bash
# RIGHT — always verify
make push-guarded BRANCH=development
make verify-remote BRANCH=development
# VERIFIED development@abc123def  ← confirmed the remote tip matches
# NOW you can claim "pushed"
```

---

## Checklist

- [ ] Tried ordinary `make git-commit` / `make push-guarded` flow — blocked
- [ ] Determined the block is a false positive (not a legitimate policy violation)
- [ ] Tried `make crash-recovery` (stale state file reset)
- [ ] Tried `make clean-tmp` (stale temp file cleanup)
- [ ] Ran `make disengage-enforcement`
- [ ] Verified state through `make enforcement-status`
- [ ] Added a failing regression for the false-positive block
- [ ] Ran the exact candidate gate
- [ ] Committed via the ordinary guarded Make target
- [ ] Pushed via `make push-guarded BRANCH=development`
- [ ] Verified remote via `make verify-remote BRANCH=development`
- [ ] Re-armed enforcement via `make rearm-enforcement`
