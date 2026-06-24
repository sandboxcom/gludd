import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"

// enforce-deadline.ts — subagent task wall-clock timeout enforcement.
//
// PROBLEM: AGENTS.md says "Each subagent task must complete in under 5 minutes"
// but that was pure prose with ZERO mechanical enforcement. Subagent tasks ran
// for 1-2 hours with no timeout. This plugin makes the limit observable.
//
// WHAT IT DOES:
//   * tool.execute.before (task/agent/workflow)  -> record dispatch timestamp
//   * tool.execute.before (ANY tool)            -> warn on any task whose
//                                                  elapsed > GLUDD_TASK_TIMEOUT_MS
//   * tool.execute.after  (task/agent/workflow)  -> remove completed task
//
// It cannot hard-kill a running task (the plugin API has no kill primitive).
// It surfaces the breach via console.warn so the orchestrator (the main-loop
// agent reading its own tool stream) sees it and can dispatch a replacement /
// re-split the work. Observability beats silent hangs.
//
// FAIL-OPEN: every code path is wrapped so an internal error NEVER wedges the
// session. Worst case = no deadline enforcement (back to the old behavior),
// never a blocked tool call.

// ============================================================================
// CONFIG
// ============================================================================
const TASK_TIMEOUT_MS = parseInt(process.env.GLUDD_TASK_TIMEOUT_MS || "300000", 10)
const DEADLINE_STATE = process.env.GLUDD_TASK_DEADLINE_STATE || "/tmp/gludd-task-deadlines.json"
const DEADLINE_ENABLED = (process.env.GLUDD_TASK_DEADLINE_ENABLED || "1") !== "0"

// ============================================================================
// STATE FILE (atomic-ish read/write; fail-open on any IO error)
// Shape: { "<task_id>": <dispatch epoch ms>, ... }
// ============================================================================
function loadDeadlines(): Record<string, number> {
  try {
    const data = JSON.parse(fs.readFileSync(DEADLINE_STATE, "utf8"))
    return data && typeof data === "object" ? data as Record<string, number> : {}
  } catch {
    return {}
  }
}

function saveDeadlines(d: Record<string, number>): void {
  try {
    const tmp = DEADLINE_STATE + ".tmp"
    fs.writeFileSync(tmp, JSON.stringify(d))
    fs.renameSync(tmp, DEADLINE_STATE)
  } catch { /* fail open */ }
}

function extractTaskId(args: unknown): string | null {
  try {
    if (!args || typeof args !== "object") return null
    const a = args as Record<string, unknown>
    if (typeof a.task_id === "string" && a.task_id) return a.task_id
    if (typeof a.id === "string" && a.id) return a.id
    return null
  } catch { return null }
}

function isDispatchTool(tool: string): boolean {
  return tool === "task" || tool === "agent" || tool === "workflow"
}

// ============================================================================
// PLUGIN
// ============================================================================
export default (async ({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (!DEADLINE_ENABLED) return
      const tool = input.tool
      const args = output?.args

      try {
        // (1) On task/agent/workflow dispatch: record start time.
        if (isDispatchTool(tool)) {
          const id = extractTaskId(args) || `auto-${Date.now()}`
          const d = loadDeadlines()
          d[id] = Date.now()
          saveDeadlines(d)
        }

        // (2) On EVERY tool: scan tracked tasks for deadline breaches.
        const d = loadDeadlines()
        const now = Date.now()
        for (const id of Object.keys(d)) {
          const start = d[id]
          if (typeof start !== "number") continue
          const elapsed = now - start
          if (elapsed > TASK_TIMEOUT_MS) {
            const mins = (elapsed / 60000).toFixed(1)
            const limitMin = (TASK_TIMEOUT_MS / 60000).toFixed(0)
            // Advisory — plugins cannot hard-kill tasks. The orchestrator reads
            // its own console stream and acts (re-dispatch / re-split / abandon).
            console.warn(
              `TASK DEADLINE EXCEEDED: task ${id} has been running for ${mins}min ` +
              `(limit ${limitMin}min). This task should have completed. The ` +
              `orchestrator should dispatch a replacement.`
            )
          }
        }
      } catch { /* fail open — never wedge */ }
    },

    "tool.execute.after": async (input, _output) => {
      if (!DEADLINE_ENABLED) return
      if (!isDispatchTool(input.tool)) return
      try {
        const id = extractTaskId(input.args) || null
        if (!id) return
        const d = loadDeadlines()
        if (d[id] !== undefined) {
          delete d[id]
          saveDeadlines(d)
        }
      } catch { /* fail open */ }
    },
  }
}) satisfies Plugin
