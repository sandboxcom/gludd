/**
 * enforce-multitask.ts — MECHANICALLY FORCES dispatching subagents per wave.
 *
 * The codified floor is HARD — cannot be bypassed by alternating tool types
 * or gating on pending-work checks. After MAX_ZERO_STREAK consecutive
 * zero-dispatch responses, ALL non-dispatch tool calls are denied.
 *
 * FAIL-OPEN: any error → allow. Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.
 * Floor: GLUDD_MULTITASK_MIN_DISPATCHES (default 7).
 */
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

const FLOOR_ENFORCE = process.env.GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"
export const MIN_DISPATCHES = parseInt(process.env.GLUDD_MULTITASK_MIN_DISPATCHES || "7", 10)
export const MIN_DISPATCHES_PER_WAVE = parseInt(process.env.GLUDD_MIN_DISPATCHES || "3", 10)
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

function _isSubagent(): boolean {
  if (process.env.OPENCODE_SUBAGENT === "1") return true;
  try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`); } catch { return false; }
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

function isDispatchTool(tool: string): boolean {
  return tool === "task" || tool === "agent" || tool === "workflow"
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
    const { spawn } = require("node:child_process")
    const child = spawn("make", ["gate-refresh"], {
      cwd: process.cwd(),
      detached: true,
      stdio: "ignore",
    })
    child.unref()
  } catch { /* fire-and-forget */ }
}

function _reportAlive(): void {
  try {
    const alivePath = "/tmp/gludd-plugin-alive.json"
    const alive = fs.existsSync(alivePath) ? JSON.parse(fs.readFileSync(alivePath, "utf8")) : {}
    alive["enforce-multitask"] = { last_seen: Date.now() }
    fs.writeFileSync(alivePath, JSON.stringify(alive), "utf8")
  } catch { /* fail-open */ }
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
      if (_isSubagent()) return
      console.log("SUBAGENT SKIP: enforce-multitask")
      _reportAlive()
      try {
        if (!FLOOR_ENFORCE) return
        const tool = (input?.tool ?? "") as string

        // Message boundary detection: if >5s since last tool call, new message
        const now = Date.now()
        if (_state.lastToolCallTs > 0 && (now - _state.lastToolCallTs) > 5000) {
          _state.thisMessageDispatches = 0
        }
        _state.lastToolCallTs = now

        if (isDispatchTool(tool)) {
          _state.thisMessageDispatches++
          _state.estimatedInFlight++
          _state.zeroStreak = 0  // dispatch resets the streak
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

        // ENFORCE MINIMUM DISPATCHES: previous message dispatched 1..N-1
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

        // PER-MESSAGE DISPATCH ENFORCEMENT: non-dispatch tools blocked if
        // current message has <7 dispatches AND pending work exists.
        if (!disengaged && hasPendingWork() && _state.thisMessageDispatches < 7) {
          const lt = tool.toLowerCase()
          if (lt === "edit" || lt === "write" || lt === "bash") {
            return {
              permissionDecision: "deny" as const,
              message: [
                "INSUFFICIENT DISPATCHES: only " + String(_state.thisMessageDispatches) + " dispatch(es) in this message.",
                "Must dispatch \u22657 subagents when work exists. Add dispatches and resend.",
                "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
                "Run 'make disengage-enforcement' to bypass.",
              ].join("\n"),
            }
          }
        }

        // ENFORCE ZERO-STREAK: N consecutive zero-dispatch messages → HARD DENY
        if (!disengaged && _state.prevMessageDispatches === 0 && _state.zeroStreak >= MAX_ZERO_STREAK) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "ZERO-DISPATCH STREAK: " + String(MAX_ZERO_STREAK) + " consecutive responses with 0 subagent dispatches.",
              "Codified floor " + String(MIN_DISPATCHES) + " is being IGNORED. This block is UNCONDITIONAL.",
              "REQUIRED: Next response MUST contain ≥" + String(MIN_DISPATCHES) + " task/agent/workflow dispatches.",
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
        if (_isSubagent()) return output
        console.log("SUBAGENT SKIP: enforce-multitask")
        console.log("SUBAGENT SKIP: enforce-multitask")
        if (!output || typeof output.text !== "string") return output
        if (/^(⛔|HARD STOP|MUST DISPATCH|ENHANCEMENT RATIO|████|BLOCKED:|MULTITASK|INSUFFICIENT DISPATCHES|ZERO-DISPATCH|DISPATCH SUBAGENTS|EARLY ENHANCEMENT|DELEGATE-FIRST|REFILL NEEDED|AFTER-RESULTS|CONSECUTIVE TEXT-ONLY|FALSE-DONE|QA RESPONSE)/.test(output.text.trim())) return output
        // RESEARCH FINDING (2026-07-12): text.complete hook NEVER fires on tool output — it only fires on text-end LLM stream events. The _input.role field does not exist in the payload. So no tool-output guard is needed: all text here is agent-generated. Do NOT add an isToolOutput / role-based guard — it is dead code.

        // Track subagent result markers (these arrive in agent text, since
        // text.complete only fires on agent-generated text).
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

        // BLOCKING: when zeroStreak exceeds max, REPLACE the agent text.
        // The agent is refusing to dispatch — silence their output.
        // But allow through if the operator has disengaged enforcement.
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

        if (!disengagedText && _state.prevMessageDispatches > 2 && _state.prevMessageDispatches < 7 && hasPendingWork()) {
          return {
            text: [
              "⛔ MESSAGE BLOCKED: must dispatch ≥7 subagents when work remains.",
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

        // Inject nag when estimated in-flight is 0
        if (_state.estimatedInFlight === 0) {
          return {
            text: [
              "DISPATCH SUBAGENTS NOW — 0 estimated in-flight.",
              "Your next response MUST contain ≥" + String(MIN_DISPATCHES) + " task/agent/workflow dispatches.",
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
}) satisfies Plugin
