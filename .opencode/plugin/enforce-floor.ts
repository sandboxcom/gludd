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

const FLOOR = parseInt(process.env.CLAUDE_AGENT_FLOOR || "10", 10)
const TARGET = parseInt(process.env.CLAUDE_AGENT_TARGET || "14", 10)
const CEILING = parseInt(process.env.CLAUDE_AGENT_CEILING || "16", 10)

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
// done. Signals (any one triggers "open work"): ratchet.yml entries, the
// multitasking backlog file present, or uncommitted git changes.
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
    const { execSync } = require("node:child_process")
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
    const { execSync } = require("node:child_process")
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
        if (isDispatchTool(tool)) return  // never block dispatch
        const active = countActiveAgents()
        if (active === null) return       // can't tell -> fail open
        if (active >= FLOOR) return        // not below floor -> allow
        if (!openWorkExists()) return      // no pending work -> allow
        const need = Math.max(0, TARGET - active)
        return {
          permissionDecision: "deny" as const,
          message: [
            "⛔ TOOL DENIED — AGENT-FLOOR BREACH (default-enforced).",
            `Only ~${active} agents are streaming; floor=${FLOOR}, target=${TARGET}.`,
            "Open work is pending (ratchet/backlog/uncommitted changes). This non-dispatch",
            `tool ('${tool}') is BLOCKED until the floor is refilled. DISPATCH ≥ ${need}`,
            "more subagents via task/agent/workflow on DISJOINT work NOW — those dispatch",
            "tools are explicitly ALLOWED (never blocked). After refilling toward target,",
            "resume this step.",
          ].join("\n"),
        }
      } catch {
        return  // fail open — never wedge the session on a guardrail bug
      }
    },

    "experimental.chat.response.transform": async (_input: unknown, output: unknown) => {
      try {
        if (typeof output !== "string") return output
        const active = countActiveAgents()
        if (active === null) return output // can't tell -> fail open

        if (active < FLOOR) {
          // Below floor: append a hard, un-ignorable directive (do NOT replace —
          // the user-facing content still ships, but the next action is forced).
          return [
            output,
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
          ].join("\n")
        }

        if (active > CEILING) {
          // Above ceiling: warn so the model STOPS adding agents (disk/overload
          // pressure — see disk-discipline memory) and lets the wave drain.
          return [
            output,
            "",
            "",
            "⚠️ AGENT-CEILING BREACH (auto-injected guardrail) ⚠️",
            `~${active} agents are streaming; ceiling=${CEILING} (target=${TARGET}).`,
            "Do NOT dispatch more subagents right now — let the in-flight wave drain back",
            `toward ~${TARGET} first (over-provisioning risks disk ENOSPC + API overload).`,
            "Keep working the main thread's own step; the floor guardrail will prompt you",
            "again only if the count later dips below the floor.",
          ].join("\n")
        }

        return output // healthy band -> nothing
      } catch {
        return output // fail open — never block on a guardrail bug
      }
    },
  }
}) satisfies Plugin
