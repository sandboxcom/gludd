// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import {
  CONSECUTIVE_NON_DISPATCH_THRESHOLD,
  CONSECUTIVE_NON_DISPATCH_WINDOW_MS,
  HARD_MAX_DISPATCHES,
  MAX_DISPATCHES,
  MAX_ZERO_STREAK,
  MIN_DISPATCHES,
  MSG_GAP_MS,
  MULTITASK_STATE_FILE,
} from "../lib/multitask_config.ts"
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
  isInPressureRelease,
  isInInlineRecovery,
  getPressureReleaseFloor,
  decrementPressureReleaseTurns,
  recordEmptyDispatch,
  recordSuccessfulDispatch,
  readDispatchOutcomes,
  writeDispatchOutcomes,
} from "../lib/shared.ts"
const FLOOR_ENFORCE = process.env.GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"
const CONFIGURED_MIN_DISPATCHES =
  process.env.GLUDD_MIN_DISPATCHES || process.env.GLUDD_MULTITASK_MIN_DISPATCHES
const HAS_CONFIGURED_MIN_DISPATCHES = CONFIGURED_MIN_DISPATCHES !== undefined
const REQUIRED_DISPATCHES = HAS_CONFIGURED_MIN_DISPATCHES
  ? Math.max(0, Math.min(MAX_DISPATCHES, Number.isFinite(MIN_DISPATCHES) ? MIN_DISPATCHES : 0))
  : 0
const WAVE_HISTORY_SIZE = 10
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

    const ciCachePath = process.env.GLUDD_CI_CACHE_PATH || "/tmp/gludd-watchdog-ci.json"
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const rawLastCheck: number = ciData.last_ci_check || 0
      const lastCheck: number = rawLastCheck < 1e11 ? rawLastCheck * 1000 : rawLastCheck
      const lastStatus = ciData.last_ci_status || ""
      if (Date.now() - lastCheck < 600_000 && lastStatus && lastStatus !== "SUCCESS") return true
    }

    try {
      const todowritePath = process.env.GLUDD_TODOWRITE_STATE_PATH || "/tmp/gludd-todowrite-state.json"
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
  // Count only an operator-configured minimum. Ten is a hard ceiling and a
  // recommendation for large waves, never an unconditional floor.
  if (REQUIRED_DISPATCHES > 0 && s.prevMessageDispatches < REQUIRED_DISPATCHES) {
    s.underFloorCount++
  } else {
    s.underFloorCount = 0
  }
  s.thisMessageDispatches = 0
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
// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// Module-private because named exports crash OpenCode's legacy plugin loader.
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
              "Maximum allowed per wave: " + String(MAX_DISPATCHES) +
                " (absolute project ceiling: " + String(HARD_MAX_DISPATCHES) + ").",
              "Wait for an in-flight agent or continue appropriate inline work.",
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
      // Disabling floor/grinding policy never disables the hard dispatch cap.
      if (!FLOOR_ENFORCE) {
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
      //
      // PRESSURE-RELEASE: skip grinding block when in pressure-release or
      // inline-recovery mode. The agent needs inline tools to recover from
      // empty/failed dispatches.
      const pressureActive = isInPressureRelease() || isInInlineRecovery()
      if (!disengaged && !pressureActive) {
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
              "Dispatch one suitable independent work item via task/agent/workflow, or use the configured recovery path.",
              "The dispatch resets this counter; never create agents merely to fill a quota.",
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
      //
      // A minimum is enforced only when the operator explicitly configured it.
      // Pressure release may lower that configured requirement, never invent
      // a minimum for an otherwise adaptive session.
      const _isUnderFloorRead = lt === "read" || lt === "grep" || lt === "glob"
      const _isUnderFloorMutation = lt === "edit" || lt === "write" || lt === "bash"
      const _effectiveFloor = REQUIRED_DISPATCHES > 0
        ? getPressureReleaseFloor(REQUIRED_DISPATCHES)
        : 0
      if (
        !disengaged &&
        REQUIRED_DISPATCHES > 0 &&
        hasPendingWork() &&
        _state.thisMessageDispatches < _effectiveFloor &&
        (_isUnderFloorMutation || (_isUnderFloorRead && _state.sessionDispatchTotal > 0))
      ) {
        writeState(_state)
        return {
          permissionDecision: "deny" as const,
          message: [
            "CONFIGURED MINIMUM BLOCK: ONLY " + String(_state.thisMessageDispatches) + " DISPATCHES.",
            "Configured minimum is " + String(_effectiveFloor) + ". Dispatch only the missing suitable work items.",
            "You have " + String(_state.thisMessageDispatches) + "; need " + String(_effectiveFloor) + ". ALL tools (read/grep/glob/edit/write/bash) are blocked when below floor and dispatches have been made this session.",
            "consecutive non-dispatch calls: " + String(_state.consecutiveNonDispatch),
            "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
            "Run 'make disengage-enforcement' to bypass.",
          ].join("\n"),
        }
      }
      // === ZERO-DISPATCH STREAK (FIRES BEFORE UNDER-FLOOR) ===
      if (
        !disengaged &&
        REQUIRED_DISPATCHES > 0 &&
        _state.thisMessageDispatches === 0 &&
        _state.zeroStreak >= MAX_ZERO_STREAK
      ) {
        writeState(_state)
        return {
          permissionDecision: "deny" as const,
          message: [
            "ZERO-DISPATCH STREAK: " + String(MAX_ZERO_STREAK) + " consecutive responses with 0 subagent dispatches.",
            "An operator-configured minimum is active: " + String(REQUIRED_DISPATCHES) + ".",
            "Dispatch suitable independent work; the hard ceiling remains " + String(MAX_DISPATCHES) + ".",
            "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable minimum enforcement.",
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
      // PRESSURE-RELEASE detection: when subagent results arrive, check if
      // they indicate empty/failed outcomes. Short text after a result marker
      // OR failure/error/empty keywords signal a dispatch that produced no
      // useful work. After 3 consecutive empty dispatches, pressure-release
      // mode activates automatically.
      const isShortResult = text.length < 200
      const isEmptyPattern = /(?:failed|error|empty|no result|nothing|unable|cannot|could not|unsuccessful)/i.test(text)
      const isSummaryWithNoDispatches = _state.thisMessageDispatches === 0 && hasResultMarker
      if (isEmptyPattern || isShortResult || isSummaryWithNoDispatches) {
        recordEmptyDispatch()
      } else {
        recordSuccessfulDispatch()
      }
    }
    // PRESSURE-RELEASE: decrement turn counters at every message boundary.
    // This is the canonical boundary signal — text.complete fires at the
    // end of every assistant response.
    decrementPressureReleaseTurns()
    const _tef = REQUIRED_DISPATCHES > 0
      ? getPressureReleaseFloor(REQUIRED_DISPATCHES)
      : 0
    if (REQUIRED_DISPATCHES > 0 && _state.thisMessageDispatches > 0 && _state.thisMessageDispatches < _tef) {
      const dispatched = _state.thisMessageDispatches
      handleMessageBoundary(_state)
      // MT.1: escalate when under-floor waves keep happening
      const mt1Escalation = _state.underFloorCount >= 3
        ? [
            "",
            "⛔ CONFIGURED MINIMUM: fewer than " + String(_tef) + " dispatches for " + String(_state.underFloorCount) + " consecutive waves.",
            "This minimum was explicitly configured; the absolute ceiling remains " + String(MAX_DISPATCHES) + ".",
          ].join("\n")
        : ""
      writeState(_state)
      return {
        text: [
          "THIN WAVE BLOCKED",
          "MUST DISPATCH a full wave before sending summary text.",
          `This message had only ${dispatched} dispatch(es).`,
          `The configured minimum requires ${_tef} per wave.`,
          "Your text has been blanked. Re-send after satisfying the configured minimum.",
          mt1Escalation,
          "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
        ].join("\n"),
      }
    }
    handleMessageBoundary(_state)
    // MT.1: escalate when under-floor waves keep happening — inject into
    // non-blocked output so the agent sees it even on full-wave responses.
    const warnings: string[] = []
    if (REQUIRED_DISPATCHES > 0 && _state.underFloorCount >= 3) {
      warnings.push([
        "⛔ CONFIGURED MINIMUM: " + String(_state.underFloorCount) + " consecutive waves with fewer than " + String(REQUIRED_DISPATCHES) + " dispatches.",
        "Only the explicit minimum is enforced; never pad a wave to ten.",
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
    return fn ? await fn(_input, output) : undefined
  },
  }
}) satisfies Plugin
