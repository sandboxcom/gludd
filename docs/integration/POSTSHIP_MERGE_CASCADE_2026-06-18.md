# Post-Ship Merge Cascade Runbook — 2026-06-18

Executable runbook for merging pending feature branches onto master after master
fast-forwards to ship commit 6063e51. All commands are `make <target>` only.

---

## 1. Prerequisite: Verify Ship Landed

Before running any merge step, confirm 6063e51 is reachable from master.

```text
make git-is-ancestor A="6063e51" B="master"
```

Expected output: `exit=0`

If `exit=1`, the ship has not happened yet. Do not proceed. Trigger the ship
pipeline first:

```text
make ship-async REF=6063e51 TARGET=master
```

Then re-verify ancestry before continuing.

Additional sanity check — confirm master tip matches 6063e51 exactly (a fast-forward
ship produces a clean match; a non-FF merge would make 6063e51 an ancestor but not
the tip):

```text
make git-show MSG="master"
```

Expected: the first line shows `commit 6063e5176401e95d1c311013913c23d655571b40`.

---

## 2. Branch Inventory

Current state as of 2026-06-18 (verified via `make git-where` and `make git-is-ancestor`).

| Branch | Tip SHA | Exists? | Ancestor-clean off 6063e51? | Notes |
|--------|---------|---------|----------------------------|-------|
| `feature/batch3-security` | 85158c2 | YES | YES (exit=0, 0 commits unique to 6063e51) | 3 commits ahead of ship; in worktree agent-a76c60dd697746078 |
| `feature/floor-gate-safe` | ef1649d | YES | NO (exit=1, 48 commits diverged) | Built off an older base; needs rebase or integration merge before FF |
| `floor_controller-consolidated` | N/A | NO | N/A | Consolidation branch (mt-7 + floor-gate-safe) not yet created |
| `feature/security-batch4` | N/A | NO | N/A | Being built by another agent; tip SHA TBD |
| `mt-6-watchdog` | N/A | NO | N/A | Watchdog branch not yet created; files TBD |
| Orchestrator meta-work | N/A | Unstaged only | N/A | AGENTS.md, Makefile, scripts/multitasking_backlog.json in working tree; not yet committed |

### Confirmed file changes per branch

**`feature/batch3-security` (85158c2):**
- `src/general_ludd/db/repository.py` — add `limit`/`offset` params to `TodoRepository.list_all`
- `src/general_ludd/routers/todos.py` — add `limit`/`offset` query params + clamping [1,500]
- `tests/test_todos_pagination.py` — new file (4 pagination regression tests)

**`feature/floor-gate-safe` (ef1649d):**
- `.claude/hooks/agent_floor_stop.sh` — new file (gate-safe BLOCKING stop hook)
- `Makefile` — adds `write-gate-safe-hook` to `.PHONY` and target body
- `scripts/floor_controller.py` — adds `gate_running` + `writer_cap_during_gate` params to `decide()`; new `read_only_only` + `gate_running` keys in return dict
- `scripts/gen_gate_safe_hook.py` — new file (generates gate-safe hook content)
- `tests/unit/test_floor_controller.py` — adds `TestGateSafeFloor` class (8 tests)

**Ship commit 6063e51 (already on target):**
- `Makefile` — `VIRTUAL_ENV`/`UV_PROJECT_ENVIRONMENT` exports + `venv-check` + `git-hard-reset` targets
- `src/general_ludd/budget_guard_check.py` — `cast(Any, guard).remaining()`
- `src/general_ludd/controllers/spend_limiter.py` — non-finite/non-number guard in `record()` and `_purge_old()`
- `src/general_ludd/models/gateway.py` — type annotation tightening (`dict` -> `dict[str, Any]`)
- `src/general_ludd/routers/models.py` — type annotation tightening

---

## 3. File Conflict Matrix

Files touched by two or more branches (or by a branch and the ship commit).

| File | Branches that touch it | Risk |
|------|------------------------|------|
| `Makefile` | 6063e51 (ship) + `feature/floor-gate-safe` + orchestrator meta-work | HIGH: three-way. Ship already on target. floor-gate-safe adds a `.PHONY` line and `write-gate-safe-hook` target; meta-work adds further targets. Requires manual 3-way review. |
| `scripts/floor_controller.py` | `feature/floor-gate-safe` + mt-7 (not yet created) | HIGH: mt-7 and floor-gate-safe both extend `decide()`; the `floor_controller-consolidated` branch must resolve this before merging. |
| `tests/unit/test_floor_controller.py` | `feature/floor-gate-safe` + mt-7 | HIGH: same consolidation dependency as floor_controller.py above. |
| `src/general_ludd/models/gateway.py` | 6063e51 (ship) + `feature/security-batch4` (expected) | MEDIUM: ship already applied type-annotation tightening to `_notify_profile_change`. batch-4 may add additional security changes; check for overlap on merge. |
| `src/general_ludd/db/repository.py` | `feature/batch3-security` only | LOW: no other branch touches this file in this wave. |
| `src/general_ludd/routers/todos.py` | `feature/batch3-security` only | LOW: isolated. |
| `scripts/multitasking_backlog.json` | orchestrator meta-work only (unstaged) | LOW: no branch conflict; commit after other merges to avoid stale state. |
| `AGENTS.md` | orchestrator meta-work only (unstaged) | LOW: no branch conflict; commit after other merges. |
| `.claude/hooks/agent_floor_stop.sh` | `feature/floor-gate-safe` (new file) | LOW if floor-gate-safe is the only author; but the file may already exist in working tree or via `write-gate-safe-hook`. Verify before merge. |

### Notes on models/gateway.py

The ship commit (6063e51) tightened the type annotations on `_notify_profile_change` (changed two `dict` params to `dict[str, Any]`). Any batch-4 security changes to the same function or adjacent code in gateway.py will conflict if they operate on the same lines. Review the batch-4 diff against 6063e51's gateway.py hunk before merging.

---

## 4. Recommended Merge Order

Run `make git-is-ancestor A="6063e51" B="master"` (expect `exit=0`) before step 1.

### Step 1 — Commit orchestrator meta-work directly to master

These files are unstaged in the working tree (not on any branch) and should land
on master first so subsequent merges have a stable base.

**Files:** `AGENTS.md`, `Makefile`, `scripts/multitasking_backlog.json`

**Conflict risk:** LOW for AGENTS.md and multitasking_backlog.json. MEDIUM for
Makefile — the ship commit (now on master) already changed Makefile; the working-tree
changes must be reviewed against that diff before staging.

**Pre-check:**

```text
make git-diff
make git-staged
```

Review the Makefile working-tree changes to confirm they do not conflict with the
venv-check/git-hard-reset additions from 6063e51.

**Commit command:**

```text
make git-add FILES='AGENTS.md Makefile scripts/multitasking_backlog.json'
make git-commit MSG='chore(orchestration): meta-work — AGENTS.md policy + Makefile targets + backlog'
```

Gate before proceeding:

```text
make gate
```

---

### Step 2 — Merge `feature/batch3-security` (85158c2)

This branch is ancestor-clean off 6063e51 (confirmed `exit=0`, 0 commits unique to
6063e51, 3 commits ahead). No files conflict with the ship commit or meta-work step.

**Branch:** `feature/batch3-security`
**Tip SHA:** 85158c2
**Files:** `src/general_ludd/db/repository.py`, `src/general_ludd/routers/todos.py`, `tests/test_todos_pagination.py`

**Conflict risk:** NONE with ship commit (ship does not touch db/repository.py or routers/todos.py) and NONE with step 1 meta-work files.

**FF pre-check:**

```text
make git-is-ancestor A="master" B="feature/batch3-security"
```

Expected: `exit=0` (master is an ancestor of feature/batch3-security, so FF is possible).

**Merge command (single branch via gated-merge):**

```text
make gated-merge BASE=master BRANCHES='feature/batch3-security' MANIFEST='/tmp/gludd-cascade-batch3.txt'
```

After merge, verify:

```text
make gate
```

On gate PASS, proceed. If FAIL, check manifest at `/tmp/gludd-cascade-batch3.txt`.

---

### Step 3 — Merge `floor_controller-consolidated` (when branch exists)

mt-7 and `feature/floor-gate-safe` both touch `scripts/floor_controller.py` and
`tests/unit/test_floor_controller.py`. These MUST be consolidated into a single branch
before merging into master. See Section 7 for the pending-branch protocol.

**When ready:**
- Confirm `floor_controller-consolidated` exists: `make git-show MSG="floor_controller-consolidated"`
- Verify ancestor-clean: `make git-is-ancestor A="master" B="floor_controller-consolidated"` (expect `exit=0`)
- Verify Makefile changes in the consolidated branch do not re-introduce the `.PHONY` line that meta-work (step 1) may have already added

**Conflict risk — Makefile:** HIGH. floor-gate-safe adds `write-gate-safe-hook` to
`.PHONY` and a target body. Step 1 (meta-work) may add different Makefile lines. After
both are on master, the consolidated branch may conflict on `.PHONY`. Resolution: accept
the master version's `.PHONY` line (it includes all additions) and apply only the
`write-gate-safe-hook` target body if it is not already present.

**Merge command:**

```text
make gated-merge BASE=master BRANCHES='floor_controller-consolidated' MANIFEST='/tmp/gludd-cascade-floor.txt'
```

After merge:

```text
make gate
```

---

### Step 4 — Merge `feature/security-batch4` (when branch exists)

Being built off 6063e51 by another agent. Files expected: `src/general_ludd/models/gateway.py`,
`src/general_ludd/connectors/registry.py`, `src/general_ludd/connectors/normalize.py`,
`src/general_ludd/connectors/base.py` plus tests.

**When ready:**
- Confirm branch: `make git-show MSG="feature/security-batch4"`
- Ancestor check: `make git-is-ancestor A="master" B="feature/security-batch4"` (expect `exit=0`)
- Check gateway.py overlap: if batch-4 changes lines adjacent to the 6063e51 type-annotation hunk in `_notify_profile_change`, do a manual diff review before merging

**Conflict risk — gateway.py:** MEDIUM. Review the batch-4 diff on gateway.py against master's current state before merging.

**Merge command:**

```text
make gated-merge BASE=master BRANCHES='feature/security-batch4' MANIFEST='/tmp/gludd-cascade-batch4.txt'
```

After merge:

```text
make gate
```

---

### Step 5 — Merge `mt-6-watchdog` (when branch exists)

Being built off 6063e51 (or master post-step-1). Expected files: `scripts/agent_watchdog.py` plus a test file (or under `src/general_ludd/orchestration/`).

**When ready:**
- Confirm branch name (may differ from `feature/mt-6-watchdog`): `make git-show MSG="feature/mt-6-watchdog"`
- Ancestor check: `make git-is-ancestor A="master" B="feature/mt-6-watchdog"` (expect `exit=0`)

**Conflict risk:** LOW unless the watchdog file overlaps with orchestration files already on master.

**Merge command:**

```text
make gated-merge BASE=master BRANCHES='feature/mt-6-watchdog' MANIFEST='/tmp/gludd-cascade-mt6.txt'
```

After merge:

```text
make gate
```

---

### Optional: Multi-branch combined merge (steps 2–5 at once, when all branches ready)

If all four pending branches exist, are ancestor-clean, and have been individually
verified conflict-free, they can be combined in one gated-merge call. Order matters:
independent branches first, overlapping ones adjacent (see ordering above).

```text
make gated-merge \
  BASE=master \
  BRANCHES='feature/batch3-security floor_controller-consolidated feature/security-batch4 feature/mt-6-watchdog' \
  MERGE_STRATEGY=stop-on-conflict \
  MANIFEST='/tmp/gludd-cascade-all.txt'
```

On any CONFLICT the script aborts and resets to BASE automatically. Check
`/tmp/gludd-cascade-all.txt` to see which branch conflicted.

---

## 5. Conflict Resolution Notes

### Makefile

Three sets of changes converge on Makefile: ship commit (6063e51), floor-gate-safe,
and orchestrator meta-work.

Ship commit is already on master (assumed). Remaining conflicts:

- **`.PHONY` line additions:** floor-gate-safe adds `write-gate-safe-hook`; meta-work may
  add other targets. Resolution: accept a union of all additions. The `.PHONY` block is
  a space-separated list; duplicates are harmless but should be cleaned.
- **`write-gate-safe-hook` target body:** lives at the bottom of the file. If meta-work
  also modifies the bottom section, apply both additions sequentially, keeping the
  gate-async/gate-status/gated-merge/ship-async/write-gate-safe-hook targets in
  alphabetical order by target name.
- **Winner:** Neither branch "wins" exclusively. Both additions are additive (new targets,
  no overwrites of existing targets). Manual merge required if the same line is touched.

### scripts/floor_controller.py

floor-gate-safe and mt-7 both extend the `decide()` function signature and return dict.

- floor-gate-safe adds `gate_running`/`writer_cap_during_gate` params and `read_only_only`/`gate_running` return keys.
- mt-7 adds watchdog-related logic (expected: `watchdog_window` param already exists; mt-7 may add stall-detection results to the return dict).

**Resolution:** The consolidated branch (`floor_controller-consolidated`) is the single
merge unit. The consolidation agent is responsible for producing a coherent signature. Do
not merge floor-gate-safe or mt-7 individually — always use the consolidated branch.

### tests/unit/test_floor_controller.py

Same consolidation dependency as floor_controller.py. The `TestGateSafeFloor` class
(8 tests) added by floor-gate-safe must coexist with mt-7 test additions. Resolution is
the consolidated branch's responsibility.

### src/general_ludd/models/gateway.py

Ship commit (6063e51) already tightened `hook_payload: dict` to `hook_payload: dict[str, Any]`
on the `_notify_profile_change` signature. If batch-4 modifies the same function, do a
line-level diff. If batch-4's change is to a different line, no conflict. If both touch
the same argument line, accept whichever provides the more specific type and a guard.

### .claude/hooks/agent_floor_stop.sh

This is a new file from floor-gate-safe. The file may already exist in the working tree
(it was created by the agent in the worktree). On merge, git will detect a new file; there
is no base-version conflict. If a different version already exists on master (e.g. a prior
commit placed a stub there), the floor-gate-safe version wins — it is the gate-safe
BLOCKING implementation.

---

## 6. Post-merge Checklist

After all merges in the cascade are complete:

1. Run the full gate:
   ```text
   make gate
   ```
   Gate must report all phases PASS (lint, typecheck, collect, test, smoke).

2. Verify test count did not drop (collection errors can silently hide tests):
   ```text
   make test-count
   ```
   Compare against the last known good count. Any drop > 10 tests warrants investigation.

3. Verify mypy is clean (MYPY_MAX=0 is enforced):
   ```text
   make typecheck
   ```
   Expected: 0 errors.

4. Run security scan:
   ```text
   make scan-secrets
   ```
   Confirm no new tracked secrets.

5. Create a release tag:
   ```text
   make git-commit MSG='chore: post-ship cascade — batch3-security + floor-gate-safe + batch4 + mt-6'
   ```
   (Only if there were any post-merge fixups; if all merges were clean no additional commit is needed.)

6. Push to remote:
   ```text
   make git-push-sandboxcom
   ```

---

## 7. Branches Not Yet Built

The following branches do not exist as of 2026-06-18 and must be handled when they land:

| Branch | Expected tip | Why it is missing | What to do when it lands |
|--------|-------------|-------------------|--------------------------|
| `floor_controller-consolidated` | TBD | Consolidation agent is merging mt-7 + feature/floor-gate-safe into this branch | Confirm branch exists, run ancestor check, follow Step 3 above |
| `feature/security-batch4` | TBD | Build agent is working off 6063e51 | Confirm branch exists, verify gateway.py overlap, follow Step 4 above |
| `mt-6-watchdog` (exact name TBD) | TBD | Build agent is working off 6063e51 | Confirm branch exists (try `feature/mt-6-watchdog` and `mt-6-watchdog`), follow Step 5 above |

### When a branch lands mid-cascade

If a branch lands after some steps are already complete, perform an ancestor check
against the current master tip (not 6063e51):

```text
make git-is-ancestor A="master" B="<new-branch>"
```

If `exit=1` (master has diverged ahead of the new branch's base), the branch needs to be
rebased or merged with master before the FF-only gated-merge will work. Use:

```text
make git-merge MSG="<new-branch>"
```

to perform a standard no-FF merge, then gate.

### Detecting the batch-4 branch name

The task description says the batch-4 branch may be named `feature/security-batch4`.
If that name does not resolve, scan the branch list for recent security-related names:

```text
make git-ls-tracked Q="batch4\|batch-4\|security-b4"
```

Or check recent worktree agent branches by reviewing `make git-where` output for
branches whose tip commits reference connectors/registry, connectors/normalize,
or models/gateway.

---

## Reference: Key Make Targets Used Here

| Target | Signature | Purpose |
|--------|-----------|---------|
| `git-is-ancestor` | `A=<commit> B=<ref>` | FF-safety check; exit=0 means A is ancestor of B |
| `git-revlist-count` | `A=<old> B=<new>` | Count commits unique to A (must be 0 for FF) and B ahead of A |
| `gated-merge` | `BASE=<ref> BRANCHES='<space-list>' [MERGE_STRATEGY=stop-on-conflict] [MANIFEST=<path>]` | flock-guarded multi-branch merge + collect-check; aborts on conflict |
| `ship-async` | `REF=<hash> [TARGET=master]` | Background gate + ff-only master merge on green |
| `gate` | (none) | Full gate: lint + typecheck + collect + test + smoke |
| `test-count` | (none) | Collection sanity check (use before any commit) |
| `git-where` | (none) | Show HEAD, master, all branches, worktrees |
| `git-show` | `MSG='<commit>[:path]'` | Show commit diff or file at a commit |

### gated-merge invocation notes

`gated_merge.sh` uses `BASE` as the starting point for the `reset --hard` fallback
on conflict. Specify `BASE=master` (not the ship SHA) once master has fast-forwarded
to the ship commit. The script acquires an exclusive flock on `/tmp/gludd-gated-merge.lock`.
Do not run two gated-merge calls concurrently. Check for a stale lock:

```text
make ps-gludd
```

If a lock is orphaned, clear it with:

```text
make kill-gate-force
```

(This removes `/tmp/gludd-gate.lock`; the merge lock at `/tmp/gludd-gated-merge.lock`
is separate — remove manually if needed after confirming no merge is in flight.)
