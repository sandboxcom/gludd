import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { execSync } from "node:child_process"

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
    const bugsMd = process.env.GLUDD_BUGS_MD || path.join(process.cwd(), "BUGS.md")
    try {
      if (fs.existsSync(bugsMd)) {
        const bugsSrc = fs.readFileSync(bugsMd, "utf8")
        const openIncidents = bugsSrc
          .split("\n")
          .filter(l => /^###\s+\d{4}-\d{2}-\d{2}\s+—/.test(l))
          .filter(l => !/\b(resolved|fixed|closed|wontfix|duplicate)\b/i.test(l))
        if (openIncidents.length > 0) return true
      }
    } catch { /* unreadable BUGS.md -> ignore */ }
    const status = execSync("git status --porcelain", {
      cwd: process.cwd(),
      encoding: "utf8",
      timeout: 3000,
    })
    if (status.trim()) return true
    return false
  } catch {
    return false
  }
}

// Count live agents via the SAME ground-truth probe the shell hooks use
// (scripts/agent_liveness.py), so the plugin and the hooks can never disagree.
// The old fs-mtime heuristic here used a 45s window while the probe uses a
// PROBE+TAIL (now 12s) "grew-recently" check — they reported OPPOSITE signals
// for a just-completed wave. Shelling out to the probe unifies the semantics.
// FAIL-OPEN: any error (probe missing, timeout, non-numeric) -> null (skip).
function countActiveAgents(): number | null {
  try {
    const out = execSync(
      "python3 " + path.join(process.cwd(), "scripts", "agent_liveness.py") + " --count",
      {
        timeout: 5000,
        cwd: process.cwd(),
        encoding: "utf8",
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env, FLOOR_PROBE_SECS: "0.6", FLOOR_TAIL_SECS: "12.0" },
      },
    )
    const n = parseInt(String(out).trim(), 10)
    return Number.isNaN(n) ? null : n
  } catch {
    return null
  }
}
const floorTurnState: { accumulatedText: string } = { accumulatedText: "" }

export default (async ({ }) => {
  return {
    // --- BLOCKING mode (default ON; GLUDD_FLOOR_ENFORCE=0 disables): deny
    // non-dispatch tools when the floor is breached AND open work exists.
    // Dispatch tools (task/agent/workflow) are always ALLOWED — never block the
    // agent from refilling the pool. Returns a permissionDecision:"deny" so
    // opencode surfaces it as a blocked tool call, not a silent append. ---
    "tool.execute.before": async (input: { tool?: string }, _output: unknown) => {
      try {
        if (!FLOOR_ENFORCE) return
        const tool = (input?.tool ?? "") as string
        const isDispatch = isDispatchTool(tool)
        const active = countActiveAgents()

        // --- CEILING BREACH (deny on dispatch, warn on read-only ops) ---
        // When live count > CEILING we MUST stop adding worktree-isolated
        // agents (each creates a ~320MB venv -> disk exhaustion risk). A
        // hard deny on dispatch is the load-bearing fix; the old code only
        // appended a warning and disk proceeded unchecked. Read-only ops
        // (Read/Edit/Grep/etc.) add NO venv so they may proceed with a
        // warning. FAIL-OPEN for the ceiling: when active is null (probe
        // error) we do NOT deny — blocking ALL dispatches could wedge the
        // session, which is worse than the rare over-dispatch.
        if (active !== null && active > CEILING && isDispatch) {
          return {
            permissionDecision: "deny" as const,
            message: [
              `⛔ AGENT CEILING BREACHED: live subagent count ${active} > ceiling ${CEILING}.`,
              "Dispatching more agents risks disk exhaustion (each worktree-isolated",
              "agent creates a ~320MB venv). Run `make clean-worktree-venvs` first,",
              "or wait for in-flight agents to complete. Set GLUDD_FLOOR_ENFORCE=0",
              "to disable both floor AND ceiling enforcement.",
            ].join("\n"),
          }
        }

        // --- FLOOR BREACH (fail-closed: treat null probe as 0) ---
        // When the agent_liveness.py probe errored (killed, missing, etc.)
        // countActiveAgents returns null. For the FLOOR check we treat null
        // as 0 (fail-closed → assume no agents live → deny mutating tools,
        // forcing the agent to dispatch). This is asymmetric with the
        // ceiling (which is fail-open) by design: a missing probe is much
        // more likely to indicate zero observable agents than an invisible
        // herd over the ceiling. Dispatch tools are ALWAYS allowed so the
        // agent can refill the pool.
        if (isDispatch) return  // never block a dispatch on the floor path
        const floorActive = active === null ? 0 : active
        if (floorActive >= FLOOR) return   // not below floor -> allow
        if (!openWorkExists()) return      // no pending work -> allow
        const need = Math.max(0, TARGET - floorActive)
        return {
          permissionDecision: "deny" as const,
          message: [
            `Live subagent count is ${floorActive} (< floor ${FLOOR}). Dispatch a wave`,
            "BEFORE continuing inline work. Set GLUDD_FLOOR_ENFORCE=0 to disable.",
            "",
            `(Need ~${need} more dispatch(es) to reach target ${TARGET}. Dispatch tools`,
            "task/agent/workflow are explicitly ALLOWED — never blocked. Open work is",
            "pending: ratchet/backlog/todowrite/TASKS.md/BUGS.md/uncommitted. Refill",
            "the pool, then resume.)",
          ].join("\n"),
        }
      } catch {
        return  // fail open — never wedge the session on a guardrail bug
      }
    },

    "session.idle": async () => {
      floorTurnState.accumulatedText = ""
    },

    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      try {
        if (!output || typeof output.text !== "string") return output
        floorTurnState.accumulatedText += output.text
        const active = countActiveAgents()
        if (active === null) return output // can't tell -> fail open

        if (active < FLOOR) {
          return {
            text: [
              output.text,
              "",
              "",
              "⛔ AGENT-FLOOR BREACH (auto-injected guardrail) ⛔",
              `Only ~${active} agents are actively streaming; floor=${FLOOR}, target=${TARGET}, ceiling=${CEILING}.`,
              "DELEGATE-FIRST: don't do the work inline — your VERY NEXT action MUST be Agent",
              `dispatch tool calls to bring the count to ~${TARGET} on DISJOINT work, BEFORE any`,
              "further integration, gating, or analysis. Dispatch them ASYNC (background) and",
              "CONTINUE — never block the main thread waiting on a subagent (no blocking",
              "TaskOutput, no wait-loop). Re-dispatch any agent that died (e.g. 'API Error:",
              "Overloaded').",
              "Fill the floor with READ-ONLY proposer agents (no worktree/merge/cleanup tax).",
              "Use worktree isolation ONLY for genuine concurrent file mutation — never to pad",
              "the count — and apply provably-correct output directly (no merge ceremony).",
              "Do not deliberate; dispatch.",
            ].join("\n"),
          }
        }

        if (active > CEILING) {
          return {
            text: [
              output.text,
              "",
              "",
              "⚠️ AGENT-CEILING BREACH (auto-injected guardrail) ⚠️",
              `~${active} agents are streaming; ceiling=${CEILING} (target=${TARGET}).`,
              "Do NOT dispatch more subagents right now — let the in-flight wave drain back",
              `toward ~${TARGET} first (over-provisioning risks disk ENOSPC + API overload).`,
              "Keep working the main thread's own step; the floor guardrail will prompt you",
              "again only if the count later dips below the floor.",
            ].join("\n"),
          }
        }

        return output // healthy band -> nothing
      } catch {
        return output // fail open — never block on a guardrail bug
      }
    },
  }
}) satisfies Plugin
