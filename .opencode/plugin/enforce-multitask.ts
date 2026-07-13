/**
 * enforce-multitask.ts — MECHANICALLY FORCES dispatching subagents per wave.
 *
 * The codified floor is HARD — cannot be bypassed by alternating tool types
 * or gating on pending-work checks. After MAX_ZERO_STREAK consecutive
 * zero-dispatch responses, ALL non-dispatch tool calls are denied.
 *
 * FAIL-OPEN: any error → allow. Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.
 * Floor: GLUDD_MULTITASK_MIN_DISPATCHES (default 7).
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
import { loadHotModule, type HotModule } from "./hot_reload.ts"
import { isSubagent, reportAlive, isDispatchTool } from "./shared.ts"

const FLOOR_ENFORCE = process.env.GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"
export const MIN_DISPATCHES = parseInt(process.env.GLUDD_MULTITASK_MIN_DISPATCHES || "10", 10)
export const MIN_DISPATCHES_PER_WAVE = parseInt(process.env.GLUDD_MIN_DISPATCHES || "5", 10)
export const MAX_ZERO_STREAK = 2
export const WAVE_HISTORY_SIZE = 10
const MAX_DISENGAGE_MS = 3_600_000

export const MULTITASK_STATE_FILE = "/tmp/gludd-multitask-state.json"

export const DISPATCH_TOOLS = Object.freeze(["task", "agent", "workflow"]) as readonly string[]

const RESULT_MARKERS: readonly string[] = [
  "task result", "completed", "agent result", "workflow result",
  "subagent result", "returning result", "final result",
]

interface MultitaskState {
  thisMessageDispatches: number
  prevMessageDispatches: number
  zeroStreak: number
  estimatedInFlight: number
  lastTs: number
  lastToolCallTs: number
  waveHistory: number[]
}

function readState(): MultitaskState {
  try {
    if (fs.existsSync(MULTITASK_STATE_FILE)) {
      const raw = JSON.parse(fs.readFileSync(MULTITASK_STATE_FILE, "utf8"))
      return {
        thisMessageDispatches: typeof raw.thisMessageDispatches === "number" ? raw.thisMessageDispatches : 0,
        prevMessageDispatches: typeof raw.prevMessageDispatches === "number" ? raw.prevMessageDispatches : 0,
        zeroStreak: typeof raw.zeroStreak === "number" ? raw.zeroStreak : 0,
        estimatedInFlight: typeof raw.estimatedInFlight === "number" ? raw.estimatedInFlight : 0,
        lastTs: typeof raw.lastTs === "number" ? raw.lastTs : 0,
        lastToolCallTs: typeof raw.lastToolCallTs === "number" ? raw.lastToolCallTs : 0,
        waveHistory: Array.isArray(raw.waveHistory) ? raw.waveHistory : [],
      }
    }
  } catch { /* corrupt → fresh */ }
  return { thisMessageDispatches: 0, prevMessageDispatches: 0, zeroStreak: 0, estimatedInFlight: 0, lastTs: 0, lastToolCallTs: 0, waveHistory: [] }
}

function writeState(s: MultitaskState): void {
  try { s.lastTs = Date.now(); fs.writeFileSync(MULTITASK_STATE_FILE, JSON.stringify(s), "utf8") } catch {}
}

function hasResultMarker(text: string): boolean {
  const lower = text.toLowerCase()
  return RESULT_MARKERS.some(m => lower.includes(m))
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
    if (isSubagent()) return
    console.log("SUBAGENT SKIP: enforce-multitask")
    reportAlive("enforce-multitask")
    try {
      if (!FLOOR_ENFORCE) return
      const tool = (input?.tool ?? "") as string

      const now = Date.now()
      if (_state.lastToolCallTs > 0 && (now - _state.lastToolCallTs) > 5000) {
        _state.thisMessageDispatches = 0
      }
      _state.lastToolCallTs = now

      if (isDispatchTool(tool)) {
        _state.thisMessageDispatches++
        _state.estimatedInFlight++
        _state.zeroStreak = 0
        writeState(_state)
        return
      }

      let disengaged = false
      try {
        const disPath = "/tmp/gludd-watchdog-disengage.json"
        if (fs.existsSync(disPath)) {
          const d = JSON.parse(fs.readFileSync(disPath, "utf8"))
          if (d.disengage_until) {
            const now = Date.now()
            const effective = Math.min(d.disengage_until, now + MAX_DISENGAGE_MS)
            if (effective > now) disengaged = true
          }
        }
      } catch {}

      if (!disengaged && _state.prevMessageDispatches > 0 && _state.prevMessageDispatches < MIN_DISPATCHES) {
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

      if (!disengaged && hasPendingWork() && _state.thisMessageDispatches < MIN_DISPATCHES) {
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

      if (!disengaged && _state.prevMessageDispatches === 0 && _state.zeroStreak >= MAX_ZERO_STREAK) {
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
    } catch {
      return
    }
  },

  "session.idle": async () => {
    _state = readState()
    _state.zeroStreak = 0
    writeState(_state)
  },

  "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
    try {
      if (isSubagent()) return output
      console.log("SUBAGENT SKIP: enforce-multitask")
      console.log("SUBAGENT SKIP: enforce-multitask")
      if (!output || typeof output.text !== "string") return output
      if (/^(⛔|HARD STOP|MUST DISPATCH|ENHANCEMENT RATIO|████|BLOCKED:|MULTITASK|INSUFFICIENT DISPATCHES|ZERO-DISPATCH|DISPATCH SUBAGENTS|EARLY ENHANCEMENT|DELEGATE-FIRST|REFILL NEEDED|AFTER-RESULTS|CONSECUTIVE TEXT-ONLY|FALSE-DONE|QA RESPONSE)/.test(output.text.trim())) return output
      if (hasResultMarker(output.text)) {
        _state.estimatedInFlight = Math.max(0, _state.estimatedInFlight - 2)
      }
      _state.prevMessageDispatches = _state.thisMessageDispatches
      if (_state.thisMessageDispatches === 0) {
        _state.zeroStreak++
      } else {
        _state.zeroStreak = 0
      }
      _state.thisMessageDispatches = 0
      writeState(_state)
      _state.waveHistory.push(_state.prevMessageDispatches)
      if (_state.waveHistory.length > WAVE_HISTORY_SIZE) {
        _state.waveHistory = _state.waveHistory.slice(-WAVE_HISTORY_SIZE)
      }
      let disengagedText = false
      try {
        const disPath = "/tmp/gludd-watchdog-disengage.json"
        if (fs.existsSync(disPath)) {
          const d = JSON.parse(fs.readFileSync(disPath, "utf8"))
          if (d.disengage_until) {
            const now = Date.now()
            const effective = Math.min(d.disengage_until, now + MAX_DISENGAGE_MS)
            if (effective > now) disengagedText = true
          }
        }
      } catch {}
      if (!disengagedText && _state.prevMessageDispatches > 2 && _state.prevMessageDispatches < MIN_DISPATCHES && hasPendingWork()) {
        return {
          text: [
            "⛔ MESSAGE BLOCKED: must dispatch \u2265" + String(MIN_DISPATCHES) + " subagents when work remains.",
            "Instead got " + String(_state.prevMessageDispatches) + " dispatch(es).",
            "Resend with more task/agent/workflow dispatches.",
            "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
            "Run 'make disengage-enforcement' to bypass.",
          ].join(" "),
        }
      }
      if (!disengagedText && _state.zeroStreak >= MAX_ZERO_STREAK) {
        return {
          text: [
            "MUST DISPATCH " + String(MIN_DISPATCHES) + "+ SUBAGENTS NOW.",
            "Floor=" + String(MIN_DISPATCHES) + ", zeroStreak=" + String(_state.zeroStreak) + ".",
            "All other output blocked. Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
            "Run 'make disengage-enforcement' to bypass.",
          ].join(" "),
        }
      }
      if (!disengagedText && (_state.prevMessageDispatches === 1 || _state.prevMessageDispatches === 2) && hasPendingWork() && _state.estimatedInFlight < MIN_DISPATCHES_PER_WAVE) {
        return {
          text: [
            "MULTITASK WARNING: only " + String(_state.prevMessageDispatches) + " dispatch(es), floor requires \u2265" + String(MIN_DISPATCHES_PER_WAVE),
            output.text,
          ].join("\n"),
        }
      }
      if (_state.estimatedInFlight === 0) {
        return {
          text: [
            "DISPATCH SUBAGENTS NOW — 0 estimated in-flight.",
            "Your next response MUST contain \u2265" + String(MIN_DISPATCHES) + " task/agent/workflow dispatches.",
            "",
            output.text,
          ].join("\n"),
        }
      }
      return output
    } catch {
      return output
    }
  },
}

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (async ({ }) => {
  spawnGateRefresh()
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-multitask pid=${process.pid}\n`, "utf8",
    )
  } catch { /* fail-open */ }

  return {
    "tool.execute.before": async (input: { tool?: string }) => {
      if (isSubagent()) return
      console.log("SUBAGENT SKIP: enforce-multitask")
      const impl = loadHotModule("multitask", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
    },

    "session.idle": async () => {
      const impl = loadHotModule("multitask", defaultImpl)
      const fn = impl["session.idle"]
      return fn ? await fn() : undefined
    },

    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      const impl = loadHotModule("multitask", defaultImpl)
      const fn = impl["experimental.text.complete"]
      return fn ? await fn(_input, output) : undefined
    },
  }
}) satisfies Plugin
