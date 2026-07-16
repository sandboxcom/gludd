# Feature: NF.8 — Multitasking Enforcement Hardening

**Status: IMPLEMENTED** | **Created: 2026-07-16** | **Target: v0.1.0-beta.2** | **Type: enforcement fix**

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
| Create | `tests/unit/test_multitask_plugin.py` |
| Create | `tests/unit/test_multitask_min_dispatch.py` |
| Create | `tests/e2e/test_multitask_e2e.py` |

## 5. Test Plan

- **Unit (28 tests)**: `test_multitask_plugin.py` + `test_multitask_min_dispatch.py`
  — structural pin on constants + min-dispatch behavior
- **E2E (97 tests)**: `test_multitask_e2e.py` — invokes actual hooks with
  constructed arguments and asserts on `permissionDecision` + side effects;
  verifies env-var disable, subagent guard, fail-open paths

## 6. Disabling

| Mechanism | Effect |
|-----------|--------|
| `GLUDD_MULTITASK_FLOOR_ENFORCE=0` | Disables entirely |
| `GLUDD_MIN_DISPATCHES=2` | Lower floor for focused single-file work |
| `make disengage-enforcement` | Temporary emergency bypass |

## 7. Evidence

- Commits: `6d45df65` (original fix, development), `db2699da` (hardened,
  9-feature wave), `816d7be6` (latest HEAD)
- 97 + 28 tests passing
- node-v26 `--experimental-strip-types` compatibility verified (no `require()`,
  no forbidden `catch { try` patterns)
