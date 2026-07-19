# Session 36 Handoff — 2026-07-15

> Generated: 2026-07-15. Authoritative state: `make git-log` + SESSION.md + CI verdict.

## Branch State

- **Branch:** `development`
- **HEAD:** `4081f38bdef73cb2d86aac4495439504cea70b2c`
- **Push status:** **NOT PUSHED** — 10 commits ahead of remote `8e290afd70ea` (`5d84dd3b..4081f38b`)
- **CI status:** PENDING — run `29398534333` in_progress on `4081f38b`
- **Gate:** lint 0, typecheck 0, collect OK (gate-lite green)
- **Version:** `0.1.0-beta.4` (pyproject.toml)

## Commits on `development` (`5d84dd3b..4081f38b`)

| # | Commit | Description |
|---|--------|-------------|
| 10 | `4081f38b` | **CI molecule YAML fixes** — gather_facts, failed_when strings, bom_detect script→shell, 8 files |
| 9 | `b62e5eb7` | **enforce-multitask s→_state bug** (107/18 tests), **NF.2 VM sandbox test API fixes** (86/86), CI molecule YAML fixes |
| 8 | `5ddd552a` | **enforce-stop disengage bypass** (13/13), **enforce-multitask text.complete thin-wave block**, **evidence regex narrowed** (hex-letter req), NF.4 radio tests (161 total), CI molecule fixes, lint 0 typecheck 0 |
| 7 | `d1503d9e` | **enforce-stop disengage bypass** (6 checks hardened, 13/13 tests), **enforce-multitask consecutive-non-dispatch reachable** (106/18), evidence regex narrowed, NF.5 validate_scenarios (48 tests), NF.4 sdr+spectrum tests (85), NF.6 connectors confirmed (187), **AGENTS.md subagent fix-dont-check policy** |
| 6 | `3c04ceb5` | **enforce-stop.ts disengage bypass fix** — isDisengaged no longer skips hasRealPendingWork text-only block (13/13 runtime tests) |
| 5 | `5fa60836` | **C.18 accounting tenant scoping** (70 tests), NF.4 radio 3 role scripts, TASKS/SESSION update C.3 closed |
| 4 | `a0ced18d` | **C.3 DB tenant scoping** — fix thread pool test aiosqlite event-loop binding, 11/11 pass |
| 3 | `1e20f907` | **enforce-session-start isTaskFileRead input shape fix**, NF.7 STS injector wiring (72 tests), NF.9 language CLI (28 tests), NF.3 binary_re molecule (8 roles), NF.5 E2E test gen P4 (50 tests), NF.6 OS expert roles+connectors, plugin self-tests (5 new) |
| 2 | `62f1bab8` | **NF.1 chat history P5** (38 tests, 115 total), **NF.2 unikernel P2 image builder** (48 tests), **C.16 filestore RCE sync fix** (21 tests), NF.4 radio 7 roles molecule, collection OK, lint 0 |
| 1 | `5d84dd3b` | enforce-stop.ts fix, enforce-multitask.ts fix, ci-status update, new STS audit pipeline test |

## Enforcement Plugin Fixes

### 1. enforce-stop.ts: Disengage Bypass Fix (commits `3c04ceb5`, `d1503d9e`, `5ddd552a`)

**Bug:** `make disengage-enforcement` was bypassing ALL `text.complete` enforcement in `enforce-stop.ts`, including the fundamental `hasRealPendingWork()` text-only block. Disengaged agents could send text-only summaries while TASKS.md had unchecked items — the core stop pattern the plugin was built to prevent.

**Fix:**
- Disengage now only skips **heuristic checks**: `COMPLETION_SMELL` patterns, `COMPLETION_WORDS` detection, and `QA_RESPONSE_PATTERNS` matching.
- The `hasRealPendingWork()` text-only block is **NEVER bypassed** by disengage.
- 6 checks hardened, 13/13 runtime tests pass.

### 2. Evidence Regex Narrowed (commits `5ddd552a`, `d1503d9e`)

**Bug:** `enforce-verified-claims.ts` regex `\b[0-9a-f]{7,40}\b` matched pure-digit 7+ character strings (CI run numbers, timestamps, build IDs), causing false-positive "evidence present" matches.

**Fix:** Regex narrowed to `\b[0-9a-f]*[a-f][0-9a-f]{6,39}\b` — requires at least one hex letter (`[a-f]`).

### 3. enforce-session-start.ts: isTaskFileRead Input Shape Fix (commit `1e20f907`)

**Bug:** `isTaskFileRead()` extracted `tool_call.path` directly, which was `undefined` when the `path` field was nested inside a `tool_input` object. No file read was ever recognized as a task-tracking file read, blocking the session-start protocol's own escape hatch.

**Fix:** `isTaskFileRead()` now checks both `tool_call.path` and `tool_call.tool_input?.path`.

### 4. enforce-multitask.ts: Three Fixes (commits `5d84dd3b`, `d1503d9e`, `5ddd552a`, `b62e5eb7`)

| Fix | Commit | What changed |
|-----|--------|--------------|
| **Under-floor block fires within same wave** | `5d84dd3b` | Previously blocked on NEXT message; now blocks immediately within the same message when dispatches < 10 |
| **text.complete thin-wave block** | `5ddd552a` | Text-only output after <10 dispatches is now blocked at the text surface, not just tool surface |
| **s→_state state file naming bug** | `b62e5eb7` | State file variable naming bug (`s` instead of `_state`) fixed; 107/18 tests pass |
| **consecutive-non-dispatch reachable** | `d1503d9e` | Groundwork for grinding counter; 106/18 tests pass |

## NF.1–NF.9 Feature Status at HEAD `4081f38b`

| Feature | Status | Latest Milestone | Test Count |
|---------|--------|-----------------|------------|
| **NF.1 - Chat CLI** | **COMPLETED** | P5 history | 115 total (38 this session) |
| NF.2 - Unikernel sandbox | **in-progress** | P2 image builder done; VM sandbox API fixes | 48 + 86 |
| NF.3 - Binary RE | **in-progress** | 8 roles molecule | 6 molecule tests |
| NF.4 - Radio engineer | **in-progress** | SDR+spectrum tests, 3 role scripts | 161 total |
| NF.5 - E2E test gen | **in-progress** | validate_scenarios + P4 | 98 total (48 + 50) |
| NF.6 - OS expert | **in-progress** | Connectors confirmed, roles+connectors | 187 total |
| NF.7 - STS tokens | **in-progress** | Injector wiring | 72 tests |
| **NF.8 - Multitasking enforcement** | **COMPLETED** | text.complete thin-wave block added; s→_state fix | — |
| NF.9 - Language expert | **in-progress** | CLI | 28 tests |

## C.3 / C.16 / C.18 Security Fix Status

| Fix | Status | Tests | Commit |
|-----|--------|-------|--------|
| **C.3 DB tenant scoping** | **FIXED** | 11/11 pass | `a0ced18d` |
| **C.16 Filestore RCE sync** | **FIXED** | 21 tests pass | `62f1bab8` |
| **C.18 Accounting tenant scoping** | **FIXED** | 70 tests pass | `5fa60836` |

- **C.3:** DB tenant-scoped queries via `do_orm_execute` / `with_loader_criteria` with `contextvars`. Thread-pool test aiosqlite event-loop binding fixed.
- **C.16:** `sync_bundled_to_filestore()` now verifies digests before writing to filestore, preventing RCE via filestore corruption.
- **C.18:** Accounting queries now tenant-scoped, preventing cross-tenant accounting data leakage.

## E2E Behavioral Tests Created

**New file:** `tests/e2e/test_behavior_enforcement.py` (404 lines, staged, not yet committed)

| Test | Name | What it verifies | Status |
|------|------|-----------------|--------|
| 1 | `test_text_only_stop_blocked_by_pending_work` | Text-only response with pending work → TEXT-ONLY RESPONSE BLOCKED | ✅ PASS |
| 2 | `test_completion_smell_blocks_continuing_pattern` | "Continuing with..." pattern → COMPLETION_SMELL block | ✅ PASS |
| 3 | `test_disengage_does_not_bypass_pending_work_block` | Disengage active + pending work → hasRealPendingWork STILL blocks | ✅ PASS |
| 4 | `test_thin_wave_blocked_after_dispatching_3` | 3 dispatches (not 10) + text output → THIN WAVE BLOCKED | ✅ PASS |
| 5 | `test_consecutive_nondispatch_blocked_after_threshold` | Consecutive non-dispatch → CONSECUTIVE GRINDING block | ❌ **BUG (xfail)** |
| 6 | `test_zero_dispatch_streak_blocks_fourth_message` | 3 zero-dispatch msgs → ZERO-DISPATCH STREAK block | ❌ **BUG (xfail)** |
| 7 | `test_under_floor_hard_block_after_zero_dispatches` | 0 dispatches + edit → UNDER-FLOOR HARD BLOCK | ✅ PASS |
| 8 | `test_text_with_evidence_passes_through` | Text with commit hash + test counts → passes through | ✅ PASS |

## Known Remaining Bugs

### 1. Zero-Streak & Consecutive-Grinding Unreachability (tests/e2e/test_behavior_enforcement.py tests 5-6)

**Root cause:** `enforce-multitask.ts` `tool.execute.before` fires the UNDER-FLOOR HARD BLOCK on the **first** non-dispatch call (line 228) before the consecutive-non-dispatch counter (line 202) or zero-streak check (line 247) can accumulate.

- `thisMessageDispatches` starts at 0 every message. The UNDER-FLOOR check at line 228 fires on the very first non-dispatch call (`thisMessageDispatches=0 < MIN_DISPATCHES=10`).
- The CONSECUTIVE GRINDING counter never reaches `cc >= threshold` because it's preempted.
- The ZERO-DISPATCH STREAK (`zeroStreak >= 1`) is never evaluated because UNDER-FLOOR fires first.

**Fix needed:** UNDER-FLOOR should not fire when the consecutive-non-dispatch counter has started accumulating within its time window. The grinding counter reaching threshold means the agent IS grinding — block with the specific grinding message, not the generic under-floor block.

**Test status:** `test_consecutive_nondispatch_blocked_after_threshold`: **xfail** (UNDER-FLOOR preempts CONSECUTIVE GRINDING). `test_zero_dispatch_streak_blocks_fourth_message`: **xfail** (UNDER-FLOOR preempts ZERO-DISPATCH STREAK, zeroStreak reaches 2 but is never evaluated).

### 2. `role_open_code_workflow` Stub

**Location:** `collections/ansible_collections/general_ludd/agent/roles/open_code_workflow/`

**Status:** Molecule playbooks exist in `molecule/playbooks/role_open_code_workflow/` with prepare/converge/verify files. Default vars at `defaults/main.yml`. **Missing `tasks/main.yml`** — the role is a stub with no tasks file. The molecule playbooks reference `general_ludd.agent.open_code_workflow` but it cannot execute.

**Impact:** CI molecule runs may fail on this role once molecule CI is fully wired. Should be completed or removed before cutting beta.2.

## CI Status

- **Current run:** `29398534333` — `in_progress` on `4081f38bdef73cb2d86aac4495439504cea70b2c`
- **Branch:** `development`
- **NOT PUSHED** — 10 commits ahead of remote `8e290afd70ea`
- **Action needed:** Push development → wait for CI green → cut beta.2

## Next Steps for Session 37

1. **[ ] Push development branch** — `make batch-push` to land 10 unpushed commits
2. **[ ] Wait for CI green** on HEAD `4081f38b` — use `make ci-verdict-safe BRANCH=development`
3. **[ ] Cut beta.2** — `make release-cut TAG=v0.1.0-beta.2 MSG='10 enforcement fixes + NF.1-NF.9 progress'` (requires CI green)
4. **[ ] Fix zero-streak/grinding unreachability bug** — reorder UNDER-FLOOR vs. consecutive-grinding checks in `enforce-multitask.ts` so grinding is correctly detected and blocked
5. **[ ] Resolve `role_open_code_workflow` stub** — either write `tasks/main.yml` or remove the stub role
6. **[ ] Commit staged E2E test file** — `tests/e2e/test_behavior_enforcement.py` (6 PASS + 2 xfail)
7. **[ ] Continue NF.2-NF.7 feature work** — all are in-progress; NF.2 unikernel, NF.3 binary_re, NF.4 radio, NF.5 E2E test gen, NF.6 OS expert, NF.7 STS tokens, NF.9 language expert
8. **[ ] Verify beta.2 artifact completeness** — `make verify-release-completeness TAG=v0.1.0-beta.2` (learn from beta.1's 1/12 failure)
