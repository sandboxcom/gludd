import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { isSubagent, reportAlive } from "../lib/shared.ts"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"

// enforce-session-start.ts — guarantees the FIRST actions of every session are:
//   1. LOCATE work: read TASKS.md, BUGS.md, config/ratchet.yml, SESSION.md
//   2. FAN OUT: dispatch >= MIN_DISPATCHES parallel task/agent subagents on
//      disjoint work BEFORE any inline mutation or terminal response.
//
// Prevents two failure modes:
//   (a) "Q&A-style session start" — agent replies with prose instead of
//       resuming work. Fixed by the SESSION START PROTOCOL banner injection.
//   (b) "Grind-inline start" — agent does the work serially on the main
//       thread with 0 subagents live, looking hung. Fixed by the
//       dispatch-count gate.
//
// Three-layer guardrail:
//   - Prompt: AGENTS.md "Session Start Protocol" section.
//   - Plugin: this file (system.transform + tool.execute.before).
//   - Test: tests/unit/test_session_start_protocol.py.
//
// FAIL-OPEN: every hook is wrapped in try/catch that returns the original
// output/undefined. A plugin bug never wedges the session.
//
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts. Hook functions
// check /tmp/gludd-hot-enforce-session-start.js on every invocation. Run
// `make hot-reload-plugins` after editing this file.

// --- Config -----------------------------------------------------------------

// Minimum parallel dispatches before inline mutations are allowed in a fresh
// session. Hard-coded to 10 (the AGENTS.md floor) so the dispatch wave is
// guaranteed to be a full-width fan-out. Override via
// GLUDD_SESSION_START_MIN_DISPATCHES.
const MIN_DISPATCHES = parseInt(
  process.env.GLUDD_SESSION_START_MIN_DISPATCHES || "10",
  10,
)
const FLOOR = parseInt(process.env.CLAUDE_AGENT_FLOOR || "10", 10)
const EFFECTIVE_MIN = Math.max(MIN_DISPATCHES, Math.min(FLOOR, 10))

// Hard-deny mode (mirrors GLUDD_FLOOR_ENFORCE / GLUDD_NO_WAIT_ENFORCE).
// Default is ON (hard deny on premature mutations). Set
// GLUDD_SESSION_START_ENFORCE=0 to fall back to advisory (directive-only)
// mode — useful for Q&A-only sessions.
const ENFORCE = process.env.GLUDD_SESSION_START_ENFORCE !== "0"

// Per-cwd state file. Overridable for tests.
const STATE_FILE =
  process.env.GLUDD_SESSION_STATE || "/tmp/gludd-session-start.json"

// A session is "fresh" for this many seconds after the first tool call. After
// that the gate turns off (the agent may have legitimately onboarded).
const FRESH_SECS = parseInt(
  process.env.GLUDD_SESSION_START_FRESH_SECS || "600",
  10,
)

// Gap 8: TASKS.md staleness check — if the session has been active for >5 min
// and TASKS.md hasn't been read, inject a nag directive.  Configurable via
// GLUDD_TASKS_STALE_MINUTES (default 5).
const TASKS_STALE_MINUTES = parseInt(
  process.env.GLUDD_TASKS_STALE_MINUTES || "5",
  10,
)

let _lastTasksReadMtime = 0

// Time-based gate constants. After DISPATCH_NOW_SECS with 0 dispatches, a
// "DISPATCH NOW" warning is emitted. After HARD_DENY_SECS with 0 dispatches,
// non-dispatch, non-read tools are denied. Both gates reset on first dispatch.
const DISPATCH_NOW_SECS = parseInt(
  process.env.GLUDD_SESSION_START_DISPATCH_NOW_SECS || "60", 10)
const HARD_DENY_SECS = parseInt(
  process.env.GLUDD_SESSION_START_HARD_DENY_SECS || "120", 10)

// Throttle warning emission (at most once per 30s) to avoid spam.
let _lastTimeGateWarningTs = 0

// Per-module-instance latch (Fix B). Once the primed condition has been
// observed — `readsDone && dispatches >= EFFECTIVE_MIN` — this instance
// skips ALL state-file I/O on subsequent tool calls. That eliminates the
// race window for every call after the orchestrator's session-start duty
// is complete (including every subagent's `make` call).
//
// Semantics:
//   null  = not yet loaded from state file (lazy init on first tool.execute.before)
//   false = loaded, primed condition not yet met — keep tracking
//   true  = primed — gate is latched open for this instance forever
let sessionPrimed: boolean | null = null

const TASK_FILES = ["TASKS.md", "BUGS.md", "config/ratchet.yml", "SESSION.md"]

// --- System prompt banner ---------------------------------------------------

function buildSessionDirective(): string {
  const nowSecs = parseInt(process.env.GLUDD_SESSION_START_DISPATCH_NOW_SECS || "60", 10)
  const denySecs = parseInt(process.env.GLUDD_SESSION_START_HARD_DENY_SECS || "120", 10)
  return [
    "================ SESSION START PROTOCOL ================",
    "The FIRST actions of this session, in strict order:",
    "  STEP 1 — LOCATE work: in ONE tool-call message, read TASKS.md,",
    "           BUGS.md, config/ratchet.yml, SESSION.md. Never serial.",
    `  STEP 2 — FAN OUT: dispatch >= ${EFFECTIVE_MIN} parallel task/agent`,
    "           subagents on disjoint work BEFORE any inline mutation, status",
    "           report, or terminal response. Reads are allowed; serial work",
    "           is not.",
    "DO NOT WRITE any prose between session start and the first dispatch wave.",
    "Do not answer the user's prompt first and dispatch second — dispatch first.",
    "No prose, no summaries, no status reports, no planning before the wave.",
    `⏱  TIME GATE: dispatch within ${nowSecs}s — warning emitted.`,
    `   After ${denySecs}s with 0 dispatches: non-dispatch mutations DENIED.`,
    "   Both gates reset on first successful dispatch.",
    "Why: a session that boots and then grinds inline (0 subagents live) looks",
    "hung to the user. The fix is structural — locate work, then fan out.",
    "Enforced by .opencode/plugin/enforce-session-start.ts (hard-deny by",
    "default). Set GLUDD_SESSION_START_ENFORCE=0 for advisory mode.",
    "========================================================",
  ].join("\n")
}

// --- State helpers ----------------------------------------------------------

interface SessionState {
  started_at: number
  readsDone: boolean
  dispatches: number
  timeGateReset: boolean
}

function loadState(): SessionState {
  try {
    if (!fs.existsSync(STATE_FILE)) {
      const initial: SessionState = {
        started_at: Date.now(),
        readsDone: false,
        dispatches: 0,
        timeGateReset: false,
      }
      fs.writeFileSync(STATE_FILE, JSON.stringify(initial))
      return initial
    }
    const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
    return {
      started_at: Number(raw.started_at) || Date.now(),
      readsDone: Boolean(raw.readsDone),
      dispatches: Number(raw.dispatches) || 0,
      timeGateReset: Boolean(raw.timeGateReset),
    }
  } catch {
    // Corrupt state file → fail-open: return a primed state so
    // the gate allows all tools through. Never wedge the session
    // on a bit-flipped JSON file.
    return {
      started_at: Date.now(), readsDone: true,
      dispatches: EFFECTIVE_MIN, timeGateReset: true,
    }
  }
}

function saveState(state: SessionState): void {
  // Fix A: atomic write via temp-file + rename. Each writer uses a
  // PID-unique temp path so concurrent writers don't clobber each other's
  // temp file, and the final rename is atomic on POSIX (no torn reads).
  // Combined with the per-instance latch (Fix B), this eliminates the
  // lost-update race for the dispatches counter.
  try {
    const tmp = `${STATE_FILE}.tmp.${process.pid}`
    fs.writeFileSync(tmp, JSON.stringify(state))
    fs.renameSync(tmp, STATE_FILE)
  } catch {
    // fail open
  }
}

function sessionIsFresh(s: SessionState): boolean {
  return (Date.now() - s.started_at) / 1000 < FRESH_SECS
}

// Returns true once the primed condition has been met AND latches the
// module-level `sessionPrimed` flag so future calls skip state I/O.
function updatePrimedLatch(state: SessionState): boolean {
  if (sessionPrimed === true) return true
  if (state.readsDone && state.dispatches >= EFFECTIVE_MIN) {
    sessionPrimed = true
    return true
  }
  if (sessionPrimed === null) sessionPrimed = false
  return false
}

// --- Tool classification ----------------------------------------------------

function isDispatchTool(tool: string): boolean {
  return tool === "task" || tool === "agent" || tool === "workflow"
}

function isReadTool(tool: string): boolean {
  return tool === "read" || tool === "glob" || tool === "grep"
}

const READ_ONLY_MAKE_TARGETS: ReadonlySet<string> = new Set([
  "git-status", "git-diff", "git-log", "git-staged", "git-show",
  "verify-state", "verify-remote",
  "check-node-v26-compat",
  "ci-verdict", "ci-verdict-safe", "ci-cooldown-status",
  "gate-status", "gate-status-check", "gate-logs",
  "disk", "disk-check", "disk-guard",
  "agent-worktree-list",
  "playbook-list",
  "collection-roles", "collection-modules",
  "test-count", "test-failures",
  "audit-messages",
  "version", "help",
  "verify-plugin-manifest", "test-hook-runtime",
  "check-disk",
  "development-status",
  "repo-status", "repo-diff", "repo-log", "repo-staged",
])

function isReadOnlyMakeTarget(tool: string, input: unknown): boolean {
  if (tool !== "bash") return false
  const inp = input as Record<string, unknown> | null
  const args = inp?.args as Record<string, unknown> | null | undefined
  const cmd: string = (args?.command as string) ?? (inp?.command as string) ?? ""
  const m = cmd.match(/^make\s+(\S+)/)
  if (!m) return false
  return READ_ONLY_MAKE_TARGETS.has(m[1])
}

function isTaskFileRead(tool: string, input: unknown): boolean {
  if (!isReadTool(tool)) return false
  try {
    const inp = input as Record<string, unknown> | null
    const args = inp?.args as Record<string, unknown> | null | undefined
    const filePath = (args?.filePath as string) ?? ""
    if (filePath && TASK_FILES.some(f => filePath.toLowerCase().includes(f.toLowerCase()))) return true
    const blob = JSON.stringify(input ?? {}).toLowerCase()
    return TASK_FILES.some(f => blob.includes(f.toLowerCase()))
  } catch {
    return false
  }
}

// --- Heartbeat helper -------------------------------------------------------

function _writeHeartbeat(): void {
  try {
    const hb = JSON.stringify({ plugin: "enforce-session-start", ts: Date.now(), pid: process.pid })
    fs.writeFileSync("/tmp/gludd-plugin-heartbeat-enforce-session-start.json", hb)
  } catch { /* fail-open */ }
}

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "experimental.chat.system.transform": async (
    _input: unknown,
    output: unknown,
  ) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return output
    try {
      const state = loadState()
      if (typeof output === "string") {
        const sessionAgeMs = Date.now() - state.started_at
        const tasksStaleMs = TASKS_STALE_MINUTES * 60_000
        const needsTasksNag = sessionAgeMs > tasksStaleMs && _lastTasksReadMtime === 0
        const tasksNagText = needsTasksNag
          ? [
              "",
              "══════════════════════════════════════════════════════════════",
              "⛔  RULE 7: Read TASKS.md for current work — STALE SESSION",
              "══════════════════════════════════════════════════════════════",
              "",
              `Session active for ${Math.round(sessionAgeMs / 60_000)} minutes.`,
              "TASKS.md has NOT been read recently.",
              "",
              "Before generating any status claim or completion report:",
              "  1. Read TASKS.md — what items are unchecked?",
              "  2. Read BUGS.md — are there open incidents?",
              "  3. Update them before claiming anything is done.",
              "",
              "See AGENTS.md Mechanical Contract rule 7.",
              "",
            ].join("\n")
          : ""
        const directive = tasksNagText + buildSessionDirective()
        return directive + "\n\n" + output
      }
      return output
    } catch {
      return output
    }
  },

  "tool.execute.before": async (
    input: { tool?: string } & Record<string, unknown>,
    _output: unknown,
  ) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return
    reportAlive("enforce-session-start")
    _writeHeartbeat()
    let denyMessage: string | null = null
    try {
      const tool = String((input as { tool?: string }).tool ?? "")

      if (sessionPrimed === true) return

      const state = loadState()

      if (isDispatchTool(tool)) {
        state.dispatches += 1
        if (!state.timeGateReset) {
          state.timeGateReset = true
        }
        saveState(state)
        updatePrimedLatch(state)
        return
      }

      if (isTaskFileRead(tool, input)) {
        if (!state.readsDone) {
          state.readsDone = true
          saveState(state)
        }
        try {
          const blob = JSON.stringify(input ?? {}).toLowerCase()
          if (blob.includes("tasks.md")) {
            const tasksPath = path.join(process.cwd(), "TASKS.md")
            _lastTasksReadMtime = fs.statSync(tasksPath).mtimeMs
          }
        } catch { /* ignore */ }
        updatePrimedLatch(state)
        return
      }

      if (isReadTool(tool)) {
        updatePrimedLatch(state)
        return
      }

      if (isReadOnlyMakeTarget(tool, input)) {
        updatePrimedLatch(state)
        return
      }

      if (!state.timeGateReset && state.dispatches === 0) {
        const elapsedSecs = (Date.now() - state.started_at) / 1000
        if (elapsedSecs >= HARD_DENY_SECS) {
          const msg = (
            `⛔ TIME GATE: ${Math.round(elapsedSecs)}s elapsed with 0 ` +
            `dispatches. Non-dispatch mutations DENIED. Dispatch >= ` +
            `${EFFECTIVE_MIN} parallel subagents NOW.`
          )
          console.warn(msg)
          if (ENFORCE) {
            denyMessage = msg
          }
        } else if (elapsedSecs >= DISPATCH_NOW_SECS) {
          const now = Date.now()
          if (now - _lastTimeGateWarningTs > 30_000) {
            _lastTimeGateWarningTs = now
            console.warn(
              `⛔ DISPATCH NOW: ${Math.round(elapsedSecs)}s elapsed, ` +
              `0 dispatches. Hard deny at ${HARD_DENY_SECS}s. ` +
              `Dispatch >= ${EFFECTIVE_MIN} parallel subagents immediately.`
            )
          }
        }
      }

      if (updatePrimedLatch(state)) return

      if (sessionIsFresh(state)) {
        const msg = [
          `[SESSION START PROTOCOL] readsDone=${state.readsDone},`,
          `${state.dispatches}/${EFFECTIVE_MIN} dispatches so far.`,
          `Locate work (TASKS.md, BUGS.md, config/ratchet.yml, SESSION.md)`,
          `then dispatch >= ${EFFECTIVE_MIN} parallel task/agent subagents`,
          `BEFORE inline mutations. Reads and dispatches are allowed; this`,
          `${tool} call is premature.`,
        ].join(" ")
        console.warn(msg)
        if (ENFORCE) {
          denyMessage = (
            "SESSION START PROTOCOL: readsDone=" + state.readsDone +
            ", " + state.dispatches + "/" + EFFECTIVE_MIN + " dispatches. " +
            "Locate work then dispatch >= " + EFFECTIVE_MIN + " parallel " +
            "subagents before doing inline mutations. Reads and dispatches " +
            "are allowed. Set GLUDD_SESSION_START_ENFORCE=0 to make this " +
            "advisory."
          )
        }
      }
    } catch {
      // fail open — never wedge the session on a plugin bug
    }
    if (denyMessage) throw new Error(denyMessage)
  },
}

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  // LOADED self-check: proves opencode invoked the factory
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-session-start ` +
      `tool.execute.before+experimental.chat.system.transform ` +
      `pid=${process.pid}\n`,
      "utf8",
    )
  } catch { /* fail-open */ }
  return {
    "experimental.chat.system.transform": async (
      _input: unknown,
      output: unknown,
    ) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return output
      const impl = loadHotModule("enforce-session-start", defaultImpl)
      const fn = impl["experimental.chat.system.transform"] || impl["system.transform"]
      return fn ? await fn(_input, output) : output
    },

    "tool.execute.before": async (
      input: { tool?: string } & Record<string, unknown>,
      _output: unknown,
    ) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return
      const impl = loadHotModule("enforce-session-start", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, _output) : undefined
    },
  }
}) satisfies Plugin
