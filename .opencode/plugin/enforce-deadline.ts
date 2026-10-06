import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive } from "../lib/shared.ts"
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
//                                                  AND record to STALE_FILE
//   * tool.execute.after  (task/agent/workflow)  -> remove completed task
//
// It cannot hard-kill a running task (the plugin API has no kill primitive).
// It surfaces the breach via console.warn so the orchestrator (the main-loop
// agent reading its own tool stream) sees it and can dispatch a replacement /
// re-split the work. It also writes breached task IDs to STALE_FILE
// (/tmp/gludd-task-stale.json) so scripts/task_watchdog.py (the killing layer)
// can read them and kill the associated hung processes. Observability +
// bridge-to-killer beats silent hangs.
//
// NOISE CONTROL (2026-06-28): a lingering breached task used to re-warn on
// EVERY subsequent tool call, flooding the user UI with the same line. Now
// each task id triggers console.warn AT MOST ONCE per session (in-memory
// `warnedIds` Set); repeat breaches for the same id are still appended to
// the persistent warning log (`GLUDD_TASK_DEADLINE_WARNINGS`) so the
// orchestrator can poll them via `make task-ttl-check`. The enforcement
// (detection + persistent log + CLI mirror) is unchanged; only the noisy
// console channel is throttled.
//
// STANDALONE CLI MIRROR: scripts/task_ttl_check.py reads the same state file
// and is exposed as `make task-ttl-check`. The CLI is the gate-friendly face
// of this plugin (usable from CI / make targets / shell); the plugin is the
// live in-session face. Both must agree on the state file shape and TTL env.
//
// FAIL-OPEN: every code path is wrapped so an internal error NEVER wedges the
// session. Worst case = no deadline enforcement (back to the old behavior),
// never a blocked tool call.
//
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
// check /tmp/gludd-hot-deadline.js on every invocation.  If present and newer
// than cached, the hot module's hook overrides the compiled-in default.  Run
// `make hot-reload-plugins` after editing this file to generate the hot module.
// ============================================================================
// CONFIG
// ============================================================================
const TASK_TIMEOUT_MS = parseInt(process.env.GLUDD_TASK_TIMEOUT_MS || "300000", 10)
const DEADLINE_STATE = process.env.GLUDD_TASK_DEADLINE_STATE || "/tmp/gludd-task-deadlines.json"
const WARNINGS_LOG = process.env.GLUDD_TASK_DEADLINE_WARNINGS || "/tmp/gludd-task-deadlines.warnings.log"
const STALE_FILE = process.env.GLUDD_TASK_STALE_FILE || "/tmp/gludd-task-stale.json"
const DEADLINE_ENABLED = (process.env.GLUDD_TASK_DEADLINE_ENABLED || "1") !== "0"
const DEADLINE_ENFORCE = process.env.GLUDD_TASK_DEADLINE_ENFORCE !== "0"
const BLOCK = DEADLINE_ENFORCE && (process.env.GLUDD_TASK_DEADLINE_BLOCK || "1") !== "0"
// ============================================================================
// NOISE-CONTROL STATE
// ============================================================================
const warnedIds = new Set<string>()
function appendWarning(line: string): void {
  try {
    fs.appendFileSync(WARNINGS_LOG, line + "\n")
  } catch { // fail open
 }
}
function recordStaleTask(taskId: string, startMs: number, elapsedMs: number): void {
  try {
    let entries: any[] = []
    try {
      const raw = JSON.parse(fs.readFileSync(STALE_FILE, "utf8"))
      if (Array.isArray(raw)) entries = raw
    } catch {  }
    if (!entries.some((e: any) => e && e.task_id === taskId)) {
      entries.push({ task_id: taskId, start_ms: startMs, elapsed_ms: Math.round(elapsedMs), stale_at: Date.now() })
      const tmp = STALE_FILE + ".tmp"
      fs.writeFileSync(tmp, JSON.stringify(entries))
      fs.renameSync(tmp, STALE_FILE)
    }
  } catch { // fail open
 }
}
// ============================================================================
// STATE FILE
// ============================================================================
function loadDeadlines(): Record<string, number> {
  try {
    const data = JSON.parse(fs.readFileSync(DEADLINE_STATE, "utf8"))
    const out = data && typeof data === "object" ? data as Record<string, number> : {}
    sweepStaleEntries(out)
    return out
  } catch {
    return {}
  }
}
function sweepStaleEntries(d: Record<string, number>): void {
  const now = Date.now()
  const maxAge = TASK_TIMEOUT_MS * 3
  let mutated = false
  for (const id of Object.keys(d)) {
    const start = d[id]
    if (typeof start !== "number") continue
    if (now - start > maxAge) {
      delete d[id]
      warnedIds.delete(id)
      mutated = true
    }
  }
  if (mutated) {
    try {
      const tmp = DEADLINE_STATE + ".tmp"
      fs.writeFileSync(tmp, JSON.stringify(d))
      fs.renameSync(tmp, DEADLINE_STATE)
    } catch { // fail open
 }
  }
}
function saveDeadlines(d: Record<string, number>): void {
  try {
    const tmp = DEADLINE_STATE + ".tmp"
    fs.writeFileSync(tmp, JSON.stringify(d))
    fs.renameSync(tmp, DEADLINE_STATE)
  } catch { // fail open
 }
}
function extractTaskId(args: unknown): string | null {
  try {
    if (!args || typeof args !== "object") return null
    const a = args as Record<string, unknown>
    if (typeof a.task_id === "string" && a.task_id) return a.task_id
    if (typeof a.id === "string" && a.id) return a.id
    const desc = typeof a.description === "string" ? a.description : ""
    const subtype = typeof a.subagent_type === "string" ? a.subagent_type : ""
    if (desc || subtype) {
      const raw = `${subtype}:${desc}`
      let hash = 5381
      for (let i = 0; i < raw.length; i++) {
        hash = ((hash << 5) + hash + raw.charCodeAt(i)) | 0
      }
      return `d-${(hash >>> 0).toString(16)}`
    }
    return null
  } catch { return null }
}
function isDispatchTool(tool: string): boolean {
  return tool === "task" || tool === "agent" || tool === "workflow"
}
// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, output: any) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return
    reportAlive("enforce-deadline")
    if (!DEADLINE_ENABLED) return
    const tool = input.tool
    const args = output?.args
    try {
      if (isDispatchTool(tool)) {
        const id = extractTaskId(args) || `auto-${Date.now()}`
        const d = loadDeadlines()
        d[id] = Date.now()
        saveDeadlines(d)
      }
      const d = loadDeadlines()
      const now = Date.now()
      let firstBreachedId: string | null = null
      let firstBreachedElapsed: number = 0
      for (const id of Object.keys(d)) {
        const start = d[id]
        if (typeof start !== "number") continue
        const elapsed = now - start
        if (elapsed > TASK_TIMEOUT_MS) {
          const mins = (elapsed / 60000).toFixed(1)
          const limitMin = (TASK_TIMEOUT_MS / 60000).toFixed(0)
          const line =
            `TASK DEADLINE EXCEEDED: task ${id} has been running for ${mins}min ` +
            `(limit ${limitMin}min). This task should have completed. The ` +
            `orchestrator should dispatch a replacement.`
          appendWarning(`${new Date().toISOString()} ${line}`)
          recordStaleTask(id, start, elapsed)
          if (!warnedIds.has(id)) {
            warnedIds.add(id)
            console.warn(line)
          }
          if (!firstBreachedId) {
            firstBreachedId = id
            firstBreachedElapsed = elapsed
          }
        }
      }
      if (BLOCK && firstBreachedId && !isDispatchTool(tool)) {
        const elapsedSec = (firstBreachedElapsed / 1000).toFixed(0)
        const limitSec = (TASK_TIMEOUT_MS / 1000).toFixed(0)
        return {
          permissionDecision: "deny",
          message: `TASK DEADLINE EXCEEDED: task ${firstBreachedId} has been running for ${elapsedSec}s (limit ${limitSec}s). Dispatch replacement or run in foreground.`
        }
      }
    } catch { // fail open
 }
  },
  "tool.execute.after": async (input: any, _output: any) => {
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
      warnedIds.delete(id)
    } catch { // fail open
 }
  },
}
// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  return {
    "tool.execute.before": async (input: any, output: any) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return;
      const impl = loadHotModule("deadline", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
    "tool.execute.after": async (input: any, _output: any) => {
      const impl = loadHotModule("deadline", defaultImpl)
      const fn = impl["tool.execute.after"]
      return fn ? await fn(input, _output) : undefined
    },
  }
}) satisfies Plugin
