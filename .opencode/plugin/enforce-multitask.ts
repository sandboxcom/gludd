/**
 * enforce-multitask.ts — MECHANICALLY FORCES dispatching subagents per wave.
 *
 * Rewritten 2026-07-13: NO text.complete export. Message boundaries detected
 * via 5s inter-call timeout in tool.execute.before. Dispatch counting,
 * zero-streak tracking, and per-message enforcement all happen in a single
 * hook — no second hook needed.
 *
 * FAIL-OPEN: any error → allow. Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.
 * Floor: GLUDD_MULTITASK_MIN_DISPATCHES (default 3).
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
  isDisengaged,
  readJsonFile,
  writeJsonFile,
} from "../lib/shared.ts"

const FLOOR_ENFORCE = process.env.GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"
export const MIN_DISPATCHES = parseInt(process.env.GLUDD_MULTITASK_MIN_DISPATCHES || "10", 10)
export const MIN_DISPATCHES_PER_WAVE = parseInt(process.env.GLUDD_MIN_DISPATCHES || "10", 10)
export const MAX_DISPATCHES = parseInt(process.env.GLUDD_MULTITASK_MAX_DISPATCHES || "10", 10)
export const MAX_ZERO_STREAK = 2
export const WAVE_HISTORY_SIZE = 10
const MSG_GAP_MS = 5000

export const MULTITASK_STATE_FILE = "/tmp/gludd-multitask-state.json"

interface MultitaskState {
  thisMessageDispatches: number
  prevMessageDispatches: number
  zeroStreak: number
  estimatedInFlight: number
  lastTs: number
  lastToolCallTs: number
  waveHistory: number[]
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
          return {
            permissionDecision: "deny" as const,
            message: [
              "DISPATCH CEILING BREACH: already " + String(_state.thisMessageDispatches) + " dispatch(es) in this message.",
              "Maximum allowed per wave: " + String(MAX_DISPATCHES) + ". No more than " + String(MAX_DISPATCHES) + " dispatches.",
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

      // --- Non-dispatch tools: enforcement checks ---
      const disengaged = isDisengaged()

      if (!disengaged) {
        // FLOOR BREACH: previous message had >0 but <MIN_DISPATCHES, and streak is live
        if (
          _state.prevMessageDispatches > 0 &&
          _state.prevMessageDispatches < MIN_DISPATCHES &&
          _state.zeroStreak > 0
        ) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "MULTITASKING FLOOR BREACH: only " + String(_state.prevMessageDispatches) + " dispatch(es) in prior message.",
              "Codified floor: " + String(MIN_DISPATCHES) + ". This is NOT advisory.",
              "REQUIRED: \u2265" + String(MIN_DISPATCHES) + " parallel task/agent/workflow dispatches in ONE message.",
              "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
              "Run 'make disengage-enforcement' to bypass.",
            ].join("\n"),
          }
        }

        // PER-MESSAGE: current message has 0 dispatches, pending work, streak live
        if (hasPendingWork() && _state.thisMessageDispatches === 0 && _state.zeroStreak > 0) {
          const lt = tool.toLowerCase()
          if (lt === "edit" || lt === "write" || lt === "bash") {
            return {
              permissionDecision: "deny" as const,
              message: [
                "INSUFFICIENT DISPATCHES: only " + String(_state.thisMessageDispatches) + " dispatch(es) in this message.",
                "Must dispatch \u2265" + String(MIN_DISPATCHES) + " subagents when work exists. Add dispatches and resend.",
                "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
                "Run 'make disengage-enforcement' to bypass.",
              ].join("\n"),
            }
          }
        }

        // ZERO-DISPATCH STREAK: unconditional — MAX_ZERO_STREAK consecutive zero-dispatch messages
        if (_state.prevMessageDispatches === 0 && _state.zeroStreak >= MAX_ZERO_STREAK) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "ZERO-DISPATCH STREAK: " + String(MAX_ZERO_STREAK) + " consecutive responses with 0 subagent dispatches.",
              "Codified floor " + String(MIN_DISPATCHES) + " is being IGNORED. This block is UNCONDITIONAL.",
              "REQUIRED: Next response MUST contain \u2265" + String(MIN_DISPATCHES) + " task/agent/workflow dispatches.",
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
