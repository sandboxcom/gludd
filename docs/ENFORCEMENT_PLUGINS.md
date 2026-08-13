# OpenCode Enforcement Plugins — Architecture & Reference

> **Audience**: agents maintaining or extending the enforcement plugin system.
> **Last updated**: 2026-07-13. **Source of truth**: the plugin source files in
> `.opencode/plugin/` — if this doc disagrees with the code, the code is correct.

---

## 1. Architecture Overview

Enforcement plugins are TypeScript modules registered in `opencode.json` under the
`plugin` key. They hook into the OpenCode agent runtime lifecycle to mechanically
prevent failure modes that the project's written policies (AGENTS.md) alone could
not stop. Every plugin follows these design principles:

**Fail-open.** Any internal error (unreadable state file, regex exception, broken
JSON) silently returns the original output or `undefined`. A broken hook must
never wedge the editor or block legitimate work.

**Subagent isolation.** Every hook function checks `OPENCODE_SUBAGENT=1` (env var)
or `/tmp/gludd-subagent-${pid}.json` (file-based fallback) at entry. Subagents
inherit the plugins but skip ALL enforcement — the orchestrator manages
enforcement, not the subagent.

**Disengage respect.** All plugins check the watchdog disengage signal
(`/tmp/gludd-watchdog-disengage.json`) and suspend enforcement when a valid
`disengage_until` timestamp is active.

**Per-plugin heartbeat.** Every plugin writes liveness data to
`/tmp/gludd-plugin-alive.json` and most write per-plugin heartbeat files
(`/tmp/gludd-plugin-heartbeat-<plugin>.json`). This lets the watchdog and tests
verify that hooks are actually firing at runtime.

### 1.1 Hook Surfaces

OpenCode exposes these hook entry points. Enforcement plugins use the subset below:

| Hook | Fires when | Used by |
|---|---|---|
| `tool.execute.before` | A tool call is about to execute | All 14 plugins |
| `tool.execute.after` | A tool call just completed | enforce-delegate, enforce-deadline, enforce-commit-lock, enforce-make |
| `experimental.text.complete` | LLM text stream ends (agent-generated text only) | enforce-floor, enforce-stop, enforce-multitask, enforce-make, enforce-enhancement-ratio, enforce-verified-claims |
| `session.idle` | The session goes idle (turn boundary) | enforce-floor, enforce-stop, enforce-multitask, enforce-make |
| `experimental.chat.system.transform` | System prompt is about to be assembled | enforce-session-start, enforce-make |
| `event` | Raw lifecycle events (e.g., `session.idle`) | enforce-stop |

**IMPORTANT (2026-07-12 finding):** `text.complete` fires ONLY on agent-generated
text end-stream events — never on tool output. All text in `text.complete` is
from the LLM. Do not add role-based guards; they are dead code.

**Hook return semantics:**
- `tool.execute.before`: return `undefined` to allow, return `{permissionDecision:"deny", message:"..."}` to deny cleanly, or `throw Error(...)` to deny with an error message.
- `text.complete`: return the (possibly modified) `{text: string}` output object.
- `system.transform`: return the (possibly modified) system prompt string.

---

## 2. Complete Plugin Table

14 enforcement plugins. All are BLOCKING by default (hard-deny on violations).

| # | Plugin | File | Enforces | Hooks Used | Env Var to Disable | State |
|---|---|---|---|---|---|---|
| 1 | enforce-make | `.opencode/plugin/enforce-make.ts` | Bash make-only: blocks non-make commands, shell metacharacters, concurrent gates, `.gate-status` writes, guardrail-defanging edits, opencode.json schema violations, TDD test-file requirement | `tool.execute.before`, `tool.execute.after`, `session.idle`, `system.transform`, `text.complete` | `GLUDD_MAKE_ENFORCE=0` | **BLOCKING** |
| 2 | enforce-floor | `.opencode/plugin/enforce-floor.ts` | Agent floor/ceiling bands: streak-based grinding detection, session-start dispatch stall, post-result read limit, message-shape enforcement | `tool.execute.before`, `session.idle`, `text.complete` | `GLUDD_FLOOR_ENFORCE=0` | **BLOCKING** |
| 3 | enforce-delegate | `.opencode/plugin/enforce-delegate.ts` | Model utilization (sonnet ratio), disk discipline (worktree ENOSPC guard), force-delegate grind guard, mainthread streak, read-grinding detection | `tool.execute.before`, `tool.execute.after` | `GLUDD_MAINTHREAD_STREAK_ENFORCE=0` (mainthread streak only; model-util: `GLUDD_MODEL_UTIL_ENFORCE=0`; force-delegate: `GLUDD_FORCE_DELEGATE=1` enables) | **BLOCKING** |
| 4 | enforce-stop | `.opencode/plugin/enforce-stop.ts` | False-done claims, stop-pattern detection, question-blocking, after-results text-only block, persistent stop-block, Q&A response patterns, main-thread grinding | `event`, `tool.execute.before`, `system.transform`, `text.complete` | `GLUDD_STOP_ENFORCE=0` disables optional heuristics only; pending-work `text.complete` remains mandatory | **BLOCKING** |
| 5 | enforce-deadline | `.opencode/plugin/enforce-deadline.ts` | Task wall-clock timeout (5 min = 300,000ms). Records dispatch timestamps, warns on breach, writes stale task IDs for `scripts/task_watchdog.py` to kill. | `tool.execute.before`, `tool.execute.after` | `GLUDD_TASK_DEADLINE_ENABLED=0` | **BLOCKING** |
| 6 | enforce-session-start | `.opencode/plugin/enforce-session-start.ts` | Session-start protocol: locates work before mutation and adaptively permits 0-10 dispatches; an explicit minimum enables the 60s warning/120s deny time gate. | `tool.execute.before`, `system.transform` | `GLUDD_SESSION_START_ENFORCE=0` | **BLOCKING** |
| 7 | enforce-enhancement-ratio | `.opencode/plugin/enforce-enhancement-ratio.ts` | Per-wave enhancement/fix ratio: >=50% of dispatches must be enhancements (not just bug fixes). Classifies by prompt keywords. | `tool.execute.before`, `text.complete` | `GLUDD_ENHANCEMENT_RATIO_ENFORCE=0` | **BLOCKING** |
| 8 | enforce-clean-tree | `.opencode/plugin/enforce-clean-tree.ts` | Clean git tree: denies Task/agent/workflow dispatch when `git status --porcelain` is non-empty | `tool.execute.before` | `GLUDD_CLEAN_TREE_ENFORCE=0` | **BLOCKING** |
| 9 | enforce-verified-claims | `.opencode/plugin/enforce-verified-claims.ts` | Evidence-backed claims: blocks text containing done-words ("committed", "shipped", "done", "green", etc.) unless machine-produced evidence (commit hash, test pass count, gate marker) is also present | `text.complete` | `GLUDD_VERIFIED_CLAIMS_ENFORCE=0` | **BLOCKING** |
| 10 | enforce-no-suppressions | `.opencode/plugin/enforce-no-suppressions.ts` | No lint-suppression comments: denies edit/write when content contains `# noqa`, `# type: ignore`, `# pylint:`, `# fmt:`, or `# isort:` | `tool.execute.before` | (hard-coded ON — no env-var disable) | **BLOCKING** |
| 11 | enforce-multitask | `.opencode/plugin/enforce-multitask.ts` | Minimum dispatches per wave: blocks non-dispatch tools when wave has <10 dispatches; enforces zero-streak limit (2 consecutive zero-dispatch messages) | `tool.execute.before`, `session.idle`, `text.complete` | `GLUDD_MULTITASK_FLOOR_ENFORCE=0` | **BLOCKING** |
| 12 | enforce-no-wait | `.opencode/plugin/enforce-no-wait.ts` | Anti-wait: denies bash sleep/tail patterns (`sleep N && make`, `make gate-tail`, `make gate-status-check`) on main thread; denies CI-poll dispatch intent in Task/agent prompts | `tool.execute.before` | `GLUDD_NO_WAIT_ENFORCE=0` | **BLOCKING** |
| 13 | enforce-deletion-gate | `.opencode/plugin/enforce-deletion-gate.ts` | Large-deletion gate: blocks edit/write when lines removed exceeds threshold (default 5) without `DELETION_REASON` set | `tool.execute.before` | (threshold=0 disables) | **BLOCKING** |
| 14 | enforce-commit-lock | `.opencode/plugin/enforce-commit-lock.ts` | Commit serialization: O_EXCL lock wraps commit-shaped `make` targets to prevent parallel subagents racing on git index | `tool.execute.before`, `tool.execute.after` | `GLUDD_COMMIT_LOCK_ENFORCE=0` | **BLOCKING** |

**Support files (not enforcement plugins themselves):**

| File | Purpose |
|---|---|
| `.opencode/plugin/shared.ts` | Shared helpers: `isSubagent()`, `isDisengaged()`, `readJsonFile()`, `writeJsonFile()`, `reportAlive()` — eliminates duplicated patterns across plugins |
| `.opencode/plugin/hot_reload.ts` | Hot-reload proxy: `loadHotModule(name, defaults)` — checks `/tmp/gludd-hot-<name>.js` on each invocation for mid-session updates |

### 2.1 OpenCode blocking contract

OpenCode 1.18.5 passes the executable Bash command to `tool.execute.before` in
the second argument at `output.args.command`; older fixtures use
`input.args.command`. Enforcement plugins must support both shapes. Returning
`{ permissionDecision: "deny" }` does not stop execution: OpenCode awaits the
hook and then invokes the tool without reading the hook's return value. A
blocking plugin must throw a tagged error, while an argument-transforming
plugin may mutate `output.args`.

This behavior is pinned by the real multi-prompt TUI test in
`tests/e2e/test_opencode_tui_permissions.py`, not only by synthetic hook
invocation. The upstream `prompt.ts` call site likewise invokes
`tool.execute.before` immediately before tool execution without assigning its
return value:

- [OpenCode tool execution source](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/prompt.ts)

User reports show this is a long-lived interoperability concern, not a
project-local edge case:

- [Native Claude Code hooks compatibility #12472](https://github.com/anomalyco/opencode/issues/12472)
  requests reliable `PreToolUse` blocking semantics and documents exit-code
  based guardrails; it has remained open since February 2026.
- [AI-visible hook messages #17412](https://github.com/anomalyco/opencode/issues/17412)
  distinguishes the existing ability to block or modify tool calls from the
  still-missing ability to inject continuation messages. It was opened in
  March 2026 and closed as not planned.

---

## 3. Hot-Reload Pattern

OpenCode loads plugins ONCE at startup. A committed change to a plugin `.ts` file
does NOT take effect without a restart. The hot-reload pattern (in `hot_reload.ts`)
provides a workaround for time-sensitive guardrail fixes.

### 3.1 How It Works

1. Each hot-reload-capable plugin is structured as a **proxy wrapper**.
2. The compiled-in defaults are the fallback implementation.
3. On every hook invocation, the proxy calls `loadHotModule(name, defaultImpl)`.
4. `loadHotModule` checks `/tmp/gludd-hot-<name>.js` — if the file exists and its
   mtime is newer than the cached copy, it re-reads and re-parses it.
5. The hot module's hook function overrides the compiled-in default.
6. **No restart needed**: edit the plugin source, run `make hot-reload-plugins`,
   and the next hook call picks up the change.

### 3.2 Plugins Using Hot-Reload

| Plugin | Hot module path | Name parameter |
|---|---|---|
| enforce-deadline | `/tmp/gludd-hot-deadline.js` | `"deadline"` |
| enforce-floor | `/tmp/gludd-hot-floor.js` | `"floor"` |
| enforce-enhancement-ratio | `/tmp/gludd-hot-enhancement-ratio.js` | `"enhancement-ratio"` |

### 3.3 Cache Semantics

- **mtime-based invalidation**: the hot module is only re-parsed when the file's
  mtime changes. No TTL — the mtime IS the invalidation signal.
- **Fail-open**: any error (missing file, parse error, runtime exception) falls
  back to compiled-in defaults silently. The hot module is a best-effort override.
- **Isolated scope**: uses `new Function("exports", code)` instead of `require()`
  or `eval()` — cleaner scope, no module cache side effects.

### 3.4 Plugin Code Pattern

```
import { loadHotModule, type HotModule } from "./hot_reload.ts"

const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => { /* compiled-in logic */ },
  "text.complete": async (output) => { /* compiled-in logic */ },
}

export default (async ({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      const impl = loadHotModule("name", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
    // ... same pattern for each hook
  }
}) satisfies Plugin
```

### 3.5 Gotchas

- The hot module `.js` file must be a standalone JS module that returns an
  object with hook functions. It is generated by a build script triggered by
  `make hot-reload-plugins`.
- Stale hot modules (from a prior session with different plugin structure) can
  cause runtime errors. Run `make reload-enforcement` to reset state files;
  `make verify-plugin-manifest` checks every plugin has the subagent guard.
- Non-hot-reload plugins (enforce-make, enforce-stop, etc.) apply changes only
  after an opencode restart.

---

## 4. Subagent Isolation

Every enforcement plugin must skip enforcement inside a subagent context. The
subagent isolation guard is implemented in `shared.ts` and called by every plugin.

### 4.1 Detection Mechanism

Two layers, checked in order:

1. **Env var**: `process.env.OPENCODE_SUBAGENT === "1"` — set by the OpenCode
   framework when spawning a subagent. This is the preferred detection method.

2. **File-based fallback**: checks for `/tmp/gludd-subagent-${process.pid}.json`.
   If the file exists, the current process IS a subagent. This fallback exists
   because the `OPENCODE_SUBAGENT` env var is not guaranteed to be set by the
   OpenCode framework in all configurations.

### 4.2 Implementation

```typescript
// shared.ts
export function isSubagent(): boolean {
  if (process.env.OPENCODE_SUBAGENT === "1") return true
  try {
    return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`)
  } catch {
    return false
  }
}
```

### 4.3 Usage in Plugins

Every hook function starts with:

```typescript
if (isSubagent()) return     // (or return output, for hooks that return a value)
console.log("SUBAGENT SKIP: <plugin-name>")
```

The `console.log` is intentional — it provides an observable signal in logs
that subagent isolation is active.

### 4.4 What Happens Without Isolation

If a plugin's subagent guard is broken (stale hot module, missing guard code):

- The subagent sees the same enforcement as the orchestrator.
- If `enforce-floor.ts` blocks non-dispatch calls, the subagent can't use `make`,
  read files, or edit code — it's deadlocked.
- If `enforce-make.ts` blocks non-make bash, the subagent can only run `make`
  targets with no metacharacters — severe but usually functional.
- Run `make verify-plugin-manifest` to detect missing guards; run
  `make hot-reload-plugins` to rebuild stale hot modules.

---

## 5. State File Map

All state files live in `/tmp/` (or an override path via env var). The table
below maps each file to its owning plugin(s) and what it contains.

### 5.1 Shared/Global State

| State File | Owner(s) | Contents |
|---|---|---|
| `/tmp/gludd-plugin-alive.json` | All plugins (via `reportAlive`) | `{ "<plugin>": {last_seen: <epoch_ms>}, ... }` — liveness probe for watchdog |
| `/tmp/gludd-plugin-loaded.log` | All plugins | Append-only log: `"ISO LOADED <plugin> hooks pid=<pid>"` — proves opencode invoked the factory |
| `/tmp/gludd-plugin-heartbeat-<plugin>.json` | Per plugin | `{plugin:"...", ts:<epoch_ms>, pid:<int>}` — runtime evidence the hook fires |
| `/tmp/gludd-watchdog-disengage.json` | watchdog (read by all plugins) | `{disengage_until: <epoch_ms>}` — emergency enforcement suspension; clamped to max 1 hour |
| `/tmp/gludd-subagent-<pid>.json` | Framework | Existence = subagent marker for file-based fallback detection |
| `/tmp/gludd-tool-streak.json` | enforce-floor, enforce-stop | `{streak, readStreak, editStreak, lastDispatchTs, lastUpdateTs, lastWriter, pid}` — shared cross-plugin grinding counter |
| `/tmp/gludd-force-dispatch.json` | enforce-delegate, enforce-stop | `{active, dispatch_count, dispatch_commands[], reason, ts}` — specific dispatch commands for agent when blocked |
| `/tmp/gludd-hot-<name>.js` | hot_reload.ts | Compiled hot module for `enforce-<name>.ts` (deadline, floor, enhancement-ratio) |

### 5.2 Per-Plugin State

| State File | Plugin | Contents |
|---|---|---|
| `/tmp/gludd-floor-override` | enforce-floor | Bare integer (or absent) — runtime floor override; takes priority over env var |
| `/tmp/gludd-ceiling-override` | enforce-floor | Bare integer — runtime ceiling override |
| `/tmp/gludd-floor-text-complete-count.json` | enforce-floor | `{count, last_fired, ts}` — text.complete fire counter |
| `/tmp/gludd-read-grind.json` | enforce-floor, enforce-delegate | `{count, lastDispatchTs, ts}` — investigation-tool streak tracking |
| `/tmp/gludd-session-start.json` | enforce-session-start, enforce-floor | `{started_at, readsDone, dispatches, timeGateReset}` — session-start protocol state |
| `/tmp/gludd-task-deadlines.json` | enforce-deadline | `{ "<task_id>": <start_epoch_ms>, ... }` — active task dispatch timestamps |
| `/tmp/gludd-task-deadlines.warnings.log` | enforce-deadline | Append-only: `"ISO TASK DEADLINE EXCEEDED: task <id> ..."` |
| `/tmp/gludd-task-stale.json` | enforce-deadline | `[{task_id, start_ms, elapsed_ms, stale_at}]` — breached tasks for watchdog killing layer |
| `/tmp/gludd-enhancement-ratio.json` | enforce-enhancement-ratio | `{wave: [{type, prompt_head, ts}], session_enhancements, session_fixes, session_unknown, early_warned, lastPid, lastTs}` |
| `/tmp/gludd-model-util.json` | enforce-delegate | `{history: ["sonnet"|"non-sonnet", ...]}` — rolling window of model dispatch types |
| `/tmp/gludd-force-delegate.json` | enforce-delegate | `{consecutive_targeted, consecutive_denied}` — force-delegate grind counter |
| `/tmp/gludd-mainthread-streak.json` | enforce-delegate | `{count, ts}` — consecutive mainthread mutating call counter |
| `/tmp/gludd-stop-state.json` | enforce-stop | `{ts, ratchetEntries, tasksMdUnchecked, gateStatusRed, repoPending, hasPendingWork, hasLocalWork, ciVerdictPendingOrRed, healthScore, watchdogDisengage}` |
| `/tmp/gludd-block-reason.json` | enforce-stop | `{reason, consecutive, ts}` — last block reason for diagnostics |
| `/tmp/gludd-block-counter.json` | enforce-stop | `{consecutiveBlocks, totalBlocks, lastBlockTs, disengageUntil}` — false-positive cascade detection (disengages after 20 consecutive blocks for 2 min) |
| `/tmp/gludd-blanked-responses.json` | enforce-stop | `{totalBlanked, blankedThisSession, lastBlankedTs, escalationLevel}` — tracks blanked responses |
| `/tmp/gludd-persist-stop-block.json` | enforce-stop | `{blocked, timestamp, reason}` — survives across turns; forces dispatch on next tool call |
| `/tmp/gludd-post-results-state.json` | enforce-stop | `{lastTurnHadResults, lastTurnHadWave, lastTurnTs, lastResultCount}` — for after-results text-only block |
| `/tmp/gludd-text-only-state.json` | enforce-stop | `{count, lastTs, sameSession}` — consecutive text-only response counter |
| `/tmp/gludd-false-done-blocks.json` | enforce-stop | Array of `{ts, iso, reason, textLength, textPreview}` — audit log of blocked false-done claims |
| `/tmp/gludd-stop-tool-counts.json` | enforce-stop | `{allowed, blocked, last_blocked, last_allowed}` — tool call pass/block tracking |
| `/tmp/gludd-stop-text-complete-count.json` | enforce-stop | `{count, last_fired, ts}` — text.complete fire counter |
| `/tmp/gludd-continue.txt` | enforce-stop (reads) | Watchdog-injected "CONTINUE" directive |
| `/tmp/gludd-watchdog-ci.json` | watchdog (read by enforce-stop, enforce-floor) | `{last_ci_check, last_ci_status}` — cached CI verdict to avoid redundant API calls |
| `/tmp/gludd-multitask-state.json` | enforce-multitask | `{thisMessageDispatches, prevMessageDispatches, zeroStreak, estimatedInFlight, lastTs, lastToolCallTs, waveHistory}` |
| `/tmp/gludd-commit.lock` | enforce-commit-lock | Contains PID of lock holder — O_EXCL commit serialization |
| `/tmp/gludd-enforce-floor-error.log` | enforce-floor | Error log for text.complete failures |

### 5.3 State File Lifecycle

- **Stale reset**: many plugins reset their state when PID changes (new session)
  or when `lastUpdateTs` is older than a threshold (60s for streak files, 3x TTL
  for deadline files).
- **Atomic writes**: all JSON state files use a `.tmp` + `fs.renameSync()` pattern
  to prevent torn reads.
- **PID-unique temp files**: `enforce-session-start.ts` uses
  `${STATE_FILE}.tmp.${process.pid}` to prevent concurrent writers from
  clobbering each other's temp files.
- **Cleanup**: `make clean-tmp` removes stale state files (PID no longer running).

---

## 6. Emergency Escape Hatches

When enforcement is preventing legitimate work (e.g., unwaivable compliance
work, emergency hotfix), these mechanisms bypass enforcement.

### 6.1 Global Escape Hatches

| Mechanism | Target | Effect |
|---|---|---|
| `make disengage-enforcement` | `make` target | Writes `{disengage_until: <now + 1h>}` to `/tmp/gludd-watchdog-disengage.json`. Optional heuristics may suspend for at most 1h; mandatory safety invariants, including stop-on-pending-work, remain active. |
| `make reload-enforcement` | `make` target | Resets ALL enforcement state files to pick up env var changes. Does NOT reload plugin code (needs opencode restart). |
| Env var `GLUDD_*_ENFORCE=0` | Per plugin | Disables the documented optional checks for that plugin; explicitly mandatory invariants remain active. |

### 6.2 Per-Plugin Escape Hatches

| Plugin | Env Var to Disable |
|---|---|
| enforce-make | `GLUDD_MAKE_ENFORCE=0` |
| enforce-floor | `GLUDD_FLOOR_ENFORCE=0` |
| enforce-delegate | `GLUDD_MAINTHREAD_STREAK_ENFORCE=0`, `GLUDD_MODEL_UTIL_ENFORCE=0`, `GLUDD_FORCE_DELEGATE=1` (opt-in) |
| enforce-stop | `GLUDD_STOP_ENFORCE=0` (optional heuristics only; mandatory pending-work text guard remains) |
| enforce-deadline | `GLUDD_TASK_DEADLINE_ENABLED=0` |
| enforce-session-start | `GLUDD_SESSION_START_ENFORCE=0` |
| enforce-enhancement-ratio | `GLUDD_ENHANCEMENT_RATIO_ENFORCE=0` |
| enforce-clean-tree | `GLUDD_CLEAN_TREE_ENFORCE=0` |
| enforce-verified-claims | `GLUDD_VERIFIED_CLAIMS_ENFORCE=0` |
| enforce-no-suppressions | (none — hard-coded ON) |
| enforce-multitask | `GLUDD_MULTITASK_FLOOR_ENFORCE=0` |
| enforce-no-wait | `GLUDD_NO_WAIT_ENFORCE=0` |
| enforce-deletion-gate | threshold=0 (disable), or set `DELETION_REASON="reason"` to pass |
| enforce-commit-lock | `GLUDD_COMMIT_LOCK_ENFORCE=0` |

### 6.3 Anti-Wedge Safeguards

- **False-positive cascade**: `enforce-stop.ts` tracks `consecutiveBlocks`. After 20
  consecutive blocks within 2-minute windows, it auto-disengages for 2 minutes to
  prevent permanent wedging.
- **Stale lock break**: `enforce-commit-lock.ts` breaks locks older than 5 minutes
  (stale threshold), allowing commits to resume if the lock-holder crashed.
- **Disengage clamp**: all disengage checks clamp `disengage_until` to max 1 hour
  from now, preventing permanently disabled enforcement from a stale timestamp.

---

## 7. Adding a New Plugin

### 7.1 Checklist

Before writing code:

1. **Identify the failure mode.** What specific agent behavior does this plugin
   prevent? Every plugin exists because past sessions demonstrated a real failure
   that written policy alone could not stop. Document the incident(s) it prevents.
2. **Choose the hook surface.** `tool.execute.before` for blocking tool calls;
   `text.complete` for mutating/blocking agent-generated text; `system.transform`
   for injecting directives; `session.idle` for state resets.
3. **Decide on env-var disable.** Every plugin must have a `GLUDD_<NAME>_ENFORCE=0`
   escape hatch (except `enforce-no-suppressions`, which is hard-coded ON by design).
4. **Decide on hot-reload.** If the plugin enforces a policy likely to need tuning
   within a session, implement the hot-reload proxy pattern. If it's stable,
   skip hot-reload (simpler).

### 7.2 Code Template

```typescript
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

// Optional: import shared helpers
// import { isSubagent, isDisengaged, reportAlive, readJsonFile, writeJsonFile } from "./shared.ts"

const ENFORCE = process.env.GLUDD_MYPLUGIN_ENFORCE !== "0"

function _isSubagent(): boolean {
  if (process.env.OPENCODE_SUBAGENT === "1") return true
  try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`) } catch { return false }
}

function _reportAlive(): void {
  try {
    const alivePath = "/tmp/gludd-plugin-alive.json"
    let alive: Record<string, unknown> = {}
    try { if (fs.existsSync(alivePath)) alive = JSON.parse(fs.readFileSync(alivePath, "utf8")) } catch {}
    alive["enforce-myplugin"] = { last_seen: Date.now() }
    fs.writeFileSync(alivePath, JSON.stringify(alive), "utf8")
  } catch {}
}

export default (async ({ }) => {
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-myplugin pid=${process.pid}\n`, "utf8"
    )
  } catch {}

  return {
    "tool.execute.before": async (input: any, output: any) => {
      if (_isSubagent()) return
      console.log("SUBAGENT SKIP: enforce-myplugin")
      _reportAlive()
      try {
        if (!ENFORCE) return

        // --- your enforcement logic here ---
        // Return undefined to allow.
        // Return {permissionDecision:"deny", message:"..."} to deny cleanly.
        // Throw Error(...) to deny with error message.

      } catch {
        // fail-open
      }
    },
  }
}) satisfies Plugin
```

### 7.3 Required Tests

Per AGENTS.md "Self-Test Quality — Structural vs Behavioral":

1. **At least one runtime test** invoking the actual hook function with
   constructed arguments and asserting on the return value. Use
   `scripts/test_hook_runtime.py` as the harness or write a Python test that
   spawns a node process to evaluate the plugin.
2. **Hook lifecycle tests** covering: (a) normal operation (violation -> block),
   (b) env-var disable path (`GLUDD_*_ENFORCE=0` -> allow), (c) subagent guard
   (`OPENCODE_SUBAGENT=1` -> allow), (d) fail-open (corrupt state / exception ->
   allow).
3. **Structural tests** in `tests/unit/` that verify the plugin file exists,
   exports the expected hooks, and has the subagent guard at the top of each
   hook function.

### 7.4 Registration

1. Add the plugin file to `.opencode/plugin/`.
2. Register it in `opencode.json` under the `"plugin"` key (the exact entry
   format depends on the OpenCode plugin loader).
3. Add a section to this doc (Section 2 table + any new state files in Section 5).
4. Run `make verify-plugin-manifest` to confirm registration.
5. Restart opencode for the plugin to take effect.
