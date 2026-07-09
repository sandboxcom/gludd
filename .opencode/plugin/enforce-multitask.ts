/**
 * enforce-multitask.ts — MECHANICALLY FORCES dispatching 10+ subagents per wave.
 *
 * Per AGENTS.md "Message-shape mechanical rule" and user directive (2026-07-09):
 * every assistant response containing tool calls MUST satisfy ONE of:
 *   (a) Zero task/agent/workflow dispatches (pure read/edit/bash — max 2 consecutive)
 *   (b) TEN OR MORE parallel task/agent/workflow dispatches in ONE message
 *
 * A response with 1–9 dispatches is DENIED. The agent must batch wider.
 *
 * ALSO: text.complete hook detects when the agent is about to stop without
 * dispatching — if zero in-flight subagents AND TASKS.md has unchecked items,
 * injects "DISPATCH SUBAGENTS NOW" into the response.
 *
 * FAIL-OPEN: any error → allow. Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.
 * Floor count env-configurable: GLUDD_MULTITASK_MIN_DISPATCHES (default 10).
 */
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

const FLOOR_ENFORCE = process.env.GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"
export const MIN_DISPATCHES = parseInt(process.env.GLUDD_MULTITASK_MIN_DISPATCHES || "10", 10)
export const MAX_ZERO_STREAK = 2

export const MULTITASK_STATE_FILE = "/tmp/gludd-multitask-state.json"

export const DENY_PREFIX =
  "MULTITASKING FLOOR: dispatch count below minimum. " +
  "Batch wider or add read-only filler to reach " +
  String(MIN_DISPATCHES) + "+ parallel dispatches per wave. " +
  "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable."

export const ZERO_STREAK_DENY_PREFIX =
  "MAX ZERO-DISPATCH RESPONSES EXCEEDED: " +
  String(MAX_ZERO_STREAK) + " consecutive responses with zero dispatches. " +
  "Must dispatch now."

export const STOP_GUARD_PREFIX =
  "DISPATCH SUBAGENTS NOW — " +
  "TASKS.md has unchecked items and zero subagents are in flight."

export const DISPATCH_TOOLS = Object.freeze(["task", "agent", "workflow"]) as readonly string[]

const RESULT_MARKERS: readonly string[] = [
  "task result",
  "completed",
  "agent result",
  "workflow result",
  "subagent result",
  "returning result",
  "final result",
]

interface MultitaskState {
  thisMessageDispatches: number
  prevMessageDispatches: number
  zeroStreak: number
  estimatedInFlight: number
  lastTs: number
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
      }
    }
  } catch { /* corrupt state → fresh start */ }
  return { thisMessageDispatches: 0, prevMessageDispatches: 0, zeroStreak: 0, estimatedInFlight: 0, lastTs: 0 }
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

function tasksHasUnchecked(): boolean {
  try {
    const tasksMd = process.env.GLUDD_TASKS_MD || path.join(process.cwd(), "TASKS.md")
    if (fs.existsSync(tasksMd)) {
      const content = fs.readFileSync(tasksMd, "utf8")
      return content.split("\n").filter(l => /^\s*[-*]\s+\[\s*\]/.test(l)).length > 0
    }
  } catch {}
  return false
}

function _reportAlive(): void {
  try {
    const alivePath = "/tmp/gludd-plugin-alive.json"
    const alive = fs.existsSync(alivePath)
      ? JSON.parse(fs.readFileSync(alivePath, "utf8"))
      : {}
    alive["enforce-multitask"] = { last_seen: Date.now() }
    fs.writeFileSync(alivePath, JSON.stringify(alive), "utf8")
  } catch { /* fail-open */ }
}

let _state: MultitaskState = readState()

export default (async ({ }) => {
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-multitask ` +
      `tool.execute.before+experimental.text.complete+session.idle ` +
      `pid=${process.pid}\n`,
      "utf8",
    )
  } catch { /* fail-open */ }

  return {
    "tool.execute.before": async (input: { tool?: string }) => {
      _reportAlive()
      try {
        if (!FLOOR_ENFORCE) return
        const tool = (input?.tool ?? "") as string

        if (isDispatchTool(tool)) {
          _state.thisMessageDispatches++
          _state.estimatedInFlight++
          writeState(_state)
          return
        }

        if (_state.prevMessageDispatches > 0 && _state.prevMessageDispatches < MIN_DISPATCHES) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "MULTITASKING FLOOR — SUB-MINIMUM DISPATCH WAVE DETECTED",
              "",
              `Previous message dispatched only ${_state.prevMessageDispatches} subagent(s).`,
              `Minimum required per wave: ${MIN_DISPATCHES} parallel dispatches.`,
              "",
              "REQUIRED: Batch ≥" + String(MIN_DISPATCHES) + " parallel task/agent dispatches in ONE message.",
              "Your message MUST contain either ZERO or ≥" + String(MIN_DISPATCHES) + " dispatches.",
              "1–" + String(MIN_DISPATCHES - 1) + " dispatches per wave is the dribbling anti-pattern.",
              "",
              "CORRECT: One message with " + String(MIN_DISPATCHES) + "+ Task tool calls in parallel.",
              "INCORRECT: Send one dispatch, wait, send another — or send 3–5 at a time.",
              "If fewer than " + String(MIN_DISPATCHES) + " edit tasks exist, fill slots with read-only research.",
              "",
              "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
            ].join("\n"),
          }
        }

        if (_state.prevMessageDispatches === 0 && _state.zeroStreak >= MAX_ZERO_STREAK) {
          const unchecked = tasksHasUnchecked()
          if (unchecked) {
            return {
              permissionDecision: "deny" as const,
              message: [
                "MAX ZERO-DISPATCH RESPONSES EXCEEDED — MUST DISPATCH NOW",
                "",
                String(MAX_ZERO_STREAK) + " consecutive responses with zero subagent dispatches.",
                "TASKS.md has unchecked items — work is pending.",
                "",
                "REQUIRED: Your next response MUST contain ≥" + String(MIN_DISPATCHES) + " parallel task/agent dispatches.",
                "Read-only responses cannot continue while work is pending.",
                "",
                "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
              ].join("\n"),
            }
          }
        }
      } catch {
        return
      }
    },

    "session.idle": async () => {
      _state = readState()
    },

    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      try {
        if (! output || typeof output.text !== "string") return output

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

        if (_state.estimatedInFlight === 0 && tasksHasUnchecked()) {
          return {
            text: [
              "DISPATCH SUBAGENTS NOW",
              "TASKS.md has unchecked items and zero subagents are estimated to be in flight.",
              "Your next response MUST contain " + String(MIN_DISPATCHES) + "+ parallel task/agent dispatches.",
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
