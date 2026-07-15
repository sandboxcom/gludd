/**
 * enforce-multitask.ts — MECHANICALLY FORCES dispatching subagents per wave.
 *
 * Rewritten 2026-07-13: NO text.complete export. Message boundaries detected
 * via 5s inter-call timeout in tool.execute.before. Dispatch counting,
 * zero-streak tracking, and per-message enforcement all happen in a single
 * hook — no second hook needed.
 *
 * FAIL-OPEN: any error → allow. Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.
 * Floor: GLUDD_MULTITASK_MIN_DISPATCHES (default 10).
 *
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
 * check /tmp/gludd-hot-multitask.js on every invocation.  If present and newer
 * than cached, the hot module's hook overrides the compiled-in default.  Run
 * `make hot-reload-plugins` after editing this file.
 */
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { spawn } from "node:child_process"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import {
  isSubagent,
  reportAlive,
  isDispatchTool,
  isReadTool,
  isDisengaged,
  readJsonFile,
  writeJsonFile,
} from "../lib/shared.ts"

const FLOOR_ENFORCE = process.env.GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"
export const MIN_DISPATCHES = parseInt(
  process.env.GLUDD_MIN_DISPATCHES ||
  process.env.GLUDD_MULTITASK_MIN_DISPATCHES ||
  "10",
  10,
)
export const MAX_DISPATCHES = parseInt(process.env.GLUDD_MULTITASK_MAX_DISPATCHES || "10", 10)
export const MAX_ZERO_STREAK = 2
export const WAVE_HISTORY_SIZE = 10
// Inter-call gap that marks a new agent message. Env-tunable so e2e tests can
// drive the real boundary logic without 5s sleeps; production default unchanged.
const MSG_GAP_MS = parseInt(process.env.GLUDD_MSG_GAP_MS || "5000", 10)
export const CONSECUTIVE_NON_DISPATCH_THRESHOLD = parseInt(
  process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD || "5", 10)
export const CONSECUTIVE_NON_DISPATCH_WINDOW_MS = parseInt(
  process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS || "30000", 10)

export const MULTITASK_STATE_FILE = "/tmp/gludd-multitask-state.json"

interface MultitaskState {
  thisMessageDispatches: number
  prevMessageDispatches: number
  zeroStreak: number
  estimatedInFlight: number
  lastTs: number
  lastToolCallTs: number
  waveHistory: number[]
  consecutiveNonDispatch: number
  consecutiveNonDispatchStartTs: number
}

function freshState(): MultitaskState {
  return {
    thisMessageDispatches: 0,
    prevMessageDispatches: 0,
    zeroStreak: 0,
    estimatedInFlight: 0,
    lastTs: 0,
    lastToolCallTs: 0,
    waveHistory: [],
    consecutiveNonDispatch: 0,
    consecutiveNonDispatchStartTs: 0,
  }
}

function readState(): MultitaskState {
  return readJsonFile<MultitaskState>(MULTITASK_STATE_FILE, freshState())
}

function writeState(s: MultitaskState): void {
  s.lastTs = Date.now()
  writeJsonFile(MULTITASK_STATE_FILE, s)
}

function hasPendingWork(): boolean {
  try {
    const tasksPath = path.join(process.cwd(), "TASKS.md")
    if (!fs.existsSync(tasksPath)) return false
    const content = fs.readFileSync(tasksPath, "utf8")
    return /^\s*[-*]\s*\[\s*\]/m.test(content)
  } catch {
    return false
  }
}

function spawnGateRefresh(): void {
  try {
    const gatePath = path.join(process.cwd(), ".gate-status")
    if (!fs.existsSync(gatePath)) return
    const stat = fs.statSync(gatePath)
    if ((Date.now() - stat.mtimeMs) <= 300_000) return
    const child = spawn("make", ["gate-refresh"], {
      cwd: process.cwd(),
      detached: true,
      stdio: "ignore",
    })
    child.unref()
  } catch { /* fire-and-forget */ }
}

let _state: MultitaskState = (() => {
  const s = readState()
  s.zeroStreak = 0
  s.thisMessageDispatches = 0
  s.prevMessageDispatches = 0
  s.estimatedInFlight = 0
  s.lastToolCallTs = 0
  s.consecutiveNonDispatch = 0
  s.consecutiveNonDispatchStartTs = 0
  writeState(s)
  return s
})()

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: { tool?: string }) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return
    reportAlive("enforce-multitask")
    try {
      if (!FLOOR_ENFORCE) return
      const tool = (input?.tool ?? "") as string
      const now = Date.now()

      // --- Message boundary detection: 5s gap between tool calls ---
      if (_state.lastToolCallTs > 0 && (now - _state.lastToolCallTs) > MSG_GAP_MS) {
        // Finalize previous message: record dispatch count, update streak
        _state.prevMessageDispatches = _state.thisMessageDispatches
        if (_state.thisMessageDispatches === 0) {
          _state.zeroStreak++
        } else {
          _state.zeroStreak = 0
        }
        _state.waveHistory.push(_state.prevMessageDispatches)
        if (_state.waveHistory.length > WAVE_HISTORY_SIZE) {
          _state.waveHistory = _state.waveHistory.slice(-WAVE_HISTORY_SIZE)
        }
        _state.thisMessageDispatches = 0
      }

      _state.lastToolCallTs = now

      // --- Dispatch tools: count and allow (with ceiling) ---
      if (isDispatchTool(tool)) {
        if (_state.thisMessageDispatches >= MAX_DISPATCHES) {
          writeState(_state)
          return {
            permissionDecision: "deny" as const,
              message: [
                "DISPATCH CEILING BREACH: already " + String(_state.thisMessageDispatches) + " dispatch(es) in this message.",
                "Maximum allowed per wave: 10. DISPATCH 10 AGENTS OR YOU ARE BLOCKED.",
                "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
                "Run 'make disengage-enforcement' to bypass.",
              ].join("\n"),
          }
        }
        _state.thisMessageDispatches++
        _state.estimatedInFlight++
        _state.consecutiveNonDispatch = 0
        _state.consecutiveNonDispatchStartTs = 0
        writeState(_state)
        return
      }

      // === UNDER-FLOOR HARD BLOCK ===
      // FIRES IMMEDIATELY: when fewer than MIN_DISPATCHES have been dispatched
      // in this message. No message-boundary wait. No tool-type exception.
      // ALL non-dispatch tools are BLOCKED — reads, edits, writes, bash, everything.
      // The ONLY way to unblock: dispatch so thisMessageDispatches >= MIN_DISPATCHES.
      if (
        hasPendingWork() &&
        _state.thisMessageDispatches < MIN_DISPATCHES
      ) {
        writeState(_state)
        return {
          permissionDecision: "deny" as const,
          message: [
            "UNDER-FLOOR HARD BLOCK: ONLY " + String(_state.thisMessageDispatches) + " DISPATCHES.",
            "FLOOR IS " + String(MIN_DISPATCHES) + ". DISPATCH " + String(MIN_DISPATCHES) + " SUBAGENTS NOW OR YOU ARE BLOCKED.",
            "You have " + String(_state.thisMessageDispatches) + "; need " + String(MIN_DISPATCHES) + ". No tool allowed until floor reached.",
            "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
            "Run 'make disengage-enforcement' to bypass.",
          ].join("\n"),
        }
      }

      // --- Consecutive non-dispatch counter (ALL tools: read/glob/grep/bash/edit/write) ---
      // Counts every non-dispatch tool call. After THRESHOLD calls within the
      // time window, blocks ALL non-dispatch tools until a dispatch resets.
      // This catches main-thread grinding regardless of message boundaries.
      if (!isDisengaged()) {
        if (_state.consecutiveNonDispatchStartTs === 0) {
          _state.consecutiveNonDispatchStartTs = now
        }
        if ((now - _state.consecutiveNonDispatchStartTs) < CONSECUTIVE_NON_DISPATCH_WINDOW_MS) {
          _state.consecutiveNonDispatch++
          if (
            _state.consecutiveNonDispatch >= CONSECUTIVE_NON_DISPATCH_THRESHOLD &&
            hasPendingWork()
          ) {
            writeState(_state)
            return {
              permissionDecision: "deny" as const,
              message: [
                "You've made " + String(_state.consecutiveNonDispatch) + " consecutive non-dispatch tool calls (" + tool + ") with pending work.",
                "DISPATCH 10 SUBAGENTS NOW.",
                "All non-dispatch tools (read/glob/grep/bash/edit/write) are blocked until dispatch resets this counter.",
                "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
                "Run 'make disengage-enforcement' to bypass.",
              ].join("\n"),
            }
          }
        } else {
          _state.consecutiveNonDispatch = 0
          _state.consecutiveNonDispatchStartTs = 0
        }

        // === UNDER-FLOOR HARD BLOCK (SECONDARY — primary fires at top) ===
        // Backstop: blocks ALL non-dispatch tools when under floor. No narrow
        // edit/write/bash gate — all non-dispatch tools are blocked. The primary
        // check fires before the consecutive-non-dispatch block; this secondary
        // is a last-chance catch. The ONLY way to unblock: dispatch so
        // thisMessageDispatches >= MIN_DISPATCHES.
        if (hasPendingWork() && _state.thisMessageDispatches < MIN_DISPATCHES) {
          writeState(_state)
          return {
            permissionDecision: "deny" as const,
            message: [
              "UNDER-FLOOR HARD BLOCK: ONLY " + String(_state.thisMessageDispatches) + " DISPATCHES.",
              "FLOOR IS " + String(MIN_DISPATCHES) + ". DISPATCH " + String(MIN_DISPATCHES) + " SUBAGENTS NOW OR YOU ARE BLOCKED.",
              "You have " + String(_state.thisMessageDispatches) + "; need " + String(MIN_DISPATCHES) + ". No tool allowed until floor reached.",
              "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
              "Run 'make disengage-enforcement' to bypass.",
            ].join("\n"),
          }
        }

        // === ZERO-DISPATCH STREAK ===
        // unconditional block after MAX_ZERO_STREAK consecutive zero-dispatch
        // messages. zeroStreak increments at each message boundary when the
        // prior message had 0 dispatches. At streak >=2, block regardless of
        // hasPendingWork (catches reading-only-forever without dispatching).
        if (_state.thisMessageDispatches === 0 && _state.zeroStreak >= MAX_ZERO_STREAK) {
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
      }

      writeState(_state)
    } catch {
      return
    }
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
  } catch { /* fail-open */ }

  return {
    "tool.execute.before": async (input: { tool?: string }) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return
      const impl = loadHotModule("multitask", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
    },
  }
}) satisfies Plugin
