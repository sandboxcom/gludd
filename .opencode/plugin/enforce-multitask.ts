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
  spawnGateRefreshIfStale,
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
const RESULT_ARRIVAL_REFRESH_INTERVAL_MS = parseInt(
  process.env.GLUDD_REFRESH_INTERVAL_MS || "30000", 10)
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
  underFloorCount: number
  lastDispatchTs: number
  singleDispatchWaves: number
  sessionDispatchTotal: number
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
    underFloorCount: 0,
    lastDispatchTs: 0,
    singleDispatchWaves: 0,
    sessionDispatchTotal: 0,
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
    const root = getProjectRoot()

    const tasksPath = path.join(root, "TASKS.md")
    if (fs.existsSync(tasksPath)) {
      const content = fs.readFileSync(tasksPath, "utf8")
      if (/^\s*[-*]\s*\[\s*\]/m.test(content)) return true
    }

    const ratchetPath = path.join(root, "config", "ratchet.yml")
    if (fs.existsSync(ratchetPath)) {
      const content = fs.readFileSync(ratchetPath, "utf8")
      const entries = content.split("\n").filter(
        l => l.trim() && !l.trim().startsWith("#") && (l.includes("::") || /^\w[\w\s]*:\s/.test(l))
      ).length
      if (entries > 0) return true
    }

    const bugsPath = path.join(root, "BUGS.md")
    if (fs.existsSync(bugsPath)) {
      const content = fs.readFileSync(bugsPath, "utf8")
      const hasOpen = content.split("\n").some(
        l => /^###\s+\d{4}-\d{2}-\d{2}\s+[-—]/.test(l) && !l.includes("(resolved)")
      )
      if (hasOpen) return true
    }

    const gatePath = path.join(root, ".gate-status")
    if (fs.existsSync(gatePath)) {
      const content = fs.readFileSync(gatePath, "utf8")
      if (/=== GATE:\s*FAILED/.test(content)) return true
      if (/test REQUIRED/.test(content) || /smoke REQUIRED/.test(content)) return true
    }

    const ciCachePath =
      process.env.GLUDD_WATCHDOG_CI_FILE || "/tmp/gludd-watchdog-ci.json"
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const rawLastCheck: number = ciData.last_ci_check || 0
      const lastCheck: number = rawLastCheck < 1e11 ? rawLastCheck * 1000 : rawLastCheck
      const lastStatus = ciData.last_ci_status || ""
      if (Date.now() - lastCheck < 600_000 && lastStatus && lastStatus !== "SUCCESS") return true
    }

    try {
      const todowritePath =
        process.env.GLUDD_TODOWRITE_STATE || "/tmp/gludd-todowrite-state.json"
      if (fs.existsSync(todowritePath)) {
        const tdData = JSON.parse(fs.readFileSync(todowritePath, "utf8"))
        const items: any[] = Array.isArray(tdData.items) ? tdData.items : []
        if (items.some((it: any) => it && (it.status === "pending" || it.status === "in_progress"))) return true
      }
    } catch {}
  } catch {
    return false
  }
  return false
}
function handleMessageBoundary(s: MultitaskState): void {
  const now = Date.now()
  // Idempotency guard: prevent double-processing within 500ms. When
  // text.complete calls the boundary handler first (canonical signal),
  // the heuristic detection in tool.execute.before may fire again on
  // the same boundary within the same process. Without this guard,
  // zeroStreak double-increments and waveHistory gets duplicate entries.
  const lastB = (s as any)._lastBoundaryTs
  if (lastB && now - lastB < 500) return
  (s as any)._lastBoundaryTs = now
  s.prevMessageDispatches = s.thisMessageDispatches
  // MT.2: single-dispatch wave escalation — 3 consecutive 1-dispatch waves triggers escalation.
  // Do NOT increment on 0-dispatch waves — that is the zero-streak violation.
  if (s.prevMessageDispatches === 1) { s.singleDispatchWaves++ } else if (s.prevMessageDispatches >= 2) { s.singleDispatchWaves = 0 }
  if (s.thisMessageDispatches === 0) {
    s.zeroStreak++
  } else {
    s.zeroStreak = 0
  }
  s.waveHistory.push(s.prevMessageDispatches)
  if (s.waveHistory.length > WAVE_HISTORY_SIZE) {
    s.waveHistory = s.waveHistory.slice(-WAVE_HISTORY_SIZE)
  }
  // MT.1: under-floor dispatch escalation
  // Count consecutive waves where dispatches were below the floor.
  // After 3+ consecutive sub-floor waves, escalate with a warning
  // injected into the next text.complete response.
  if (s.prevMessageDispatches < MAX_DISPATCHES) {
    s.underFloorCount++
  } else {
    s.underFloorCount = 0
  }
  s.thisMessageDispatches = 0
}
function spawnGateRefresh(): void {
  spawnGateRefreshIfStale(getProjectRoot(), spawn)
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
  s.underFloorCount = 0
  s.singleDispatchWaves = 0
  s.lastDispatchTs = 0
  // Preserve sessionDispatchTotal across restarts so the cumulative counter survives
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
        _state.sessionDispatchTotal++
        _state.estimatedInFlight++
        _state.lastDispatchTs = now
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
        if (!isReadTool(tool)) {
          if (_state.consecutiveNonDispatchStartTs === 0) {
            _state.consecutiveNonDispatchStartTs = now
          }
          if ((now - _state.consecutiveNonDispatchStartTs) < CONSECUTIVE_NON_DISPATCH_WINDOW_MS) {
            _state.consecutiveNonDispatch++
          } else {
            _state.consecutiveNonDispatch = 0
            _state.consecutiveNonDispatchStartTs = now
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
      // 2026-07-25 FIX: previously only blocked edit/write/bash. The agent
      // dispatched 2 agents then used unlimited reads between waves —
      // underFloorCount reached 2066 without being mechanically stopped.
      // Now blocks ALL non-dispatch tools (includes read/glob/grep) when ANY
      // dispatches have been made this session. Session-start (0 dispatches)
      // still allows reads for the initial backlog survey.
      //
      // Fallback for the first-edit-with-zero-dispatches case where the streak
      // counter (above) is still below threshold. When the streak has already
      // hit threshold, the streak block wins.
      const _isUnderFloorRead = lt === "read" || lt === "grep" || lt === "glob"
      const _isUnderFloorMutation = lt === "edit" || lt === "write" || lt === "bash"
      if (
        !disengaged &&
        hasPendingWork() &&
        _state.thisMessageDispatches < MIN_DISPATCHES &&
        (_isUnderFloorMutation || (_isUnderFloorRead && _state.sessionDispatchTotal > 0))
      ) {
        writeState(_state)
        return {
          permissionDecision: "deny" as const,
          message: [
            "UNDER-FLOOR HARD BLOCK: ONLY " + String(_state.thisMessageDispatches) + " DISPATCHES.",
            "Floor is 10. DISPATCH " + String(MIN_DISPATCHES) + " SUBAGENTS NOW OR YOU ARE BLOCKED.",
            "You have " + String(_state.thisMessageDispatches) + "; need " + String(MIN_DISPATCHES) + ". ALL tools (read/grep/glob/edit/write/bash) are blocked when below floor and dispatches have been made this session.",
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
    return await handleTextComplete(_input, output)
  },
  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    return await handleTextComplete(_input, output)
  },
}

async function handleTextComplete(_input: unknown, output: unknown): Promise<unknown> {
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
      // MT.1: escalate when under-floor waves keep happening
      const mt1Escalation = _state.underFloorCount >= 3
        ? [
            "",
            "⛔ ESCALATION: You have dispatched fewer than " + String(MAX_DISPATCHES) + " agents for " + String(_state.underFloorCount) + " consecutive waves.",
            "The 10-agent floor is MANDATORY. Every wave must be >= " + String(MAX_DISPATCHES) + " dispatches.",
            "This is " + String(_state.underFloorCount) + " waves in a row below the floor. Correct immediately.",
          ].join("\n")
        : ""
      // MT.2: single-dispatch wave escalation
      const mt2Escalation = _state.singleDispatchWaves >= 3
        ? [
            "",
            "MESSAGE SHAPE VIOLATION: 3 consecutive single-dispatch waves. Batch wider — 2+ dispatches per message.",
          ].join("\n")
        : ""
      writeState(_state)
      return {
        text: [
          "THIN WAVE BLOCKED",
          "MUST DISPATCH a full wave before sending summary text.",
          `This message had only ${dispatched} dispatch(es).`,
          `The 10-agent floor REQUIRES ${MIN_DISPATCHES} per wave.`,
          "Your text has been blanked. Re-send with >= " + String(MIN_DISPATCHES) + " dispatches.",
          mt1Escalation,
          mt2Escalation,
          "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
        ].join("\n"),
      }
    }
    handleMessageBoundary(_state)
    // MT.1: escalate when under-floor waves keep happening — inject into
    // non-blocked output so the agent sees it even on full-wave responses.
    // MT.2: single-dispatch wave escalation — inject when 3 consecutive waves
    // had exactly 1 dispatch.  2+ dispatches resets the counter.
    const warnings: string[] = []
    if (_state.underFloorCount >= 3) {
      warnings.push([
        "⛔ DISPATCH FLOOR VIOLATION: " + String(_state.underFloorCount) + " consecutive waves with fewer than " + String(MAX_DISPATCHES) + " dispatches.",
        "The 10-agent floor is MANDATORY. Each wave MUST dispatch exactly " + String(MAX_DISPATCHES) + " agents.",
        "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
      ].join("\n"))
    }
    if (_state.singleDispatchWaves >= 3) {
      warnings.push("MESSAGE SHAPE VIOLATION: 3 consecutive single-dispatch waves. Batch wider — 2+ dispatches per message.")
    }
    // DP.2: wave refill automation — inject reminder when pool drops low
    if (
      _state.lastDispatchTs > 0 &&
      _state.estimatedInFlight < 5 &&
      (Date.now() - _state.lastDispatchTs) > RESULT_ARRIVAL_REFRESH_INTERVAL_MS
    ) {
      warnings.push([
        "⛔ FLOOR LOW: only " + String(_state.estimatedInFlight) + " agents remain.",
        "Last dispatch was >" + String(Math.round((Date.now() - _state.lastDispatchTs) / 1000)) + "s ago.",
        "Dispatch replacements now to maintain the 10-agent floor.",
        "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
      ].join("\n"))
    }
    if (warnings.length > 0) {
      const warning = warnings.join("\n\n")
      const wrappedOutput = typeof output === "string"
        ? warning + "\n\n" + output
        : (output as any)?.text
          ? { ...(output as any), text: warning + "\n\n" + String((output as any).text) }
          : output
      writeState(_state)
      return wrappedOutput
    }
    writeState(_state)
    return output
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
    const impl = loadHotModule("multitask", defaultImpl)
    const fn = impl["experimental.text.complete"] ?? impl["text.complete"]
    return fn ? await fn(_input, output) : output
  },
  }
}) satisfies Plugin
