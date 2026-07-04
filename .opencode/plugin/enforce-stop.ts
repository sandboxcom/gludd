import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

const FLOOR = parseInt(process.env.CLAUDE_AGENT_FLOOR || "10", 10)

const STATE_FILE =
  process.env.GLUDD_STOP_STATE_FILE ||
  "/tmp/gludd-stop-state.json"

interface StopStateCache {
  ts: number
  ratchetEntries: number
  tasksMdUnchecked: boolean
  gateStatusRed: boolean
  repoPending: boolean
  backlogOpen: number
  backlogItems: string[]
  hasPendingWork: boolean
  hasLocalWork: boolean
  ciVerdictPendingOrRed: boolean
}

const turnState: { accumulatedText: string; blocked: boolean } = {
  accumulatedText: "",
  blocked: false,
}

interface CiVerdictCache {
  ts: number
  isPendingOrRed: boolean
}

let ciVerdictCache: CiVerdictCache | null = null

// ── QUESTION DENY ──────────────────────────────────────────────────────────

const QUESTION_DENY_REASON = [
  "BLOCKING QUESTION DENIED — user standing directive: never interrupt work to",
  "ask. DEFAULT TO ACTION: choose the most reasonable option yourself, state in",
  "one line the assumption you are making, and PROCEED. Do NOT re-attempt the",
  "question. For a genuinely destructive/irreversible external action, state",
  "the plan + the risk and proceed (or note it and continue with the safe",
  "default) rather than blocking — the user will redirect you if needed.",
  "Keep moving.",
].join(" ")

// ── STOP-LIKE TOOL DENY ────────────────────────────────────────────────────

const STOP_LIKE_TARGETS_RE = /^make\s+(git-commit|commit-no-verify|ship-commit|git-push-branch|git-push-branch-nv|git-push-sandboxcom|git-push-sandboxcom-main|git-push-master|git-tag-push|release-cut|release-promote|test-and-commit|repo-commit|feature-done|release-recut|release-branch-new|git-merge)(\s|$)/

function stopLikeDenyMessage(taskMd: boolean, ratchetEntries: number): string {
  return [
    "⛔ STOP-LIKE TOOL BLOCKED — PENDING WORK EXISTS:",
    `TASKS.md unchecked items: ${taskMd ? "yes" : "no"}`,
    `config/ratchet.yml entries: ${ratchetEntries}`,
    "",
    "You are trying to commit/push/release while the project still has",
    "known-unfinished work. This is the exact premature-stop pattern",
    "that BUGS.md records 20+ times — the agent declares completion",
    "while TASKS.md items remain unchecked or ratchet entries are active.",
    "",
    "Fix the pending work FIRST before committing/pushing:",
    "  1. Complete all unchecked TASKS.md items (implement, test, verify)",
    "  2. Burn all ratchet.yml entries (fix the test failures, re-run make gate)",
    "  3. Re-run this tool call after the pending work is addressed.",
    "",
    "Do NOT bypass this. Do NOT use repo-commit or commit-no-verify to",
    "dodge it — those are still stop-like. The work itself is the",
    "deliverable; the commit is just the recording of completed work.",
  ].join("\n")
}

// ── CI RED PATTERNS (5 entries max) ────────────────────────────────────────

const CI_RED_PATTERNS: RegExp[] = [
  /\bCI\s+is\s+(?:red|failing|broken|not green|down)\b/i,
  /\bCI\s+(?:run|job|pipeline|workflow)\s+(?:failed|is red|is failing)\b/i,
  /\bGitHub\s+Actions?\s+(?:is\s+)?(?:red|failing|failed)\b/i,
  /\b(?:gate|sandboxcom|Actions)\s+(?:is\s+)?(?:still\s+)?(?:red|failing|not green)\b/i,
  /\bCI\s+(?:still|remains?)\s+(?:red|failing)\b/i,
]

// ── TERMINAL RESPONSE DETECTOR (state-based) ───────────────────────────────

function responseLooksTerminal(text: string): boolean {
  if (/\|[^\n|]+\|[^\n|]+\|/.test(text)) return true
  if (/\b(?:DONE|COMPLETE)\b/.test(text)) return true
  if (text.length > 200 && !/\?\s*$/.test(text)) return true
  const checked = (text.match(/- \[[xX]\]/g) || []).length
  const unchecked = (text.match(/- \[ \]/g) || []).length
  if (checked >= 3 && unchecked === 0) return true
  if (/session\s+summary/i.test(text)) return true
  return false
}

function responseMentionsCiRed(text: string): boolean {
  return CI_RED_PATTERNS.some(p => p.test(text))
}

// ── STATE FUNCTIONS ────────────────────────────────────────────────────────

function ratchetHasEntries(): number {
  try {
    const ratchetPath = path.join(process.cwd(), "config", "ratchet.yml")
    if (!fs.existsSync(ratchetPath)) return 0
    const content = fs.readFileSync(ratchetPath, "utf8")
    const entries = content.split("\n").filter(
      l => l.trim() && !l.trim().startsWith("#") && l.includes(":")
    )
    return entries.length
  } catch {
    return 0
  }
}

function tasksMdHasUnchecked(): boolean {
  try {
    const tasksPath = path.join(process.cwd(), "TASKS.md")
    if (!fs.existsSync(tasksPath)) return false
    const content = fs.readFileSync(tasksPath, "utf8")
    return /-\s+\[\s*\]/.test(content) || /\*\s+\[\s*\][^xX]/i.test(content) || /\*\s+\[\s*\]/i.test(content)
  } catch {
    return false
  }
}

function gateStatusIsRed(): boolean {
  try {
    const gatePath = path.join(process.cwd(), ".gate-status")
    if (!fs.existsSync(gatePath)) return false
    const content = fs.readFileSync(gatePath, "utf8")
    const lines = content.split("\n")
    for (const line of lines) {
      if (line.startsWith("===")) continue
      if (/FAIL/.test(line)) return true
    }
    return false
  } catch {
    return false
  }
}

function repoHasPendingWork(): boolean {
  try {
    const { execSync } = require("node:child_process")
    const cwd = process.cwd()
    try {
      const unpushed = execSync("git log --oneline @{u}..HEAD", {
        cwd,
        encoding: "utf8",
        timeout: 3000,
        stdio: ["pipe", "pipe", "pipe"],
      })
      if (unpushed.trim().length > 0) return true
    } catch {
      // no upstream — fall through
    }
    try {
      const status = execSync("git status --porcelain", {
        cwd,
        encoding: "utf8",
        timeout: 3000,
        stdio: ["pipe", "pipe", "pipe"],
      })
      if (status.trim().length > 0) return true
    } catch {
      // not a git repo — fail open
    }
    return false
  } catch {
    return false
  }
}

function ciIsPendingOrRed(): boolean {
  const now = Date.now()
  if (ciVerdictCache && (now - ciVerdictCache.ts) < 60_000) {
    return ciVerdictCache.isPendingOrRed
  }
  try {
    const { execSync } = require("node:child_process")
    const cwd = process.cwd()
    try {
      const output = execSync("make ci-verdict BRANCH=master", {
        cwd,
        encoding: "utf8",
        timeout: 15000,
        stdio: ["pipe", "pipe", "pipe"],
      }).trim()
      const isGreen = /^CI GREEN:/m.test(output) && !/STALE RUN WARNING/i.test(output)
      ciVerdictCache = { ts: now, isPendingOrRed: !isGreen }
      return !isGreen
    } catch (e: any) {
      const output = (e?.stdout || e?.stderr || "").trim()
      if (output) {
        const isGreen = /^CI GREEN:/m.test(output) && !/STALE RUN WARNING/i.test(output)
        ciVerdictCache = { ts: now, isPendingOrRed: !isGreen }
        return !isGreen
      }
      ciVerdictCache = { ts: now, isPendingOrRed: true }
      return true
    }
  } catch {
    ciVerdictCache = null
    return false
  }
}

// ── PLUGIN ─────────────────────────────────────────────────────────────────

export default (async ({ }) => {
  return {
    // Session idle — reset turn state, warm CI cache, write state file
    event: async ({ event }: { event: { type: string } }) => {
      if (event.type === "session.idle") {
        try {
          turnState.accumulatedText = ""

          ciIsPendingOrRed()

          const ratchetCount = ratchetHasEntries()
          const tasksMdUnchecked = tasksMdHasUnchecked()
          const gateRed = gateStatusIsRed()
          const repoPending = repoHasPendingWork()
          const ciVerdictPendingOrRed = ciIsPendingOrRed()
          const hasLocalWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || gateRed
          const hasPendingWork = hasLocalWork || ciVerdictPendingOrRed

          const state: StopStateCache = {
            ts: Date.now(),
            ratchetEntries: ratchetCount,
            tasksMdUnchecked,
            gateStatusRed: gateRed,
            repoPending,
            backlogOpen: 0,
            backlogItems: [],
            hasPendingWork,
            hasLocalWork,
            ciVerdictPendingOrRed,
          }

          fs.writeFileSync(STATE_FILE, JSON.stringify(state), "utf8")
        } catch {
          // fail open
        }
      }
    },

    // Deny question tool + stop-like commits while pending work exists
    "tool.execute.before": async (input: any, output: any) => {
      try {
        if (turnState.blocked) {
          turnState.blocked = false
        }

        if (input.tool === "question") {
          throw new Error(QUESTION_DENY_REASON)
        }

        if (input.tool === "bash") {
          const args = (output as Record<string, unknown> | undefined)?.args as { command?: string } | undefined
          const command = typeof args?.command === "string" ? args.command.trim() : ""
          if (command.startsWith("make ") && STOP_LIKE_TARGETS_RE.test(command)) {
            let taskMd: boolean
            let ratchetCount: number
            try {
              if (fs.existsSync(STATE_FILE)) {
                const raw = fs.readFileSync(STATE_FILE, "utf8")
                const cache = JSON.parse(raw)
                taskMd = cache.tasksMdUnchecked ?? tasksMdHasUnchecked()
                ratchetCount = cache.ratchetEntries ?? ratchetHasEntries()
              } else {
                taskMd = tasksMdHasUnchecked()
                ratchetCount = ratchetHasEntries()
              }
            } catch {
              taskMd = tasksMdHasUnchecked()
              ratchetCount = ratchetHasEntries()
            }
            if (taskMd || ratchetCount > 0) {
              throw new Error(stopLikeDenyMessage(taskMd, ratchetCount))
            }
          }
        }
      } catch (e: any) {
        if (e instanceof Error && e.message.includes("BLOCKED")) throw e
        // fail open — non-block errors pass through
      }
    },

    // Inject orchestration context (lean — session-start is the heavy version)
    "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
      try {
        if (typeof output === "string") {
          return `[orchestration] make-only commits, floor ${FLOOR}, gate-background for long ops.\n\n${output}`
        }
        return output
      } catch {
        return output
      }
    },

    // State-based stop detection
    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      try {
        const text = output.text
        if (!text || text.trim().length === 0) return
        if (text.trim().length < 60) return

        turnState.accumulatedText += text

        let cache: StopStateCache | null = null
        try {
          if (fs.existsSync(STATE_FILE)) {
            const raw = fs.readFileSync(STATE_FILE, "utf8")
            cache = JSON.parse(raw)
          }
        } catch {
          // fail open — compute fresh below
        }

        if (!cache) {
          const ratchetCount = ratchetHasEntries()
          const tasksMdUnchecked = tasksMdHasUnchecked()
          const gateRed = gateStatusIsRed()
          const repoPending = repoHasPendingWork()
          const ciVerdictPendingOrRed = ciIsPendingOrRed()
          const hasLocalWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || gateRed
          const hasPendingWork = hasLocalWork || ciVerdictPendingOrRed
          cache = {
            ts: Date.now(),
            ratchetEntries: ratchetCount,
            tasksMdUnchecked,
            gateStatusRed: gateRed,
            repoPending,
            backlogOpen: 0,
            backlogItems: [],
            hasPendingWork,
            hasLocalWork,
            ciVerdictPendingOrRed,
          }
        }

        const repoPending = cache?.repoPending ?? repoHasPendingWork()
        const ratchetCount = cache.ratchetEntries
        const tasksMdUnchecked = cache?.tasksMdUnchecked ?? tasksMdHasUnchecked()
        const gateRed = cache?.gateStatusRed ?? gateStatusIsRed()
        const ciVerdictPendingOrRed = cache?.ciVerdictPendingOrRed ?? ciIsPendingOrRed()
        const ciRed = gateRed || responseMentionsCiRed(turnState.accumulatedText)
        const hasLocalWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || gateRed
        const hasAnyWork = hasLocalWork || ciVerdictPendingOrRed

        if (turnState.blocked) {
          output.text = ""
          return
        }

        // Only block when there is LOCAL work pending. CI-pending alone
        // (with no local work) is not a stop — the agent is waiting for
        // external validation and should be allowed to communicate progress.
        if (hasLocalWork && responseLooksTerminal(turnState.accumulatedText)) {
          output.text = [
            "HARD STOP — STATE-BASED BLOCK: your final response looks terminal",
            "(markdown table / DONE-COMPLETE banner / long body / all-checked checkboxes /",
            "session summary) while the project state reports known-unfinished work.",
            "",
            `TASKS.md unchecked: ${cache.tasksMdUnchecked ? "yes" : "no"}`,
            `gate-status red: ${gateRed ? "yes" : "no"}`,
            `ratchet entries remaining: ${cache.ratchetEntries}`,
            `repo pending (unpushed/uncommitted): ${cache.repoPending ? "yes" : "no"}`,
            `CI pending or red: ${ciVerdictPendingOrRed ? "yes" : "no"}`,
            "",
            "YOU ARE STOPPING PREMATURELY. Dispatch ≥5 subagents to continue",
            "fixing the pending items above. Do NOT rephrase — work.",
          ].join("\n")
          turnState.blocked = true
          return
        }

        // CI-pending-only case: don't block, but log awareness
        if (!hasLocalWork && ciVerdictPendingOrRed && responseLooksTerminal(turnState.accumulatedText)) {
          // CI is the only pending work — agent can communicate progress
          // but should not treat CI-pending as "done"
          turnState.blocked = false
          return
        }
      } catch {
        return
      }
    },
  }
}) satisfies Plugin
