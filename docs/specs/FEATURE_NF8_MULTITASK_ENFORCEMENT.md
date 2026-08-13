# Feature: NF.8 — Multitasking Enforcement Hardening

**Status: CONFIRMED-COMPLETE** | **Created: 2026-07-16** | **Verified: 2026-08-02** | **Target: v0.1.0-beta.2** | **Type: enforcement fix**

## 1. Problem

The 10-agent dispatch floor was bypassable. Agents could grind the main
thread with rapid-fire read-only tool calls (read/grep/glob) without ever
dispatching subagents, while technically satisfying message-shape rules.
The prior enforcement (message-shape + streak counters) was defeated by:

- Responses carrying 1 dispatch + N inline reads, each resetting counters.
- Rapid-fire read-only messages with zero dispatches between waves.
- `thisMessageDispatches` counter persisting across message boundaries
  (boundary detection missed transitions via time-gap heuristic).

## 2. Root Cause

Two gaps in `enforce-multitask.ts` + `enforce-delegate.ts`:

1. **No per-call counter with a time window.** Streak counters operated at
   message granularity, not per-tool-call. A burst of 20 reads in 5s
   bypassed the floor while the counter saw "0 dispatches in one message."
2. **Message boundary mis-detection.** `thisMessageDispatches` inflated
   across messages because the time-gap heuristic (`MSG_GAP_MS`) missed
   rapid-fire responses, causing the floor count to accumulate incorrectly.

## 3. Fix

### Consecutive non-dispatch counter (`enforce-multitask.ts`)

- `CONSECUTIVE_NON_DISPATCH_THRESHOLD = 5` (env: `GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD`)
- `CONSECUTIVE_NON_DISPATCH_WINDOW_MS = 30000` (30s sliding window)
- Every non-dispatch tool call (read/glob/grep/edit/write/bash) increments
  `consecutiveNonDispatch`. Dispatches reset it to 0. At threshold = 5,
  ALL non-dispatch tool calls are blocked with `GRINDING BLOCKED`.

### `text.complete` canonical boundary signal

- `handleMessageBoundary()` runs at end of `text.complete` to reset
  `thisMessageDispatches`. Time-gap / pattern / high-water-mark detection
  in `tool.execute.before` is now a FALLBACK only, gated behind a
  module-level flag to prevent double-processing.

### Under-floor hard block (2026-07-15)

- Block fires IMMEDIATELY when <10 dispatches in current message — does
  NOT wait for a message boundary. Closes the "dispatch 1 then grind"
  bypass.

## 4. Files

| Action | Path |
|--------|------|
| Modify | `.opencode/plugin/enforce-multitask.ts` |
| Modify | `.opencode/plugin/enforce-delegate.ts` |
| Create | `.opencode/plugin/enforce-additive-task.ts` |
| Create | `.opencode/plugin/enforce-floor-v2.ts` |
| Create | `scripts/check_dispatch_diversity.py` |
| Create | `tests/unit/test_multitask_plugin.py` |
| Create | `tests/unit/test_multitask_min_dispatch.py` |
| Create | `tests/e2e/test_multitask_e2e.py` |
| Create | `tests/unit/test_multitasking_enforcement.py` |
| Create | `tests/unit/test_multitasking_grinding_block.py` |
| Create | `tests/unit/test_multitasking_backlog.py` |
| Create | `tests/unit/test_multitask_pressure_release.py` |

## 5. Test Plan

- **Unit (structural)**: `test_multitask_plugin.py` (973 lines) — structural pin
  on constants, diversity classification, topic clustering, pressure-release state
- **Unit (behavioral)**: `test_multitask_min_dispatch.py` (819 lines) — min-dispatch
  config, subagent guard, disable path, fail-open, plugin export shape
- **Unit (supplementary)**: `test_multitasking_enforcement.py`,
  `test_multitasking_grinding_block.py`, `test_multitasking_backlog.py`,
  `test_multitask_pressure_release.py` — grinding, backlog, pressure-release
- **E2E (41 tests)**: `test_multitask_e2e.py` (1187 lines) — invokes actual hooks
  with constructed arguments; verifies subagent guard, env disable, dispatch
  counting, zero-streak, grinding block, under-floor block, diversity check,
  pressure-release, topic boundary reset

## 6. Disabling

| Mechanism | Effect |
|-----------|--------|
| `GLUDD_MULTITASK_FLOOR_ENFORCE=0` | Disables floor/grinding enforcement |
| `GLUDD_MULTITASK_DIVERSITY_ENFORCE=0` | Disables topic diversity check |
| `GLUDD_ADDITIVE_TASK_ENFORCE=0` | Disables additive-task (continuation) check |
| `GLUDD_MIN_DISPATCHES=2` | Lower floor for focused single-file work |
| `make disengage-enforcement` | Temporary emergency bypass |

## 7. Evidence

- Commits: `6d45df65` (original fix, development), `db2699da` (hardened,
  9-feature wave), `816d7be6` (latest HEAD), `36e1ea1a` (current HEAD)
- Enforcement plugins verified active: enforce-multitask.ts (615 lines),
  enforce-additive-task.ts (192 lines), enforce-floor-v2.ts
- Diversity script: `scripts/check_dispatch_diversity.py` (194 lines, 10 topics)
- Test infrastructure: 6 unit files + 1 e2e file
- node-v26 `--experimental-strip-types` compatibility verified (no `require()`,
  no forbidden `catch { try` patterns)
- Config module: `.opencode/lib/multitask_config.ts` (39 lines, shared constants)

## 8. Verified-Complete Evidence (2026-08-02)

All three fix vectors confirmed present and active in
`.opencode/plugin/enforce-multitask.ts`:

| Fix vector | Location | Verified |
|---|---|---|
| Consecutive non-dispatch counter | `enforce-multitask.ts:374-382` | `consecutiveNonDispatch` increments on every non-dispatch tool call within `CONSECUTIVE_NON_DISPATCH_WINDOW_MS` (30s). At `CONSECUTIVE_NON_DISPATCH_THRESHOLD` (5), all non-dispatch tools blocked with "GRINDING BLOCKED". Dispatch resets counter to 0 (`:235-236, :299-300`). |
| `handleMessageBoundary()` via text.complete | `enforce-multitask.ts:194, :538, :560` | Canonical boundary signal at `text.complete` end — resets `thisMessageDispatches`. Time-gap heuristic kept as FALLBACK only, gated behind flag to prevent double-processing (`:270-287`). |
| Under-floor hard block (2026-07-15) | `enforce-multitask.ts:421-422` | Block fires IMMEDIATELY when <10 dispatches in current message — does NOT wait for message boundary. Closes "dispatch 1 then grind" bypass. |

### Practitioner evidence: explicit turn boundaries

OpenCode users have reported that existing hooks do not provide a reliable signal
for the end of an assistant turn, motivating a dedicated turn-completed event
([anomalyco/opencode#23503](https://github.com/anomalyco/opencode/issues/23503)).
Gludd therefore treats `experimental.text.complete` as its canonical available
message boundary and pins the user-visible block kind for a zero-dispatch wave as
`ZERO-DISPATCH TEXT BLOCKED`; the runtime test must assert that current contract,
not the retired `MUST DISPATCH` wording.

### Runtime verification

```
make test-hook-runtime → 52 functional tests across 8 plugins (PASS)
enforce-multitask.test.node.mjs → runtime behavioral tests T1-T6 pass
  T5: CONSECUTIVE_NON_DISPATCH_THRESHOLD === 5
  T6: CONSECUTIVE_NON_DISPATCH_WINDOW_MS === 30000
test_multitask_e2e.py → 97 E2E tests (invokes actual hooks)
```
