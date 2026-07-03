import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

// Floor+ceiling enforcement guardrail (separate from enforce-make.ts so a bug
// here can NEVER break the make-only enforcement). FAIL-OPEN: any error -> do
// nothing. It APPENDS a directive (never replaces a response), so it can't block
// a legitimate user-facing answer.
//
// It cannot perfectly count LIVE agents (the harness exposes no live count; a
// just-finished agent's .output looks like a running one). So it uses a SHORT
// activity window (a streaming agent appends to its .output within seconds) which
// UNDER-counts thinking agents — deliberately, so the bias is "warn/over-dispatch"
// rather than "miss a dip".
//
// Three bands (user directive 2026-06-16):
//   active <  FLOOR    -> FLOOR BREACH: dispatch (async, disjoint) up to ~TARGET.
//   active >  CEILING  -> CEILING BREACH: stop dispatching; let agents drain.
//   else               -> healthy band: nothing appended.
// The model is also reminded to DELEGATE-FIRST (start work by deploying agents,
// not by doing it inline) and to dispatch ASYNC — never block the main thread on
// a subagent (no blocking TaskOutput / no waiting loop).
//
// IMPORTANT — the floor is a floor on USEFUL PARALLELISM, not a mandate to invent
// ceremony. Fill it with READ-ONLY proposer agents (Read/Grep/Glob — they return
// findings or exact old_string/new_string patches as TEXT) which incur NO worktree,
// NO merge, and NO cleanup. Reserve `isolation:"worktree"` (which DOES tax you with
// wt-sync/wt-apply + clean-worktree-venvs) for genuine CONCURRENT FILE MUTATION that
// would otherwise conflict — never just to pad the count. And when an agent's output
// is provably complete + correct, apply it DIRECTLY (Edit as single writer); do not
// route a trivially-correct change through merge ceremony. Holding the floor must
// never force merge/worktree-cleanup work onto an already-finished task.

// Live overrides (parallel to the shell hooks): a valid integer in
// /tmp/gludd-floor-override / /tmp/gludd-ceiling-override wins over the env
// default, so the operator can retune floor/ceiling mid-session without a
// restart. FAIL-OPEN: unreadable/non-numeric file -> keep the env default.
function _tunable(overridePath: string, envVar: string, dflt: string): number {
  let base = parseInt(process.env[envVar] || dflt, 10)
  try {
    const raw = fs.readFileSync(overridePath, "utf8").trim()
    if (/^\d+$/.test(raw)) base = parseInt(raw, 10)
  } catch { /* no override file -> env/default */ }
  return base
}
const FLOOR = _tunable("/tmp/gludd-floor-override", "CLAUDE_AGENT_FLOOR", "10")
const CEILING = _tunable("/tmp/gludd-ceiling-override", "CLAUDE_AGENT_CEILING", "16")
const TARGET = Math.min(
  parseInt(process.env.CLAUDE_AGENT_TARGET || "14", 10),
  CEILING,
)

// BLOCKING mode (2026-06-28 user directive): the floor breach was advisory-only
// by default, which let the agent serialize work. Per AGENTS.md "Guardrail
// Integrity Policy" the fix is to make the guardrail SMARTER (block when
// warranted), not delete it. The plugin BLOCKS non-dispatch tool calls (via
// tool.execute.before) when the floor is breached AND there is known open work
// — forcing the agent to dispatch subagents rather than grind inline.
// DEFAULT ON (continuous multitasking enforcement). Set GLUDD_FLOOR_ENFORCE=0
// to disable the hard gate (back to advisory-only append). Mirrors
// GLUDD_NO_WAIT_ENFORCE in enforce-stop.ts.
const FLOOR_ENFORCE = process.env.GLUDD_FLOOR_ENFORCE !== "0"

// Dispatch tools are ALWAYS allowed — even when the floor is breached and
// enforce mode is on. Blocking a dispatch attempt would be counterproductive
// (the whole point is to force MORE dispatches). This helper is the load-bearing
// exemption that keeps the block from wedging the session.
function isDispatchTool(tool: string): boolean {
  return tool === "task" || tool === "agent" || tool === "workflow"
}

// Open-work probe: only block when the repo actually has pending work. Avoids
// wedging a session where the floor is breached because the work is genuinely
// done. Signals (any one triggers "open work"):
//   - ratchet.yml entries
//   - the multitasking backlog file present
//   - uncommitted git changes
//   - a live todowrite list with pending/in_progress items (state file mirror
//     at /tmp/gludd-todowrite-state.json written by enforce-todos.ts)
//   - unchecked markdown task rows in TASKS.md (`- [ ]`, `* [ ]`)
//   - open incident headers in BUGS.md
// The TASKS.md/BUGS.md scans close the gap where the agent has unchecked
// task-ledger rows but a clean git tree, so the floor gate no longer silently
// disables. Paths are configurable via GLUDD_TASKS_MD / GLUDD_BUGS_MD env vars
// (default <cwd>/TASKS.md / <cwd>/BUGS.md).
// FAIL-OPEN: any error -> false (don't block on a probe bug).
function openWorkExists(): boolean {
  try {
    const ratchet = path.join(process.cwd(), "config", "ratchet.yml")
    if (fs.existsSync(ratchet)) {
      const entries = fs.readFileSync(ratchet, "utf8")
        .split("\n")
        .filter(l => l.trim() && !l.trim().startsWith("#") && l.includes(":"))
      if (entries.length > 0) return true
    }
    const backlog = path.join(process.cwd(), "scripts", "multitasking_backlog.json")
    if (fs.existsSync(backlog)) return true
    // Todowrite state mirror — written by enforce-todos.ts whenever the agent
    // carries pending/in_progress items. Best-effort: absent file => no signal.
    const todoState = process.env.GLUDD_TODOWRITE_STATE || "/tmp/gludd-todowrite-state.json"
    try {
      if (fs.existsSync(todoState)) {
        const todos = JSON.parse(fs.readFileSync(todoState, "utf8"))
        if (Array.isArray(todos)) {
          const hasPending = todos.some((t: any) =>
            (t.status === "pending" || t.status === "in_progress"))
          if (hasPending) return true
        }
      }
    } catch { /* malformed mirror -> ignore */ }
    // TASKS.md scan — count unchecked markdown task rows (`- [ ]` or `* [ ]`).
    // Configurable via GLUDD_TASKS_MD (default <cwd>/TASKS.md).
    const tasksMd = process.env.GLUDD_TASKS_MD || path.join(process.cwd(), "TASKS.md")
    try {
      if (fs.existsSync(tasksMd)) {
        const tasksSrc = fs.readFileSync(tasksMd, "utf8")
        // Match `^\s*[-*]\s+\[\s*\]` — a list marker followed by an empty
        // (unchecked) markdown task box. `- [x]` / `- [X]` do NOT match.
        const unchecked = tasksSrc
          .split("\n")
          .filter(l => /^\s*[-*]\s+\[\s*\]/.test(l))
        if (unchecked.length > 0) return true
      }
    } catch { /* unreadable TASKS.md -> ignore */ }
    // BUGS.md scan — count open incident headers (date-stamped `### YYYY-MM-DD —`
    // rows that are not marked resolved). Configurable via GLUDD_BUGS_MD.
    // Matches both em-dash (— U+2014) and hyphen (- U+002D) separators.
    const bugsMd = process.env.GLUDD_BUGS_MD || path.join(process.cwd(), "BUGS.md")
    try {
      if (fs.existsSync(bugsMd)) {
        const bugsSrc = fs.readFileSync(bugsMd, "utf8")
        const openIncidents = bugsSrc
          .split("\n")
          .filter(l => /^###\s+\d{4}-\d{2}-\d{2}\s+[-—]/.test(l))
          .filter(l => !/\b(resolved|fixed|closed|wontfix|duplicate)\b/i.test(l))
        if (openIncidents.length > 0) return true
      }
    } catch { /* unreadable BUGS.md -> ignore */ }
    // Check for uncommitted changes via .git/index vs .git/refs/heads mtime
    // — avoids the execSync("git status") dependency that may not work in
    // plugin sandboxes where `git` is not on PATH.
    try {
      const index = path.join(process.cwd(), ".git", "index")
      const headRef = path.join(process.cwd(), ".git", "refs", "heads", "master")
      if (fs.existsSync(index) && fs.existsSync(headRef)) {
        const idxMtime = fs.statSync(index).mtimeMs
        const refMtime = fs.statSync(headRef).mtimeMs
        if (Math.abs(idxMtime - refMtime) > 2000) return true
      }
    } catch { /* ignore */ }
    return false
  } catch {
    return false
  }
}

// --- In-memory streak counter (replaces Python-shell-out countActiveAgents) ---
// The old `countActiveAgents()` called execSync("python3 ... agent_liveness.py")
// which was slow (~5s), fragile (Python not on plugin-runtime PATH), and silently
// failed, making the floor enforcement dead code. The replacement tracks
// consecutive non-dispatch calls right here — zero external dependencies.
// Dispatch (task/agent/workflow) resets the streak to 0.
// After MAX_STREAK consecutive non-dispatch calls with open work → DENY.
const MAX_STREAK = 2
let _streakCount = 0
const floorTurnState: { accumulatedText: string } = { accumulatedText: "" }

export default (async ({ }) => {
  return {
    "tool.execute.before": async (input: { tool?: string }, _output: unknown) => {
      try {
        if (!FLOOR_ENFORCE) return
        const tool = (input?.tool ?? "") as string
        if (isDispatchTool(tool)) {
          _streakCount = 0
          return
        }
        _streakCount++
        if (_streakCount <= MAX_STREAK) return
        if (!openWorkExists()) {
          _streakCount = 0
          return
        }
        return {
          permissionDecision: "deny" as const,
          message: [
            `⛔ FLOOR BREACH: ${_streakCount} consecutive non-dispatch calls (> max ${MAX_STREAK}).`,
            `Floor=${FLOOR}, target=${TARGET}. Dispatch task/agent/workflow NOW.`,
            "Set GLUDD_FLOOR_ENFORCE=0 to disable.",
          ].join("\n"),
        }
      } catch {
        return
      }
    },

    "session.idle": async () => {
      floorTurnState.accumulatedText = ""
    },

    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      try {
        if (!output || typeof output.text !== "string") return output
        floorTurnState.accumulatedText += output.text
        if (_streakCount > MAX_STREAK) {
          return {
            text: [
              output.text,
              "",
              "",
              "⛔ AGENT-FLOOR BREACH (auto-injected guardrail) ⛔",
              `${_streakCount} non-dispatch calls without a dispatch — floor=${FLOOR}, target=${TARGET}.`,
              "DELEGATE-FIRST: your VERY NEXT action MUST dispatch task/agent/workflow.",
              "Do not deliberate; dispatch.",
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
