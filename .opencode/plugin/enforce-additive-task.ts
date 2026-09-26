import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive, writeHeartbeat, getProjectRoot } from "../lib/shared.ts"

// enforce-additive-task.ts — per-wave additive task enforcement.
//
// Prevents the agent from dispatching 100% new-task waves while existing
// in-progress items remain unchecked in TASKS.md.  Enforces at least 1
// continuation slot per wave when ≥2 items are pending.
//
// WHAT IT DOES:
//   * tool.execute.before (task/agent/workflow) — classifies dispatch as
//     "continuation" (prompt references a TASKS.md task ID like SEC.1, D-13)
//     or "new-task" (no task ID reference).  Reads TASKS.md to count
//     unchecked items.
//   * Rule 1: ≥2 unchecked items AND 0 continuation-classified dispatches
//     in the wave → DENY.
//   * Rule 2: ≥10 dispatches AND 100% new-task → DENY.
//
// STATE FILE: /tmp/gludd-additive-task.json
// DISABLE: GLUDD_ADDITIVE_TASK_ENFORCE=0
// SOFT MODE: GLUDD_ADDITIVE_TASK_BLOCK=0 (console.warn only, no deny)
// SUBAGENT SKIP: OPENCODE_SUBAGENT=1
//
// FAIL-OPEN: every code path wrapped; internal errors never wedge the session.
//
// HOT-RELOAD: proxy pattern from hot_reload.ts.  Hook functions check
// /tmp/gludd-hot-additive-task.js on every invocation.  Run
// `make hot-reload-plugins` after editing this file.

const STATE_FILE = process.env.GLUDD_ADDITIVE_TASK_STATE || "/tmp/gludd-additive-task.json"
const ENABLED = (process.env.GLUDD_ADDITIVE_TASK_ENFORCE || "1") !== "0"
const BLOCK = (process.env.GLUDD_ADDITIVE_TASK_BLOCK || "1") !== "0"

const TASK_ID_RE = /\b[A-Z]+[.-]\d+\b/

interface AdditiveEntry {
  type: "continuation" | "new-task"
  prompt_head: string
  ts: number
}

interface AdditiveState {
  wave: AdditiveEntry[]
  inProgressCount: number
  lastPid: number
  lastTs: number
}

function _freshState(): AdditiveState {
  return { wave: [], inProgressCount: 0, lastPid: process.pid, lastTs: 0 }
}

function _isStale(raw: any): boolean {
  if (typeof raw.lastPid === "number" && raw.lastPid !== process.pid) return true
  if (typeof raw.lastTs === "number" && (Date.now() - raw.lastTs) > 1_800_000) return true
  return false
}

function loadState(): AdditiveState {
  try {
    if (fs.existsSync(STATE_FILE)) {
      const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
      if (_isStale(raw)) return _freshState()
      return {
        wave: Array.isArray(raw.wave) ? raw.wave : [],
        inProgressCount: typeof raw.inProgressCount === "number" ? raw.inProgressCount : 0,
        lastPid: typeof raw.lastPid === "number" ? raw.lastPid : process.pid,
        lastTs: typeof raw.lastTs === "number" ? raw.lastTs : Date.now(),
      }
    }
  } catch {}
  return _freshState()
}

function saveState(s: AdditiveState): void {
  try {
    s.lastPid = process.pid
    s.lastTs = Date.now()
    fs.writeFileSync(STATE_FILE, JSON.stringify(s), "utf8")
  } catch {}
}

function countUnchecked(): number {
  try {
    const root = getProjectRoot()
    const tasksPath = path.join(root, "TASKS.md")
    if (!fs.existsSync(tasksPath)) return 0
    const content = fs.readFileSync(tasksPath, "utf8")
    const matches = content.match(/^-\s*\[ \]/gm)
    return matches ? matches.length : 0
  } catch {
    return 0
  }
}

function extractPrompt(args: any): string {
  if (!args) return ""
  if (typeof args.prompt === "string") return args.prompt
  if (typeof args.description === "string") return args.description
  if (typeof args.message === "string") return args.message
  if (typeof args.content === "string") return args.content
  if (typeof args.text === "string") return args.text
  try { return JSON.stringify(args).substring(0, 500) } catch { return "" }
}

function classify(prompt: string): "continuation" | "new-task" {
  return TASK_ID_RE.test(prompt) ? "continuation" : "new-task"
}

function isDispatchTool(tool: string): boolean {
  return tool === "task" || tool === "agent" || tool === "workflow"
}

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, _output: any) => {
    if (isSubagent()) return
    reportAlive("enforce-additive-task")
    writeHeartbeat("enforce-additive-task")
    if (!ENABLED) return
    const tool = input.tool
    if (!isDispatchTool(tool)) return
    try {
      const prompt = extractPrompt(input.args)
      const category = classify(prompt)
      const unchecked = countUnchecked()
      const s = loadState()
      s.wave.push({
        type: `${category}`,
        prompt_head: `${prompt.substring(0, 120)}`,
        ts: Date.now(),
      })
      s.inProgressCount = unchecked

      const cCount = s.wave.filter(e => e.type === "continuation").length
      const newCount = s.wave.filter(e => e.type === "new-task").length
      const total = s.wave.length

      if (unchecked >= 2 && cCount === 0 && total > 0) {
        s.wave = []
        saveState(s)
        if (BLOCK) {
          return {
            permissionDecision: "deny",
            message: `ADDITIVE TASK VIOLATION: ${unchecked} items unchecked in TASKS.md but 0/${total} dispatch slots reference existing task IDs. Include ≥1 continuation slot (reference a TASKS.md item like SEC.1, D-13, MWK.1).`
          }
        } else {
          console.warn(`ADDITIVE TASK WARNING: ${unchecked} items unchecked but 0/${total} dispatch slots are continuations.`)
          return
        }
      }

      if (total >= 10 && newCount === total) {
        const newPct = ((newCount / total) * 100).toFixed(0)
        s.wave = []
        saveState(s)
        if (BLOCK) {
          return {
            permissionDecision: "deny",
            message: `ADDITIVE TASK RATIO VIOLATION: ${newPct}% new-task dispatches (${newCount}/${total}). All slots are new tasks with 0 continuations. Include ≥1 continuation slot referencing an existing TASKS.md task ID.`
          }
        } else {
          console.warn(`ADDITIVE TASK RATIO WARNING: ${newPct}% new-task dispatches (${newCount}/${total}).`)
          return
        }
      }

      if (total >= 10) s.wave = []
      saveState(s)
    } catch { // fail open
  }
  },
}

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  return {
    "tool.execute.before": async (input: any, _output: any) => {
      if (isSubagent()) return;
      const impl = loadHotModule("additive-task", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, _output) : undefined
    },
  }
}) satisfies Plugin
