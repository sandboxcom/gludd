import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

// enforce-todos.ts — NOTHING-DROPPED GUARDRAIL.
//
// Prevents the recurring failure mode where the agent dispatches N parallel
// subagents, receives N results, then sends a text SUMMARY without codifying
// any of them — dropping the work. The pattern ("dispatch N -> summarize ->
// drop") was a repeated incident (2026-06-22 et seq): the agent treated the
// summary itself as the deliverable and never committed / ticked / cancelled
// the items, so the work evaporated at session end.
//
// Two enforcement layers:
//
//   1. experimental.chat.response.transform — when the active todowrite list
//      has pending/in_progress items AND the outgoing response looks like a
//      summary with no tool call, PREPEND a loud directive telling the agent
//      to resume work. Advisory (the response still ships) but un-ignorable.
//
//   2. tool.execute.before (gated by GLUDD_TODO_GUARD_ENFORCE, DEFAULT ON via
//      the `!== "0"` pattern) — when a commit-shaped make target runs while
//      pending todowrite items exist AND those items are neither referenced
//      in the commit message nor addressed by a staged TASKS.md update, DENY
//      the commit. The agent must either complete the items, cancel them with
//      a reason, or stage a TASKS.md update referencing each one.
//
// FAIL-OPEN: every hook returns silently / passes output through on any
// internal error (corrupt todo file, missing git, etc.). A guardrail bug must
// NEVER wedge the session.
//
// Opt-outs:
//   GLUDD_TODO_GUARD_ENFORCE=0  -> advisory-only (directive prepended, NO
//                                  commit block). Default is ON.
//   GLUDD_TODO_GUARD_BYPASS=1   -> emergency hotfix escape hatch. Skips the
//                                  commit block for a single commit. NEVER
//                                  the default.

// ============================================================================
// CONFIG
// ============================================================================

// DEFAULT ON: any value other than the literal "0" enables the hard commit
// gate. This is the canonical default-on pattern (matches GLUDD_FLOOR_ENFORCE
// and GLUDD_NO_WAIT_ENFORCE). A missing env var defaults to ON.
const TODO_GUARD_ENFORCE = process.env.GLUDD_TODO_GUARD_ENFORCE !== "0"

// Emergency single-commit bypass (hotfix escape hatch — never the default).
const TODO_GUARD_BYPASS = process.env.GLUDD_TODO_GUARD_BYPASS === "1"

// The commit-shaped make targets the gate recognizes. Any one of these with
// pending unrelated todos triggers the deny.
const COMMIT_TARGETS = [
  "git-commit",
  "commit-no-verify",
  "repo-commit",
  "ship-commit",
  "git-commit-file",
  "commit-bootstrap",
  "test-and-commit",
]

// Summary-style response heuristic. The directive fires only when the response
// LOOKS like a recap, not when the agent is mid-work (tool calls, make
// invocations, code blocks).
const SUMMARY_KEYWORDS = [
  "summary",
  "completed",
  "done",
  "results",
  "here's what",
  "here is what",
  "what i did",
  "what i changed",
  "landed",
  "shipped",
  "recap",
]

// ============================================================================
// TODO STATE PROBE
// Reads the opencode-persisted todowrite state. opencode does not document a
// stable path, so we check a list of candidates plus an env override. Returns
// the list of pending/in_progress items (empty list if none / unreadable).
// FAIL-OPEN: any error -> empty list (no directive, no commit block).
// ============================================================================

function todoStateCandidates(): string[] {
  const home = process.env.HOME || process.env.USERPROFILE || ""
  const cwd = process.cwd()
  const candidates: string[] = []
  if (process.env.GLUDD_TODO_STATE_FILE) {
    candidates.push(process.env.GLUDD_TODO_STATE_FILE)
  }
  candidates.push(path.join(home, ".local", "share", "opencode", "todos.json"))
  candidates.push(path.join(home, ".local", "share", "opencode", "todo.json"))
  candidates.push(path.join(home, ".local", "share", "opencode", "state", "todos.json"))
  candidates.push(path.join(cwd, ".opencode", "todos.json"))
  candidates.push(path.join(cwd, ".opencode", "state", "todos.json"))
  candidates.push(path.join(cwd, "todos.json"))
  return candidates
}

interface TodoItem {
  content?: string
  status?: string
  priority?: string
}

function readPendingTodos(): TodoItem[] {
  try {
    for (const candidate of todoStateCandidates()) {
      if (!fs.existsSync(candidate)) continue
      const raw = fs.readFileSync(candidate, "utf8")
      const parsed = JSON.parse(raw)
      const items: TodoItem[] = Array.isArray(parsed)
        ? parsed
        : Array.isArray(parsed?.todos)
          ? parsed.todos
          : Array.isArray(parsed?.items)
            ? parsed.items
            : []
      const pending = items.filter(it => {
        const s = String(it?.status ?? "").toLowerCase()
        return s === "pending" || s === "in_progress" || s === "in-progress"
      })
      return pending
    }
    return []
  } catch {
    return []
  }
}

// ============================================================================
// SUMMARY HEURISTIC
// Returns true when the outgoing response LOOKS like a recap and does NOT
// contain evidence of ongoing work (make invocation / tool dispatch).
// ============================================================================

function responseLooksLikeSummary(text: string): boolean {
  const lower = text.toLowerCase()
  if (/\bmake\s+\w/.test(lower)) return false
  if (/```(?:bash|sh|shell)\b/i.test(text)) return false

  const hasKeyword = SUMMARY_KEYWORDS.some(kw => lower.includes(kw))
  const bulletLines = text.split(/\r?\n/).filter(l => /^\s*([-*]|\d+\.)\s+\S/.test(l))
  const hasBullets = bulletLines.length >= 2
  const longRecap = text.length >= 120 && !/\?\s*$/.test(text)

  return hasKeyword || (hasBullets && longRecap)
}

// ============================================================================
// STAGED-CHANGES PROBE
// Returns true if TASKS.md appears in the staged diff. FAIL-OPEN -> false.
// ============================================================================

function stagedChangesIncludeTasksMd(): boolean {
  try {
    const { execSync } = require("node:child_process")
    const out = execSync("git diff --cached --name-only", {
      cwd: process.cwd(),
      encoding: "utf8",
      timeout: 3000,
      stdio: ["pipe", "pipe", "pipe"],
    })
    return /^TASKS\.md$/m.test(out.trim())
  } catch {
    return false
  }
}

function extractCommitMessage(makeCmd: string): string {
  const m = makeCmd.match(/\bMSG=(?:"([^"]*)"|'([^']*)'|(\S+))/)
  if (m) return (m[1] ?? m[2] ?? m[3] ?? "").toLowerCase()
  return ""
}

// ============================================================================
// PLUGIN
// ============================================================================
export default (async () => {
  return {
    "experimental.chat.response.transform": async (_input: unknown, output: unknown) => {
      try {
        if (typeof output !== "string") return output
        const pending = readPendingTodos()
        if (pending.length === 0) return output
        if (!responseLooksLikeSummary(output)) return output

        const directive = [
          "",
          "",
          "⛔ NOTHING-DROPPED GUARDRAIL: you have " + pending.length + " pending",
          "todowrite items but this response is a text summary with no tool call.",
          "Work is being dropped. RESUME WORK NOW — make a tool call that advances",
          "the next pending item, OR explicitly mark items completed/cancelled in",
          "todowrite with the reason.",
          "",
          "Pending items:",
          ...pending.slice(0, 8).map((it, i) =>
            "  " + (i + 1) + ". " + (it?.content ?? "(no content)") +
            " [" + (it?.status ?? "?") + "]",
          ),
          pending.length > 8 ? "  ... (+" + (pending.length - 8) + " more)" : "",
        ].join("\n")

        return output + "\n" + directive
      } catch {
        return output
      }
    },

    "tool.execute.before": async (input: { tool?: string }, output: unknown) => {
      try {
        if (!TODO_GUARD_ENFORCE) return
        if (TODO_GUARD_BYPASS) return
        if (input?.tool !== "bash") return

        const command = (output as { args?: { command?: string } })?.args?.command ?? ""
        const trimmed = typeof command === "string" ? command.trim() : ""
        if (!trimmed.startsWith("make ") && trimmed !== "make") return

        const m = trimmed.match(/^make\s+(\S+)/)
        const target = m ? m[1] : ""
        if (!COMMIT_TARGETS.includes(target)) return

        const pending = readPendingTodos()
        if (pending.length === 0) return

        if (stagedChangesIncludeTasksMd()) return
        const msg = extractCommitMessage(trimmed)
        if (msg) {
          const referenced = pending.some(it => {
            const tokens = String(it?.content ?? "")
              .toLowerCase()
              .split(/\W+/)
              .filter(t => t.length >= 4)
            return tokens.some(tok => tok && msg.includes(tok))
          })
          if (referenced) return
        }

        return {
          permissionDecision: "deny" as const,
          message: [
            "⛔ COMMIT BLOCKED (nothing-dropped guardrail): " + pending.length + " pending",
            "todowrite items are not addressed by this commit. The 'dispatch N ->",
            "summarize -> commit unrelated work' pattern drops the dispatched work.",
            "",
            "To proceed, do ONE of:",
            "  1. Complete each pending item (run its tests, wire its code) and",
            "     re-run the commit.",
            "  2. Mark items cancelled in todowrite WITH a reason (not silently",
            "     dropped), then re-run the commit.",
            "  3. Stage a TASKS.md update referencing each pending item:",
            "       make git-add FILES='TASKS.md'",
            "     then re-run the commit.",
            "  4. For an emergency hotfix only: GLUDD_TODO_GUARD_BYPASS=1 (never",
            "     the default).",
            "",
            "Pending items:",
            ...pending.slice(0, 8).map((it, i) =>
              "  " + (i + 1) + ". " + (it?.content ?? "(no content)") +
              " [" + (it?.status ?? "?") + "]",
            ),
          ].join("\n"),
        }
      } catch {
        return
      }
    },
  }
}) satisfies Plugin
