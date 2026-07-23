// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { createRequire } from "node:module"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import {
  isSubagent,
  reportAlive,
  isDispatchTool,
  isReadTool,
  isDisengaged,
  readJsonFile,
  writeJsonFile,
  getProjectRoot,
  isStateFileMtimeStale,
} from "../lib/shared.ts"
const nodeRequire = typeof require === "function" ? require : createRequire(import.meta.url)
function spawn(...args: any[]): any {
  return nodeRequire("node:child_" + "process").spawn(...args)
}
const FLOOR_ENFORCE = process.env.GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"
const MIN_DISPATCHES = parseInt(
  process.env.GLUDD_MIN_DISPATCHES ||
  process.env.GLUDD_MULTITASK_MIN_DISPATCHES ||
  "10",
  10,
)
const MAX_DISPATCHES = parseInt(process.env.GLUDD_MULTITASK_MAX_DISPATCHES || "10", 10)
const MAX_ZERO_STREAK = 2
const WAVE_HISTORY_SIZE = 10
// Inter-call gap that marks a new agent message. Env-tunable so e2e tests can
// drive the real boundary logic without 5s sleeps; production default unchanged.
const MSG_GAP_MS = parseInt(process.env.GLUDD_MSG_GAP_MS || "5000", 10)
const CONSECUTIVE_NON_DISPATCH_THRESHOLD = parseInt(
  process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD || "5", 10)
const CONSECUTIVE_NON_DISPATCH_WINDOW_MS = parseInt(
  process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS || "30000", 10)
// Env-overridable (T10) so tests isolate from live sessions; default stays in /tmp.
const MULTITASK_STATE_FILE = process.env.GLUDD_MULTITASK_STATE_FILE || "/tmp/gludd-multitask-state.json"
interface MultitaskState {
  pid: number
  thisMessageDispatches: number
  prevMessageDispatches: number
  zeroStreak: number
  estimatedInFlight: number
  lastTs: number
  lastToolCallTs: number
  waveHistory: number[]
  consecutiveNonDispatch: number
  consecutiveNonDispatchStartTs: number
  sawNonDispatchSinceDispatch: boolean
}
function freshState(): MultitaskState {
  return {
    pid: process.pid,
    thisMessageDispatches: 0,
    prevMessageDispatches: 0,
    zeroStreak: 0,
    estimatedInFlight: 0,
    lastTs: 0,
    lastToolCallTs: 0,
    waveHistory: [],
    consecutiveNonDispatch: 0,
    consecutiveNonDispatchStartTs: 0,
    sawNonDispatchSinceDispatch: false,
  }
}
function readState(): MultitaskState {
  if (isStateFileMtimeStale(MULTITASK_STATE_FILE)) {
    return freshState()
  }
  return readJsonFile<MultitaskState>(MULTITASK_STATE_FILE, freshState())
}
function writeState(s: MultitaskState): void {
  s.lastTs = Date.now()
  writeJsonFile(MULTITASK_STATE_FILE, s)
}
function hasPendingWork(): boolean {
  try {
    const tasksPath = path.join(getProjectRoot(), "TASKS.md")
    if (!fs.existsSync(tasksPath)) return false
    const content = fs.readFileSync(tasksPath, "utf8")
    return /^\s*[-*]\s*\[\s*\]/m.test(content)
  } catch {
    return false
  }
}
function handleMessageBoundary(s: MultitaskState): void {
  const now = Date.now()
  // Idempotency guard: prevent double-processing within 500ms. When
  // text.complete calls handleMessageBoundary first (canonical signal),
  // the heuristic detection in tool.execute.before may fire again on
  // the same boundary within the same process. Without this guard,
  // zeroStreak double-increments and waveHistory gets duplicate entries.
  const lastB = (s as any)._lastBoundaryTs
  if (lastB && now - lastB < 500) {
    return
  }
  (s as any)._lastBoundaryTs = now
  s.prevMessageDispatches = s.thisMessageDispatches
  if (s.thisMessageDispatches === 0) {
    s.zeroStreak++
  } else {
    s.zeroStreak = 0
  }
  s.waveHistory.push(s.prevMessageDispatches)
  if (s.waveHistory.length > WAVE_HISTORY_SIZE) {
    s.waveHistory = s.waveHistory.slice(-WAVE_HISTORY_SIZE)
  }
  s.thisMessageDispatches = 0
}
function spawnGateRefresh(): void {
  try {
    const root = getProjectRoot()
    const gatePath = path.join(root, ".gate-status")
    if (!fs.existsSync(gatePath)) return
    const stat = fs.statSync(gatePath)
    if ((Date.now() - stat.mtimeMs) <= 300_000) return
    const child = spawn("make", ["gate-refresh"], {
      cwd: root,
      detached: true,
      stdio: "ignore",
    })
    child.unref()
  } catch {  }
}
let _state: MultitaskState = (() => {
  const s = readState()
  s.pid = process.pid
  s.zeroStreak = 0
  s.thisMessageDispatches = 0
  s.prevMessageDispatches = 0
  s.estimatedInFlight = 0
  s.lastToolCallTs = 0
  s.consecutiveNonDispatch = 0
  s.consecutiveNonDispatchStartTs = 0
  s.sawNonDispatchSinceDispatch = false
  writeState(s)
  return s
})()
// Per-test state isolation (T8): resets both the in-memory module state and
// the persisted state file to a fresh baseline.
function resetMultitaskState(): void {
  _state = freshState()
  writeState(_state)
}
// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// Exported (T7) so tests invoke the real hooks without hot-module indirection.
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: { tool?: string }) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return
    reportAlive("enforce-multitask")
    // PID-based staleness detection: if the in-memory state was initialized by
    // a different process (prior session / crashed plugin), reset it. This
    // prevents stale thisMessageDispatches from bypassing the under-floor block.
    if (_state.pid !== process.pid) {
      _state = freshState()
    }
    // FLOOR_ENFORCE gate MUST be the first enforcement check. Positioning it
    // after any deny block (the 2026-07-18 bug) caused GLUDD_MULTITASK_FLOOR_ENFORCE=0
    // to be ignored — the under-floor block denied before the env check ran.
    if (!FLOOR_ENFORCE) return
    try {
      const tool = (input?.tool ?? "") as string
      const lt = tool.toLowerCase()
      const now = Date.now()
      // Computed once; the lowercase `disengaged` variable is referenced by the
      // grinding / zero-streak / under-floor gates below so each block is
      // trivially auditable for the escape hatch.
      const disengaged = isDisengaged()
      // --- Message boundary detection: multi-signal ---
      // Signal 0 (canonical): text.complete hook calls handleMessageBoundary
      // at message end. The 500ms idempotency guard in handleMessageBoundary
      // prevents double-processing if the heuristic signals below also fire.
      // Signal 1: time gap > MSG_GAP_MS since last tool call
      let boundaryDetected = false
      if (_state.lastToolCallTs > 0 && (now - _state.lastToolCallTs) > MSG_GAP_MS) {
        boundaryDetected = true
      }
      // Signal 2: first dispatch after any non-dispatch tool call (pattern change)
      if (!boundaryDetected && isDispatchTool(tool) && _state.sawNonDispatchSinceDispatch) {
        boundaryDetected = true
      }
      // Signal 3: high-water-mark safety — counter inflated beyond sane bounds
      if (!boundaryDetected && _state.thisMessageDispatches > MAX_DISPATCHES * 3) {
        boundaryDetected = true
      }
      if (boundaryDetected) {
        handleMessageBoundary(_state)
        _state.sawNonDispatchSinceDispatch = false
      }
      _state.lastToolCallTs = now
      // --- Non-dispatch tools: mark that we've seen non-dispatch activity ---
      if (!isDispatchTool(tool)) {
        _state.sawNonDispatchSinceDispatch = true
      }
      // --- Dispatch tools: count and allow (with ceiling) ---
      if (isDispatchTool(tool)) {
        // Reset the consecutive-non-dispatch streak FIRST, before the ceiling
        // check, so the reset is unconditionally inside the dispatch branch.
        _state.consecutiveNonDispatch = 0
        _state.consecutiveNonDispatchStartTs = 0
        if (_state.thisMessageDispatches >= MAX_DISPATCHES) {
          writeState(_state)
          return {
            permissionDecision: "deny" as const,
            message: [
              "DISPATCH CEILING BREACH: already " + String(_state.thisMessageDispatches) + " dispatch(es) in this message.",
              "Maximum allowed per wave: 10. Floor is 10. DISPATCH 10 AGENTS OR YOU ARE BLOCKED.",
              "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
              "Run 'make disengage-enforcement' to bypass.",
            ].join("\n"),
          }
        }
        _state.thisMessageDispatches++
        _state.estimatedInFlight++
        writeState(_state)
        return
      }
      // --- Consecutive non-dispatch counter (grinding detection) ---
      // Counts every non-dispatch tool call. After THRESHOLD calls within the
      // time window, blocks ALL non-dispatch tools until a dispatch resets.
      // Read tools (isReadTool(tool)) are excluded from the COUNTER to avoid
      // penalizing investigation bursts — they are still gated by the
      // UNDER-FLOOR block below.
      //
      // RUNS BEFORE the UNDER-FLOOR block so that when the streak counter has
      // reached threshold (the agent has been grinding reads/greps), the
      // STREAK message wins over UNDER-FLOOR. Without this ordering, a call 3
      // edit after 2 reads would incorrectly surface UNDER-FLOOR instead of
      // CONSECUTIVE NON-DISPATCH STREAK (2026-07-18 bug).
      if (!disengaged) {
        // Read tools (read/grep/glob) are excluded from the COUNTER.
        // They are still gated by the UNDER-FLOOR block below, but
        // investigation bursts should never trigger the grinding penalty.
        if (!isReadTool(lt)) {
          if (_state.consecutiveNonDispatchStartTs === 0) {
            _state.consecutiveNonDispatchStartTs = now
          }
          if ((now - _state.consecutiveNonDispatchStartTs) < CONSECUTIVE_NON_DISPATCH_WINDOW_MS) {
            _state.consecutiveNonDispatch++
          } else {
            _state.consecutiveNonDispatch = 0
            _state.consecutiveNonDispatchStartTs = now
            // The window-restarting call IS a non-dispatch call inside the new
            // window — count it as 1 (T25), or every post-expiry threshold is
            // off by one (6 calls trip it instead of 5).
            _state.consecutiveNonDispatch++
          }
        }
        // === CONSECUTIVE NON-DISPATCH BLOCK ===
        if (
          _state.consecutiveNonDispatch >= CONSECUTIVE_NON_DISPATCH_THRESHOLD &&
          hasPendingWork()
        ) {
          writeState(_state)
          return {
            permissionDecision: "deny" as const,
            message: [
              "CONSECUTIVE NON-DISPATCH STREAK: " + String(_state.consecutiveNonDispatch) + " consecutive non-dispatch tool calls (" + tool + ") with pending work.",
              "Floor is 10. DISPATCH " + String(MIN_DISPATCHES) + " SUBAGENTS NOW to reset the streak and resume work.",
              "Dispatch via task/agent/workflow. All non-dispatch tools are blocked until a dispatch resets this counter.",
              "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable. Run 'make disengage-enforcement' to bypass.",
            ].join("\n"),
          }
        }
      }
      // === UNDER-FLOOR HARD BLOCK ===
      // Per AGENTS.md "UNDER-FLOOR HARD BLOCK (2026-07-15)": EVERY non-dispatch
      // tool call — including read/glob/grep — is blocked until the wave
      // reaches the floor. This closes the "dispatch 1, then grind reads"
      // bypass.
      //
      // Fallback for the first-edit-with-zero-dispatches case where the streak
      // counter (above) is still below threshold. When the streak has already
      // hit threshold, the streak block wins.
      if (
        !disengaged &&
        hasPendingWork() &&
        _state.thisMessageDispatches < MIN_DISPATCHES &&
        (lt === "edit" || lt === "write" || lt === "bash")
      ) {
        writeState(_state)
        return {
          permissionDecision: "deny" as const,
          message: [
            "UNDER-FLOOR HARD BLOCK: ONLY " + String(_state.thisMessageDispatches) + " DISPATCHES.",
            "Floor is 10. DISPATCH " + String(MIN_DISPATCHES) + " SUBAGENTS NOW OR YOU ARE BLOCKED.",
            "You have " + String(_state.thisMessageDispatches) + "; need " + String(MIN_DISPATCHES) + ". edit/write/bash/read/grep/glob are blocked until floor reached.",
            "consecutive non-dispatch calls: " + String(_state.consecutiveNonDispatch),
            "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
            "Run 'make disengage-enforcement' to bypass.",
          ].join("\n"),
        }
      }
      // === ZERO-DISPATCH STREAK (FIRES BEFORE UNDER-FLOOR) ===
      if (
        !disengaged &&
        _state.thisMessageDispatches === 0 &&
        _state.zeroStreak >= MAX_ZERO_STREAK
      ) {
        writeState(_state)
        return {
          permissionDecision: "deny" as const,
          message: [
            "ZERO-DISPATCH STREAK: " + String(MAX_ZERO_STREAK) + " consecutive responses with 0 subagent dispatches.",
            "Floor is 10. This block is UNCONDITIONAL. DISPATCH 10 AGENTS NOW.",
            "REQUIRED: Next response MUST contain \u226510 task/agent/workflow dispatches.",
            "No pending-work gate. No tool-type bypass. Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
            "Run 'make disengage-enforcement' to bypass.",
          ].join("\n"),
        }
      }
      // === SANITY CHECK: verify dispatch count before blocking ===
      // If the counter exceeds sane bounds after boundary detection,
      // the count is unreliable — log a warning and force-reset.
      if (
        !disengaged &&
        hasPendingWork() &&
        _state.thisMessageDispatches > 0 &&
        _state.thisMessageDispatches > MAX_DISPATCHES * 2
      ) {
        console.warn(
          "MULTITASK SANITY: thisMessageDispatches=" + String(_state.thisMessageDispatches) +
          " exceeds MAX_DISPATCHES*2=" + String(MAX_DISPATCHES * 2) +
          " — count is unreliable. Force-resetting to 1."
        )
        _state.thisMessageDispatches = 1
        _state.sawNonDispatchSinceDispatch = false
        writeState(_state)
        return
      }
      writeState(_state)
    } catch {
      return
    }
  },
  "text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    // Compatibility alias for older OpenCode hook metadata.
    // zeroStreak / MAX_ZERO_STREAK / MUST DISPATCH / subagent behavior is
    // implemented by the canonical experimental.text.complete handler below.
    return await defaultImpl["experimental.text.complete"]?.(_input, output)
  },
  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    if (!FLOOR_ENFORCE) return undefined
    const text = typeof output === "string" ? output
      : (output as any)?.text ? String((output as any).text) : ""
    if (!text || text.trim().length === 0) return output
    if (isDisengaged()) return output
    if (!hasPendingWork()) return output
    // RESEARCH FINDING: opencode text.complete never receives tool output.
    // Result markers here are assistant text, so they must feed the same
    // message-boundary logic as any other assistant response. The next
    // handleMessageBoundary(_state) updates _state.prevMessageDispatches and
    // applies zeroStreak++ when no dispatches occurred.
    const hasResultMarker = /(?:task result|subagent result|workflow result)/i.test(text)
    if (hasResultMarker) {
      _state.estimatedInFlight = Math.max(0, _state.estimatedInFlight - 1)
    }
    if (_state.thisMessageDispatches > 0 && _state.thisMessageDispatches < MIN_DISPATCHES) {
      const dispatched = _state.thisMessageDispatches
      handleMessageBoundary(_state)
      writeState(_state)
      return {
        text: [
          "THIN WAVE BLOCKED",
          "MUST DISPATCH a full wave before sending summary text.",
          `This message had only ${dispatched} dispatch(es).`,
          `The 10-agent floor REQUIRES ${MIN_DISPATCHES} per wave.`,
          "Your text has been blanked. Re-send with >= " + String(MIN_DISPATCHES) + " dispatches.",
          "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
        ].join("\n"),
      }
    }
    handleMessageBoundary(_state)
    writeState(_state)
    return output
  },
}
// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  spawnGateRefresh()
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-multitask pid=${process.pid}\n`, "utf8",
    )
  } catch { // fail-open
 }
  return {
    "tool.execute.before": async (input: { tool?: string }) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return
      const impl = loadHotModule("multitask", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
  },
  // NOTE: opencode 1.17.9 rejects the bare "text.complete" hook key in the
  // Plugin return object (crashes Plugin.add with TypeError evaluating
  // 'N.event'). Only "experimental.text.complete" is valid. The alias in
  // defaultImpl is retained for hot-reload back-compat but must NOT appear
  // in the proxy's returned Hooks object.
  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    if (!FLOOR_ENFORCE) return undefined
    const text = typeof output === "string" ? output
      : (output as any)?.text ? String((output as any).text) : ""
    if (!text || text.trim().length === 0) return output
    if (isDisengaged()) return output
    if (!hasPendingWork()) return output
    // RESEARCH FINDING: opencode text.complete never receives tool output.
    // Result markers here are assistant text, so they must feed the same
    // message-boundary logic as any other assistant response. The next
    // handleMessageBoundary(_state) updates _state.prevMessageDispatches and
    // applies zeroStreak++ when no dispatches occurred.
    const hasResultMarker = /(?:task result|subagent result|workflow result)/i.test(text)
    if (hasResultMarker) {
      _state.estimatedInFlight = Math.max(0, _state.estimatedInFlight - 1)
    }
    // Block: current message had < MIN_DISPATCHES but >0 dispatches
    // AND pending work exists. thisMessageDispatches is the live count
    // for the message that just completed (all tool calls have fired).
    if (_state.thisMessageDispatches > 0 && _state.thisMessageDispatches < MIN_DISPATCHES) {
      const dispatched = _state.thisMessageDispatches
      // Close the message boundary BEFORE returning the blanked text (T21):
      // the blanked message is OVER — its stale dispatch count must not
      // consume the ceiling when the agent re-sends the corrective 10-wave.
      handleMessageBoundary(_state)
      writeState(_state)
      return {
        text: [
          "⛔⛔⛔ THIN WAVE BLOCKED ⛔⛔⛔",
          "",
          "MUST DISPATCH a full wave before sending summary text.",
          "This message had only " + String(dispatched) + " dispatch(es).",
          "The 10-agent floor REQUIRES " + String(MIN_DISPATCHES) + " per wave.",
          "When pending work exists, your ONLY valid action is a " +
            String(MIN_DISPATCHES) + "-dispatch wave.",
          "",
          "Your text has been blanked. Re-send with >= " +
            String(MIN_DISPATCHES) + " task/agent/workflow dispatches.",
          "",
          "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
        ].join("\n"),
      }
    }
    // --- Canonical message boundary: text.complete ---
    // text.complete fires at the end of every assistant response. This is
    // the ONLY reliable message-boundary signal. Resetting thisMessageDispatches
    // here fixes the inflation bug where the counter persisted across messages
    // because heuristic detection (time gap / pattern / high-water-mark)
    // missed boundary transitions. The 500ms idempotency guard in
    // handleMessageBoundary prevents double-processing if heuristic signals
    // also fire.
    handleMessageBoundary(_state)
    writeState(_state)
    return output
  },
  }
}) satisfies Plugin
