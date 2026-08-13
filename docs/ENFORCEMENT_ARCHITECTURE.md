# Enforcement Plugin Architecture

> **Audience**: agents and operators maintaining the enforcement plugin system.
> **Companion docs**:
> - `docs/ENFORCEMENT_PLUGIN_REGISTRY.md` — per-plugin operator reference (what each plugin blocks, disable env var). Verify currency via `make test-specific TESTFILE='tests/unit/test_enforcement_registry'`.
> - `docs/ENFORCEMENT_PLUGINS.md` — historical deep-dive on hot-reload + subagent isolation patterns.
>
> **Source of truth**: `.opencode/plugin/*.ts` and `.opencode/plugins/*.ts` source files.
> If this doc disagrees with the code, the code is correct. This document is
> pinned by `tests/unit/test_enforcement_architecture_doc.py`.

---

## 1. Overview

The enforcement layer is a set of TypeScript plugins loaded by the opencode
runtime at startup. They mechanically prevent the recurring failure modes
documented in `AGENTS.md` and `BUGS.md` — premature stops, commit bypasses,
zero-dispatch grinding, false "done" claims, dirty-tree dispatch races, and
the rest of the project's incident history.

- **Plugin count**: 28 plugins registered in `opencode.json` under the `plugin`
  key (27 `enforce-*.ts` files plus `watchdog.ts`).
- **Location**: `.opencode/plugin/*.ts` (enforcement), `.opencode/plugins/watchdog.ts`
  (liveness observer).
- **Shared library**: `.opencode/lib/shared.ts` — common helpers imported by
  every plugin.
- **Hot-reload proxy**: `.opencode/lib/hot_reload.ts` — runtime override pattern.
- **Policy layer**: `AGENTS.md` codifies the agent-visible policy each plugin
  enforces. The plugins are the machine layer of the three-layer guardrail
  pattern (config permission → runtime hook → agent prompt).

### Design principles (every plugin obeys these)

1. **Fail-open.** Any internal error (unreadable state file, regex exception,
   broken JSON) silently returns the original output or `undefined`. A broken
   hook must never wedge the editor or block legitimate work.
2. **Subagent isolation.** Every hook function calls `isSubagent()` at entry.
   Subagents inherit the plugins but skip ALL enforcement — the orchestrator
   manages enforcement, not the subagent.
3. **Disengage respect.** All blocking plugins honor the watchdog disengage
   signal (`/tmp/gludd-watchdog-disengage.json`) and suspend enforcement when
   a valid `disengage_until` timestamp is active.
4. **Per-plugin heartbeat.** Every plugin writes liveness data to
   `/tmp/gludd-plugin-alive.json` via `reportAlive()` so the watchdog and the
   runtime hook test harness can verify hooks are actually firing.
5. **Disable env var.** Every blocking plugin exposes a `GLUDD_*_ENFORCE=0`
   knob (with one intentional exception: `enforce-no-suppressions.ts` is
   hard-coded ON because lint suppressions are never legitimate).

---

## 2. Architecture — The Hot-Reload Proxy Pattern

### 2.1 The Problem

OpenCode loads plugins ONCE at startup. A committed change to a `.ts` plugin
file does NOT take effect without a full opencode restart. A guardrail fix
committed mid-session would otherwise require the operator to restart — and
during a long automated session, that means stopping work, restarting, and
losing live subagent context.

### 2.2 The Workaround

Each hot-reload-capable plugin is structured as a **proxy wrapper** around a
compiled-in default implementation. On every hook invocation the proxy calls
`loadHotModule(name, defaultImpl)`:

1. Checks `/tmp/gludd-hot-<name>.js` — a standalone JS module compiled from
   the plugin's source.
2. If the file exists AND its mtime is newer than the cached copy, re-reads
   and re-parses it.
3. The hot module's hook function overrides the compiled-in default.
4. **No restart needed**: edit the plugin source, run `make hot-reload-plugins`,
   and the next hook call picks up the change.

```
┌─────────────────────────────────────────────────────────────────┐
│  opencode runtime (calls hook on every tool invocation)         │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Plugin export default factory (the proxy)                      │
│                                                                 │
│    "tool.execute.before": async (input, output) => {            │
│      if (isSubagent()) return                                   │
│      const impl = loadHotModule("name", defaultImpl)            │
│      const fn = impl["tool.execute.before"]                     │
│      return fn ? await fn(input, output) : undefined            │
│    }                                                            │
└────────────────────────┬────────────────────────────────────────┘
                         │
            ┌────────────┴───────────────┐
            ▼                            ▼
┌───────────────────────┐    ┌──────────────────────────────┐
│  loadHotModule path   │    │  compiled-in defaultImpl     │
│  /tmp/gludd-hot-      │    │  (fallback when hot module   │
│  <name>.js (mtime-    │    │  missing, unparsable, or     │
│  checked, cached)     │    │  throws)                     │
└───────────────────────┘    └──────────────────────────────┘
```

### 2.3 Cache + Fail-Open Semantics

- **mtime-based invalidation** — the hot module is only re-parsed when the
  file's mtime changes. No TTL; the mtime IS the invalidation signal.
- **Fail-open** — any error (missing file, parse error, runtime exception,
  `ReferenceError` on a missing import) falls back to `defaultImpl` silently.
  The hot module is a best-effort override, never a source of breakage.
- **Cache eviction** — `delete _require.cache[_require.resolve(hotPath)]`
  ensures a stale module isn't served from the require cache.
- **Legacy shape fallback** — if the hot module exports zero keys (an old
  shape), `legacyExportsObject()` parses hook names out of the source text so
  a partially-built module still produces a usable shape.

### 2.4 Required Code Pattern

Every hot-reload-capable plugin follows this shape exactly:

```typescript
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"

const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => { /* compiled-in */ },
  "text.complete":       async (output) => { /* compiled-in */ },
}

export default (async ({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return
      const impl = loadHotModule("name", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
    // ...same pattern for each hook
  }
}) satisfies Plugin
```

### 2.5 Important Caveats

- **Plugin source changes still require restart.** Hot-reload only applies
  when the plugin already follows the proxy pattern AND `make hot-reload-plugins`
  has been run after the edit. The hot module is a `/tmp/` artifact, not the
  committed source.
- **Stale hot modules break things.** A hot module from a prior session with
  different plugin structure can throw at runtime. Run
  `make reload-enforcement` to reset state files; `make verify-plugin-manifest`
  checks every plugin has the subagent guard.
- **Cross-plugin imports are forbidden.** The 2026-07-24 incident
  (`enforce-floor.ts` calling `incrementTextCompleteCount` defined in
  `enforce_stop_impl.ts` without importing it) crashed opencode at boot.
  Cross-plugin function references must be inlined. Verified by
  `make check-plugin-hook-invoke`.
- **Registered entrypoints must exist and load.** A practitioner report shows
  a missing OpenCode plugin dependency surfacing as `ERR_MODULE_NOT_FOUND` and
  leaving every later prompt silently unresponsive
  ([issue #28286](https://github.com/anomalyco/opencode/issues/28286)). The
  manifest/file integrity check and runtime import tests therefore fail closed
  when a registered proxy entrypoint is absent, even if its implementation
  module still exists elsewhere in the tree.
- **Live binary tests must provide their own local model.** OpenCode users have
  reported `opencode run` hanging immediately with both hosted credentials and
  local models ([issue #1418](https://github.com/anomalyco/opencode/issues/1418)),
  while non-interactive pipeline users have independently reported blocked or
  cancelled headless runs
  ([issue #13851](https://github.com/anomalyco/opencode/issues/13851)). Therefore
  `tests/e2e/test_opencode_binary_boot.py` runs the real OpenCode binary and real
  plugin loader against the in-process deterministic OpenAI-compatible provider.
  This keeps plugin/crash assertions live while eliminating external provider,
  credential, and network latency from the boot gate. OpenCode also reconciles
  `.opencode/package.json` to the running binary's `@opencode-ai/plugin` version;
  users have documented that installer path and its startup impact
  ([issue #26003](https://github.com/anomalyco/opencode/issues/26003)), and the
  plugin documentation requires matching the plugin package to the targeted
  OpenCode release
  ([OpenCode plugin dependencies](https://opencode.ai/v2/docs/build/plugins#installation-and-dependencies)).
  Users have also reproduced OpenCode creating or updating dependency files in
  every project-local `.opencode/` directory on launch
  ([issue #11147](https://github.com/anomalyco/opencode/issues/11147)). A copied
  working directory alone is not a complete subprocess boundary: Python's
  `cwd=` does not rewrite inherited `PWD`, `OPENCODE_CONFIG`, or
  `OPENCODE_CONFIG_DIR`, and OpenCode exposes both config-path variables as
  supported overrides
  ([OpenCode CLI environment variables](https://opencode.ai/docs/cli/#environment-variables)).
  The E2E therefore copies `opencode.json` and `.opencode/` into a pytest-owned
  temporary project before boot, then pins all three path variables to that
  copy. Dependency reconciliation remains exercised, but it can update only the
  disposable copy, never the tracked release tree.

---

## 3. Plugin Lifecycle

```
opencode.json: plugin[] ─┐
                         │  (load order = array order; earlier wins on ties)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Plugin loader (once at startup)                                │
│                                                                 │
│  1. Reads each plugin path under the `plugin` key.              │
│  2. Imports the module (`import "./.opencode/plugin/foo.ts"`).  │
│  3. Calls the default export (the factory function).            │
│  4. Receives a hook map:                                        │
│        { "tool.execute.before": fn, "text.complete": fn, ... }  │
│  5. Registers each hook under its surface name.                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Hook invocation (per tool call / text emission / session tick) │
│                                                                 │
│  runtime event ──► registered hook fn ──► return value:         │
│                                                   │             │
│         tool.execute.before:                     │ allow: undefined
│         { permissionDecision: "deny", message }  │ deny:  object
│                                                   │             │
│         text.complete:        modified { text }  │             │
│         system.transform:     modified string     │             │
│         session.idle:         void                 │             │
└─────────────────────────────────────────────────────────────────┘
```

### 3.1 Stages

1. **Registration** — `opencode.json` `plugin` array lists every active
   plugin path. Order matters: earlier-listed plugins win on hook ties.
2. **Factory call** — at startup the loader imports each module and calls
   its `export default` function. The factory returns the hook map.
3. **Hook invocation** — the runtime fires hooks at the documented surfaces.
   Each hook returns either `undefined` (allow / no-op) or a structured
   object (deny / modified text).
4. **Heartbeat** — every successful hook invocation calls `reportAlive()`,
   stamping `/tmp/gludd-plugin-alive.json` so external watchers can confirm
   the hook is firing.

### 3.2 Hot-Reload Lifecycle (mid-session)

1. Operator edits `.opencode/plugin/foo.ts` source.
2. `make hot-reload-plugins` compiles the source to `/tmp/gludd-hot-foo.js`.
3. On the NEXT hook invocation, `loadHotModule("foo", defaultImpl)` detects
   the new mtime, re-requires the module, and returns its hook map.
4. The proxy delegates to the new implementation. No restart.

### 3.3 Verification Commands

| Command | Purpose |
|---|---|
| `make verify-enforcement` | Check all plugins are healthy + present. |
| `make verify-plugin-manifest` | Every plugin has the subagent guard. |
| `make check-plugin-validate` | Static analysis (Node v26 compat, imports, hook shape). |
| `make check-plugin-hook-invoke` | Runtime hook invocation — catches `ReferenceError` import-checks miss. |
| `make check-node-v26-compat` | Plugin code parseable by Node v26 `--experimental-strip-types`. |
| `make test-hook-runtime` | Functional hook runtime tests across all plugins. |
| `make list-plugins` | Full roster with hooks and block conditions. |

### 3.4 Deterministic OpenCode Boundary Tests

Default enforcement tests do not use ``opencode run``. That subcommand opens
a model session, couples a policy test to provider availability and cost, and
has long-lived user reports of failing to terminate: OpenCode issue
[#5888](https://github.com/anomalyco/opencode/issues/5888) reported recurring
CLI hangs in December 2025, and issue
[#17516](https://github.com/anomalyco/opencode/issues/17516) demonstrated in
March 2026 that the process can remain alive after every tool call completes.

Gludd therefore verifies the boundary in two bounded layers:

1. ``tests/e2e/test_opencode_binary_boot.py`` starts the real
   ``opencode serve`` loader on an ephemeral loopback port, observes its boot
   log, and terminates the child deterministically.
2. ``tests/e2e/test_opencode_enforce_make.py`` and
   ``make test-hook-runtime`` load the real TypeScript source through the Node
   hook harness and execute allow/deny inputs without a model or network call.

Provider-backed agent loops belong only in explicitly live tests with their
own credentials, budget and teardown contract; they are not a prerequisite
for offline enforcement correctness.

---

## 4. Hook Surfaces

OpenCode exposes these hook entry points. Enforcement plugins use the subset
documented below.

| Hook | Fires when | Used by (representative) | Return shape |
|---|---|---|---|
| `tool.execute.before` | A tool call is about to execute | All 27 enforce-* plugins | `undefined` to allow, `{permissionDecision:"deny", message:"..."}` to deny, `throw` to deny with error |
| `tool.execute.after` | A tool call just completed | enforce-delegate, enforce-deadline, enforce-commit-lock, enforce-make | `undefined` (observation only) |
| `experimental.text.complete` | LLM text stream ends (agent-generated text only) | enforce-floor, enforce-stop, enforce-multitask, enforce-make, enforce-enhancement-ratio, enforce-verified-claims, enforce-audit, enforce-anti-essay | modified `{text: string}` |
| `experimental.chat.system.transform` | System prompt is about to be assembled | enforce-session-start, enforce-make | modified system prompt string |
| `session.idle` | Session goes idle (turn boundary) | enforce-floor, enforce-stop, enforce-multitask, enforce-make | void |
| `event` | Raw lifecycle events (e.g. `session.created`, `session.deleted`) | enforce-stop, watchdog | void |

**Important (2026-07-12 finding):** `text.complete` fires ONLY on agent-
generated text end-stream events — never on tool output. All text in
`text.complete` is from the LLM. Role-based guards there are dead code.

---

## 5. Shared Helpers (`.opencode/lib/shared.ts`)

Every plugin imports the helpers it needs rather than copy-pasting. The
shared library eliminates the duplicated `_isSubagent`, disengage, JSON
state, and heartbeat patterns that were scattered across 14+ plugins before
the E.5 refactor (2026-07-13).

### 5.1 Subagent + Disengage Guards

| Export | Signature | Purpose |
|---|---|---|
| `isSubagent()` | `() => boolean` | True when `process.env.OPENCODE_SUBAGENT === "1"` OR `/tmp/gludd-subagent-${pid}.json` exists. The first thing every hook calls. |
| `isDisengaged(opts?)` | `(opts?: { maxMs?: number }) => boolean` | Reads `/tmp/gludd-watchdog-disengage.json` (and `/tmp/gludd-disengage-next` for single-shot) and returns true when a valid non-expired disengage window is active. `maxMs` clamps forward duration (default 5 min). |

### 5.2 Liveness + Heartbeat

| Export | Signature | Purpose |
|---|---|---|
| `reportAlive(pluginName)` | `(name: string) => void` | Stamps `/tmp/gludd-plugin-alive.json[name]` with `{last_seen, ts, loaded}` so the watchdog can detect dead plugins. |
| `writeHeartbeat(pluginName)` | `(name: string) => void` | Writes `/tmp/gludd-plugin-heartbeat-<name>.json` proving the plugin's `tool.execute.before` fired. |

### 5.3 State File I/O

| Export | Signature | Purpose |
|---|---|---|
| `readJsonFile<T>(path, default)` | `<T>(path: string, def: T) => T` | Safe JSON read — returns `default` on any error (missing, corrupt, permission). Never throws. |
| `writeJsonFile(path, data)` | `(path: string, data: unknown) => void` | Atomic JSON write — writes to `path.tmp.${pid}` then `renameSync`. Survives partial writes from concurrent processes. |

### 5.4 Tool Classification

| Export | Signature | Purpose |
|---|---|---|
| `isDispatchTool(tool)` | `(tool: string) => boolean` | True for `task`, `agent`, `workflow`. Used by floor/streak/multitask plugins. |
| `isReadTool(tool)` | `(tool: string) => boolean` | True for `read`, `grep`, `glob`. Read tools are exempt from grinding blocks. |
| `DISPATCH_TOOLS` / `READ_TOOLS` | `readonly string[]` | Frozen canonical lists. |

### 5.5 Shared Streak State

Cross-call grinding detection shared between `enforce-floor.ts` and
`enforce-stop.ts` so EITHER plugin can catch main-thread grinding (serial
read/edit/bash with no dispatch). The dedup window (500ms) prevents
double-counting when both plugins fire on the same `tool.execute.before`.

| Export | Purpose |
|---|---|
| `readSharedStreak()` | Reads `/tmp/gludd-tool-streak.json`; auto-resets stale state (mtime older than session-start file, age > 60s, or PID mismatch). |
| `writeSharedStreak(s)` | Writes the streak state. |
| `updateSharedStreak(tool, pluginName)` | Increments on non-dispatch tools, resets on dispatch. Returns the updated state. |

### 5.6 Project Root Resolution

Plugin worker processes may have a different `cwd` than the main opencode
process, causing `hasPendingWork()` to fail finding `TASKS.md`/`ratchet.yml`.
`getProjectRoot()` resolves this with cached walk-up logic:

1. `GLUDD_PROJECT_ROOT` env var (if it names an existing directory). The
   explicit directory is authoritative even without project markers.
2. Walk up from `cwd` looking for `TASKS.md` OR (`opencode.json` + `Makefile`).
3. Fall back to `cwd`.

An absent, missing, or non-directory override cannot broaden discovery beyond
`cwd` and its ancestors. There is no developer-specific or machine-specific
checkout fallback. Only the trusted environment override may intentionally
select an unrelated directory; see
[`ENFORCEMENT_PROCESS_ISOLATION.md`](features/ENFORCEMENT_PROCESS_ISOLATION.md#project-ledger-root-isolation)
for the configurability, security, rollout, and rollback contract.

The cache is keyed on `(GLUDD_PROJECT_ROOT, cwd)` so a mid-session change
invalidates the cached resolution. `invalidateProjectRootCache()` forces a
re-resolve.

### 5.7 Session-Start Mtime Guards

When opencode reuses PIDs across restarts, PID-only staleness detection
fails. The mtime guard complements it: if a state file was last modified
before the current session started, it is stale and must be discarded.

| Export | Purpose |
|---|---|
| `getSessionStartMtimeMs()` | mtime of `/tmp/gludd-session-start.json` (0 if absent). |
| `isStateFileMtimeStale(path)` | True when the given state file's mtime precedes the session-start mtime. |

---

## 6. Fail-Open Principle

**Every check returns silently on internal error.** This is the single most
important invariant. The enforcement layer exists to prevent agent misbehavior
— it must NEVER itself become the reason work cannot proceed.

### 6.1 What Fail-Open Looks Like

```typescript
"tool.execute.before": async (input, output) => {
  if (isSubagent()) return
  reportAlive("enforce-foo")
  if (process.env.GLUDD_FOO_ENFORCE === "0") return
  try {
    // ...the actual check...
    if (violation) {
      return { permissionDecision: "deny", message: "..." }
    }
  } catch {
    // Fail-open: never wedge the editor on a plugin error.
    return
  }
}
```

### 6.2 Where Fail-Open Applies

| Site | Behavior on error |
|---|---|
| `loadHotModule` | Returns `defaultImpl` if hot module missing / unparsable / throws |
| `readJsonFile` | Returns the supplied default |
| `writeJsonFile` | Silently skips (permission / disk-full) |
| `isSubagent` | Returns `false` if the marker file can't be stat'd |
| `isDisengaged` | Returns `false` if the disengage file is corrupt |
| `reportAlive` / `writeHeartbeat` | Silently skip — liveness is best-effort |
| `getProjectRoot` | Falls back to `process.cwd()` |
| Every hook body | `try { ... } catch { return }` — deny is impossible from a broken hook |

### 6.3 What Fail-Open Does NOT Mean

Fail-open does NOT mean "make the check advisory." A check that decides a
violation exists MUST still deny — only *errors during the decision* fall
back to allow. Removing the deny path to "stop the noise" is a Guardrail
Integrity Policy violation (see `AGENTS.md`).

---

## 7. Subagent Isolation

The `OPENCODE_SUBAGENT` env-var guard prevents enforcement from firing
inside a delegated context. The orchestrator manages enforcement; the
subagent inherits the plugins but skips every check.

### 7.1 Detection (two layers, checked in order)

1. **Env var (preferred):** `process.env.OPENCODE_SUBAGENT === "1"` — set by
   the opencode framework when spawning a subagent.
2. **File-based fallback:** `/tmp/gludd-subagent-${process.pid}.json` exists.
   This fallback exists because the env var is not guaranteed to be set in
   all opencode configurations.

### 7.2 Known Limitation

If `OPENCODE_SUBAGENT` is not set by the framework AND the file-based
fallback fails (e.g. the marker file wasn't written), enforcement WILL fire
inside subagents. This is a framework-level gap; the file-based fallback is
a workaround, not a fix for the missing env var.

### 7.3 Verification

- `make verify-plugin-manifest` checks every plugin has the subagent guard.
- `make test-hook-runtime` includes tests verifying subagent context skips
  enforcement.
- Hot modules in `/tmp/gludd-hot-enforce-*.js` MUST include the guard at the
  top of every exported hook function. Stale hot modules can bypass compiled-in
  guards — run `make hot-reload-plugins` to rebuild.

---

## 8. State Files (`/tmp/gludd-*.json` Pattern)

All runtime state lives in `/tmp/gludd-*.json` files, NOT in plugin memory.
This makes the state observable, debuggable, and survivable across plugin
re-invocations.

### 8.1 Categories

| Category | Example files | Purpose |
|---|---|---|
| **Disengage** | `/tmp/gludd-watchdog-disengage.json`, `/tmp/gludd-disengage-next`, `/tmp/gludd-disengage-audit.jsonl` | Watchdog / single-shot disengage windows + audit log |
| **Liveness** | `/tmp/gludd-plugin-alive.json`, `/tmp/gludd-plugin-heartbeat-<name>.json` | Plugin alive registry + per-invocation heartbeats |
| **Floor / streak** | `/tmp/gludd-tool-streak.json`, `/tmp/gludd-floor-text-complete-count.json`, `/tmp/gludd-missed-commit-dispatch.json` | Grinding/streak/floor state shared between plugins |
| **Floor override** | `/tmp/gludd-floor-override`, `/tmp/gludd-ceiling-override` | Runtime tuning of floor/ceiling without restart |
| **Session-start** | `/tmp/gludd-session-start.json` | Per-session dispatch counter; PID + mtime for crash recovery |
| **CI cooldown** | `/tmp/gludd-ci-check-state.json` | Last CI check timestamp + push SHA for cooldown enforcement |
| **Task deadlines** | `/tmp/gludd-task-deadlines.json`, `/tmp/gludd-task-stale.json`, `/tmp/gludd-task-killed.json` | Wall-clock deadlines for dispatched subagents |
| **Subagent markers** | `/tmp/gludd-subagent-<pid>.json` | File-based subagent detection fallback |
| **Hot modules** | `/tmp/gludd-hot-<name>.js` | Compiled hot-reload modules (mtime-checked) |
| **Enhancement ratio** | `/tmp/gludd-enhancement-ratio.json` | Per-wave + session-aggregate fix/enhancement ratio |
| **Read-grind** | `/tmp/gludd-read-grind.json` | Serial read-only investigation counter |

### 8.2 Reset + Audit

- `make reload-enforcement` — resets all enforcement state files (used after
  env-var changes or a crash).
- `make crash-recovery` — manually reset state after a stale PID / age-gate.
- `make clean-tmp` — clean `/tmp/gludd-*` files (logs, stale state).
- `make check-disk` — pre-commit check: fails if `/tmp/gludd-*` > 100MB.

The reload target honors `GLUDD_STREAK_FILE`,
`GLUDD_MAINTHREAD_STREAK_FILE`, and `GLUDD_MULTITASK_STATE_FILE`.
This keeps test workers and parallel projects from resetting one another's
live state. A long-running [pytest concurrency report](https://github.com/pytest-dev/pytest/issues/4181)
documents build jobs colliding on a shared temporary path; environment-directed
state paths are the corresponding isolation boundary here.

### 8.3 Atomic Write Pattern

All state writes use the `writeJsonFile()` atomic-rename pattern to prevent
partial-read races when multiple Node processes share a state file:

```typescript
const tmp = `${filePath}.tmp.${process.pid}`
fs.writeFileSync(tmp, JSON.stringify(data), "utf8")
fs.renameSync(tmp, filePath)
```

This also avoids EXDEV on the macOS `/tmp` → `/private/tmp` symlink.

---

## 9. Disable Mechanisms (`GLUDD_*_ENFORCE=0`)

Every blocking plugin exposes an env-var disable knob. Disabling a plugin is
the operator's escape hatch when a misbehaving guardrail is blocking
legitimate work — it is NOT a routine development workflow.

### 9.1 Granular Disable (Per-Plugin)

```bash
GLUDD_FLOOR_ENFORCE=0            # disable enforce-floor.ts only
GLUDD_MULTITASK_FLOOR_ENFORCE=0  # disable enforce-multitask.ts only
GLUDD_TDD_ENFORCE=0              # disable enforce-tdd.ts only
# ...one per plugin (see docs/ENFORCEMENT_PLUGIN_REGISTRY.md for the full list)
```

### 9.2 Bulk Disable (All Enforcement)

- `make disengage-enforcement` — writes the disengage signal to
  `/tmp/gludd-watchdog-disengage.json` with a 5-minute forward window. All
  blocking plugins check this on every invocation.
- `make disengage-next` — single-shot disengage: arms for ONE tool call,
  then auto-rearms. Used for emergency single-operation escapes.
- `OPENCODE_SUBAGENT=1` — completely disables enforcement for the current
  process (used by subagents).

### 9.3 Important Caveats

- **As of 2026-07-15**, disengage only skips *heuristic* checks
  (`COMPLETION_SMELL`, `COMPLETION_WORDS`, QA patterns) in `enforce-stop.ts`.
  The fundamental `hasRealPendingWork()` text-only block is NEVER bypassed —
  any text-only response while pending work exists is always blanked.
- **`enforce-no-suppressions.ts` has NO env-var disable** — lint-suppression
  comments are never legitimate. Tune the allowlist instead.
- **State-file tuning without restart:** the `/tmp/gludd-*` pattern allows
  runtime tuning (floor override, disengage window, cooldown reset) without
  an opencode restart. Plugin source changes still require restart OR
  `make hot-reload-plugins` for proxy-pattern plugins.

---

## 10. Plugin Interaction Diagram

```
                       ┌──────────────────────────────────────┐
                       │       opencode agent runtime         │
                       │   (tool call, text emission, etc.)   │
                       └──────────────────┬───────────────────┘
                                          │
                 ┌────────────────────────┼────────────────────────┐
                 │                        │                        │
                 ▼                        ▼                        ▼
    ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
    │ tool.execute.before │  │  text.complete      │  │ system.transform    │
    │ (27 enforce-* +     │  │  (8 plugins)        │  │ (2 plugins)         │
    │  watchdog)          │  │                     │  │                     │
    └──────────┬──────────┘  └──────────┬──────────┘  └──────────┬──────────┘
               │                        │                        │
               └────────────────────────┼────────────────────────┘
                                        │
                                        ▼
            ┌──────────────────────────────────────────────────────┐
            │   Each hook calls (in this order, fail-open at each): │
            │                                                      │
            │   1. isSubagent()            → return if true         │
            │   2. reportAlive(name)       → heartbeat              │
            │   3. env-var disable check   → return if "0"          │
            │   4. isDisengaged()          → return if true         │
            │   5. loadHotModule(name, defaultImpl)                 │
            │      └─ mtime-check /tmp/gludd-hot-<name>.js          │
            │      └─ fail-open to defaultImpl on any error         │
            │   6. invoke resolved hook fn                          │
            │      └─ try { ... } catch { return undefined }        │
            │                                                      │
            │   Return: undefined (allow) | {deny, message} (block) │
            └──────────────────────────────────────────────────────┘
                                        │
                                        ▼
            ┌──────────────────────────────────────────────────────┐
            │              Shared state layer                       │
            │                                                      │
            │   /tmp/gludd-plugin-alive.json      ← reportAlive()   │
            │   /tmp/gludd-plugin-heartbeat-*.json← writeHeartbeat  │
            │   /tmp/gludd-tool-streak.json       ← updateStreak    │
            │   /tmp/gludd-watchdog-disengage.json← isDisengaged    │
            │   /tmp/gludd-session-start.json     ← crash recovery  │
            │   /tmp/gludd-hot-<name>.js          ← loadHotModule   │
            │                                                      │
            │   All writes atomic (tmp+rename), all reads fail-open │
            └──────────────────────────────────────────────────────┘
                                        │
                                        ▼
            ┌──────────────────────────────────────────────────────┐
            │          Background observers (decoupled)             │
            │                                                      │
            │   scripts/agent_watchdog.py  → reads alive.json,      │
            │                                resets streak every 60s │
            │   scripts/task_watchdog.py   → reads deadlines.json,  │
            │                                kills over-budget tasks │
            │   make watchdog-auto         → starts the daemon      │
            └──────────────────────────────────────────────────────┘
```

### 10.1 Hook Firing Order

Plugins fire in `opencode.json` registration order. Earlier-listed plugins
win on ties (e.g. if two plugins both want to deny, the first one's deny
message is the one the agent sees). The current ordering places:

1. `enforce-session-start.ts` — first; gates everything until dispatch threshold met.
2. `enforce-make.ts` — early; bash policy is foundational.
3. `enforce-floor.ts` / `enforce-delegate.ts` / `enforce-multitask.ts` — the multitasking trio.
4. `enforce-stop.ts` — late; only fires on text emission / idle.
5. The rest in dependency order.
6. `watchdog.ts` — last; observation only.

### 10.2 Cross-Plugin Coordination

Plugins do NOT call each other directly (the 2026-07-24 incident proved how
broken that gets). They coordinate exclusively through shared state files:

- `enforce-floor.ts` and `enforce-stop.ts` both read/write
  `/tmp/gludd-tool-streak.json` via `updateSharedStreak()`. Either plugin can
  catch grinding — the 500ms dedup window prevents double-counting.
- `enforce-deadline.ts` writes `/tmp/gludd-task-deadlines.json`;
  `scripts/task_watchdog.py` reads it and kills over-budget task processes.
- `enforce-session-start.ts` writes `/tmp/gludd-session-start.json`; the
  shared streak reader uses its mtime for staleness detection.

---

## 11. References

- `opencode.json` — plugin registration (source of truth for active plugins).
- `.opencode/lib/shared.ts` — shared helper library.
- `.opencode/lib/hot_reload.ts` — hot-reload proxy utility.
- `.opencode/plugin/*.ts` — individual plugin source.
- `docs/ENFORCEMENT_PLUGIN_REGISTRY.md` — per-plugin operator reference.
- `docs/ENFORCEMENT_PLUGINS.md` — historical deep-dive.
- `AGENTS.md` — agent-visible policy each plugin enforces.
- `tests/unit/test_enforcement_registry.py` — pins registry completeness.
- `tests/unit/test_enforcement_architecture_doc.py` — pins this document.
- `make list-plugins` — current roster with hooks and block conditions.
- `make verify-enforcement` — health check.
- `make test-hook-runtime` — functional hook runtime tests.
