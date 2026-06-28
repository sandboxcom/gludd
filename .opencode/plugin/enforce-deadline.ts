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

// ============================================================================
// CONFIG
// ============================================================================
const TASK_TIMEOUT_MS = parseInt(process.env.GLUDD_TASK_TIMEOUT_MS || "300000", 10)
const DEADLINE_STATE = process.env.GLUDD_TASK_DEADLINE_STATE || "/tmp/gludd-task-deadlines.json"
const WARNINGS_LOG = process.env.GLUDD_TASK_DEADLINE_WARNINGS || "/tmp/gludd-task-deadlines.warnings.log"
const DEADLINE_ENABLED = (process.env.GLUDD_TASK_DEADLINE_ENABLED || "1") !== "0"

// ============================================================================
// NOISE-CONTROL STATE
// ----------------------------------------------------------------------------
// warnedIds: in-memory Set of task ids that have ALREADY triggered a
// console.warn this session. Guarding the warn with this Set ensures each
// breached task surfaces to the UI at most ONCE; subsequent breaches for the
// same id go only to WARNINGS_LOG (the persistent channel). Cleared in
// tool.execute.after so a task_id reused in a later dispatch warns again.
// ============================================================================
const warnedIds = new Set<string>()

function appendWarning(line: string): void {
  try {
    fs.appendFileSync(WARNINGS_LOG, line + "\n")
  } catch { /* fail open — persistent log is best-effort */ }
}

// ============================================================================
// STATE FILE (atomic-ish read/write; fail-open on any IO error)
// Shape: { "<task_id>": <dispatch epoch ms>, ... }
// ============================================================================
function loadDeadlines(): Record<string, number> {
  try {
    const data = JSON.parse(fs.readFileSync(DEADLINE_STATE, "utf8"))
    const out = data && typeof data === "object" ? data as Record<string, number> : {}
    // TTL sweep: drop any entry older than TASK_TIMEOUT_MS * 3 (15 min default).
    // Prevents unbounded accumulation if a tool.execute.after ever fails to
    // delete its entry (mismatched id, missing args, hook error). Without this
    // sweep, a long session leaks entries that throttle-warn once and then sit
    // in the persistent file forever.
    sweepStaleEntries(out)
    return out
  } catch {
    return {}
  }
}

function sweepStaleEntries(d: Record<string, number>): void {
  const now = Date.now()
  const maxAge = TASK_TIMEOUT_MS * 3 // 15 min default; 3x the deadline window
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
  // Caller owns the save; sweep only mutates the in-memory dict.
  if (mutated) {
    try {
      const tmp = DEADLINE_STATE + ".tmp"
      fs.writeFileSync(tmp, JSON.stringify(d))
      fs.renameSync(tmp, DEADLINE_STATE)
    } catch { /* fail open */ }
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
    // Deterministic fallback: combine stable fields so tool.execute.before and
    // tool.execute.after produce the SAME id for the same dispatch. Without
    // this, both hooks see args without a task_id/id (opencode assigns its
    // own internal ses_... id elsewhere), before falls back to
    // `auto-${Date.now()}` (timestamp-based, different each call), and after
    // gets null → never deletes the entry → leak + repeated throttle warns.
    // djb2 hash of `${subagent_type}:${description}` gives a stable id for the
    // lifetime of one dispatch (both hooks receive the same args).
    const desc = typeof a.description === "string" ? a.description : ""
    const subtype = typeof a.subagent_type === "string" ? a.subagent_type : ""
    if (desc || subtype) {
      const raw = `${subtype}:${desc}`
      let hash = 5381 // djb2
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
            const line =
              `TASK DEADLINE EXCEEDED: task ${id} has been running for ${mins}min ` +
              `(limit ${limitMin}min). This task should have completed. The ` +
              `orchestrator should dispatch a replacement.`
            // Persistent channel — every breach is logged so the orchestrator
            // can poll via `make task-ttl-check` (CLI mirror reads the same
            // state file; this log is the audit trail).
            appendWarning(`${new Date().toISOString()} ${line}`)
            // UI channel — throttled to ONCE per task id per session so a
            // lingering breached task does not flood the user's terminal.
            // The orchestrator still gets the signal (one warn is enough to
            // trigger re-dispatch / re-split); the user UI stays readable.
            if (!warnedIds.has(id)) {
              warnedIds.add(id)
              // Advisory — plugins cannot hard-kill tasks. The orchestrator
              // reads its own console stream and acts (re-dispatch / re-split
              // / abandon).
              console.warn(line)
            }
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
        // Reset the UI-throttle gate so a future dispatch reusing this id
        // (e.g. resumed task via SendMessage) is allowed to warn again if it
        // also exceeds the deadline. The persistent WARNINGS_LOG is untouched.
        warnedIds.delete(id)
      } catch { /* fail open */ }
    },
  }
}) satisfies Plugin
