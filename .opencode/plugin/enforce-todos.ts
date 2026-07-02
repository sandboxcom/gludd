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

// ============================================================================
// UNTRACKED-DELIVERABLE DETECTION (Gap 1, 2, 3)
// The original guardrail checked ONLY todowrite state. The 2026-06-29 audit
// proved that was insufficient: 14 subagent-produced files accumulated
// untracked in the working tree without ever being committed, and the
// guardrail never fired because none of them were tracked in todowrite. These
// helpers close that hole by directly inspecting the working tree.
// ============================================================================

const DELIVERABLE_PATTERNS: readonly RegExp[] = [
  /^tests\/.*\.py$/,
  /^src\/.*\.(py|ts|tsx|js)$/,
  /^docs\/design\/.*\.md$/,
  /^collections\/.*\/modules\/.*\.py$/,
  /^collections\/.*\/roles\/[^/]+\/main\.yml$/,
  /^alembic\/versions\/.*\.py$/,
  /^\.opencode\/plugin\/.*\.ts$/,
  /^infra\/.*\.rego$/,
  /^infra\/.*\.tf$/,
  /^playbooks\/.*\.yml$/,
  /^molecule\/.*\.yml$/,
]

const UNTRACKED_DELIVERABLE_THRESHOLD = 2

interface GitStatus {
  untrackedDeliverables: string[]
  untrackedTests: string[]
  trackedFiles: Set<string>
}

function probeWorkingTree(): GitStatus {
  const empty: GitStatus = {
    untrackedDeliverables: [],
    untrackedTests: [],
    trackedFiles: new Set(),
  }
  try {
    const { execSync } = require("node:child_process")
    const cwd = process.cwd()

    let porcelain = ""
    try {
      porcelain = execSync("git status --porcelain", {
        cwd,
        encoding: "utf8",
        timeout: 3000,
        stdio: ["pipe", "pipe", "pipe"],
      })
    } catch {
      return empty
    }

    const untrackedDeliverables: string[] = []
    const untrackedTests: string[] = []
    for (const line of porcelain.split(/\r?\n/)) {
      if (!line.startsWith("??")) continue
      const raw = line.slice(2).trim()
      const p = raw.startsWith('"') && raw.endsWith('"')
        ? raw.slice(1, -1)
        : raw
      if (DELIVERABLE_PATTERNS.some(re => re.test(p))) {
        untrackedDeliverables.push(p)
      }
      if (/^tests\/unit\/test_.*\.py$/.test(p)) {
        untrackedTests.push(p)
      }
    }

    let tracked = ""
    try {
      tracked = execSync("git ls-files", {
        cwd,
        encoding: "utf8",
        timeout: 3000,
        stdio: ["pipe", "pipe", "pipe"],
      })
    } catch {
      tracked = porcelain
        .split(/\r?\n/)
        .filter(l => l && !l.startsWith("??"))
        .map(l => l.slice(3).trim().replace(/^"|"$/g, ""))
        .join("\n")
    }
    const trackedFiles = new Set(
      tracked.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
    )

    return { untrackedDeliverables, untrackedTests, trackedFiles }
  } catch {
    return empty
  }
}

function sourceCandidatesForTest(testPath: string): string[] {
  const base = testPath.split("/").pop() ?? testPath
  const m = base.match(/^test_(.+)\.py$/)
  if (!m) return []
  const stem = m[1]
  return [`src/${stem}.py`, stem + ".py"]
}

function findOrphanedTests(status: GitStatus): string[] {
  const orphans: string[] = []
  for (const testPath of status.untrackedTests) {
    const candidates = sourceCandidatesForTest(testPath)
    const hasCommittedSource = candidates.some(c => {
      if (status.trackedFiles.has(c)) return true
      const stem = candidates[0]?.split("/").pop() ?? ""
      if (!stem) return false
      for (const tracked of status.trackedFiles) {
        if (tracked.split("/").pop() === stem) return true
      }
      return false
    })
    if (hasCommittedSource) orphans.push(testPath)
  }
  return orphans
}

// ============================================================================
// FREQUENCY CAP (Gap 4) — suppresses refiring within 30s per directive type.
// ============================================================================

const FREQ_CAP_PATH =
  process.env.GLUDD_NOTHING_DROPPED_FREQ_FILE ||
  "/tmp/gludd-nothing-dropped-last-fired.json"
const FREQ_CAP_WINDOW_MS = 30_000

type DirectiveType =
  | "untracked-deliverables"
  | "post-wave-sweep"
  | "orphaned-test"

function readFreqState(): Record<string, number> {
  try {
    if (!fs.existsSync(FREQ_CAP_PATH)) return {}
    const raw = fs.readFileSync(FREQ_CAP_PATH, "utf8")
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const out: Record<string, number> = {}
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
        if (typeof v === "number") out[k] = v
      }
      return out
    }
    return {}
  } catch {
    return {}
  }
}

function freqCapAllows(type: DirectiveType): boolean {
  const state = readFreqState()
  const last = state[type]
  if (typeof last !== "number") return true
  return Date.now() - last >= FREQ_CAP_WINDOW_MS
}

function recordFire(type: DirectiveType): void {
  try {
    const state = readFreqState()
    state[type] = Date.now()
    fs.writeFileSync(FREQ_CAP_PATH, JSON.stringify(state), "utf8")
  } catch {
    // best-effort
  }
}

// Gap #4 fix: when the post-wave-sweep directive fires (REPLACE severity),
// reset ALL other directive caps so the next response gets full scrutiny.
// The 30s per-directive cap can otherwise mask a repeat breach immediately
// following a replace-grade one.
function resetOtherDirectiveCaps(firedType: DirectiveType): void {
  try {
    const state = readFreqState()
    let mutated = false
    for (const key of Object.keys(state)) {
      if (key !== firedType) {
        delete state[key]
        mutated = true
      }
    }
    if (mutated) {
      fs.writeFileSync(FREQ_CAP_PATH, JSON.stringify(state), "utf8")
    }
  } catch {
    // best-effort
  }
}

const DISPATCH_RESULT_MARKERS = [
  "<task_result>",
  "<task_id>",
  "subagent result",
  "wave",
  "parallel task",
]

function responseMentionsDispatchResults(text: string): boolean {
  const lower = text.toLowerCase()
  return DISPATCH_RESULT_MARKERS.some(m => lower.includes(m))
}

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
// (response.transform migrated to text.complete below, 2026-07-01)

const todoTurnState: { accumulatedText: string } = {
  accumulatedText: "",
}

function classifyAndBlock(text: string): string | null {
  const directives: string[] = []

  // --- Original todowrite-state check (preserved) -------------------
  const pending = readPendingTodos()
  const isSummary = responseLooksLikeSummary(text)
  if (pending.length > 0 && isSummary) {
    directives.push([
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
    ].join("\n"))
  }

  // --- Gap 1 + 2 + 3: working-tree deliverable detection ------------
  const status = probeWorkingTree()

  if (
    isSummary &&
    status.untrackedDeliverables.length >= UNTRACKED_DELIVERABLE_THRESHOLD &&
    freqCapAllows("untracked-deliverables")
  ) {
    recordFire("untracked-deliverables")
    directives.push([
      "",
      "",
      "⛔ UNTRACKED-DELIVERABLES GUARDRAIL: " + status.untrackedDeliverables.length,
      "untracked deliverable files have accumulated (tests/, src/, docs/,",
      "collections/, etc.). These were produced by subagents but never",
      "committed. Recover them NOW — review `make git-status`, run `make",
      "collect-check` on each, then commit. The pattern of 'produce ->",
      "summarise -> drop' is the bug this guardrail exists to prevent.",
      "",
      "Untracked deliverables (first 8):",
      ...status.untrackedDeliverables.slice(0, 8).map((p, i) => "  " + (i + 1) + ". " + p),
      status.untrackedDeliverables.length > 8
        ? "  ... (+" + (status.untrackedDeliverables.length - 8) + " more)"
        : "",
    ].join("\n"))
  }

  const mentionsDispatch = responseMentionsDispatchResults(text)
  const sweepUntracked = status.untrackedDeliverables
  if (
    mentionsDispatch &&
    sweepUntracked.length >= 2 &&
    freqCapAllows("post-wave-sweep")
  ) {
    recordFire("post-wave-sweep")
    resetOtherDirectiveCaps("post-wave-sweep")
    // REPLACE severity — return the directive as the full replacement text
    return [
      "⛔ NOTHING-DROPPED GUARDRAIL — POST-WAVE SWEEP REQUIRED",
      "",
      "Your response mentions parallel subagent results, but:",
      "- " + sweepUntracked.length + " untracked deliverable file(s) have accumulated.",
      "- The summary above is being REPLACED by this directive.",
      "",
      "The 'dispatch N agents → get N results → write summary' pattern is the bug.",
      "The summary itself is NOT the deliverable.",
      "",
      "Recover NOW:",
      "1. Run `make git-status` to see what's untracked.",
      "2. Run `make collect-check` on each untracked deliverable to verify it's complete.",
      "3. Commit each coherent group via `make commit-ci-gate MSG='...'`.",
      "4. After all deliverables are committed, resume new work.",
      "",
      "Untracked deliverables (" + sweepUntracked.length + "):",
      ...sweepUntracked.slice(0, 10).map((p, i) => "  " + (i + 1) + ". " + p),
      sweepUntracked.length > 10
        ? "  ... (+" + (sweepUntracked.length - 10) + " more)"
        : "",
    ].join("\n")
  }

  if (
    mentionsDispatch &&
    sweepUntracked.length === 1 &&
    freqCapAllows("post-wave-sweep")
  ) {
    recordFire("post-wave-sweep")
    directives.push([
      "",
      "",
      "⛔ POST-WAVE COMMIT SWEEP: you dispatched parallel tasks and got",
      "results. 1 deliverable file remains uncommitted. Before any other",
      "work, commit it. Recovery is the deliverable now — not new work.",
      "",
      "Uncommitted deliverable:",
      "  1. " + sweepUntracked[0],
    ].join("\n"))
  }

  const orphans = findOrphanedTests(status)
  if (orphans.length > 0 && freqCapAllows("orphaned-test")) {
    recordFire("orphaned-test")
    directives.push([
      "",
      "",
      "⛔ ORPHANED-TEST GUARDRAIL: " + orphans.length + " test file(s) are",
      "untracked while their corresponding source files are committed.",
      "The tests were dropped. Commit them now.",
      "",
      "Orphaned tests:",
      ...orphans.slice(0, 8).map((p, i) => "  " + (i + 1) + ". " + p),
    ].join("\n"))
  }

  if (directives.length === 0) return null
  return text + "\n" + directives.join("\n")
}

export default (async () => {
  return {
    "session.idle": async () => {
      todoTurnState.accumulatedText = ""
    },

    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      try {
        if (typeof output?.text !== "string") return output
        todoTurnState.accumulatedText += output.text
        const result = classifyAndBlock(todoTurnState.accumulatedText)
        if (result !== null) {
          output.text = result
        }
        return output
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
