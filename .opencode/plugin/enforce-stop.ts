import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

const FLOOR = parseInt(process.env.CLAUDE_AGENT_FLOOR || "10", 10)
const STOP_ENFORCE = process.env.GLUDD_STOP_ENFORCE !== "0"

const STATE_FILE = process.env.GLUDD_STOP_STATE_FILE || "/tmp/gludd-stop-state.json"
const BLOCK_REASON_FILE = "/tmp/gludd-block-reason.json"
const BLOCK_COUNTER_FILE = "/tmp/gludd-block-counter.json"

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
  healthScore: number
  watchdogDisengage: boolean
}

const turnState: { accumulatedText: string; blocked: boolean; toolCallMade: boolean } = {
  accumulatedText: "",
  blocked: false,
  toolCallMade: false,
}

interface CiVerdictCache {
  ts: number
  isPendingOrRed: boolean
}

let ciVerdictCache: CiVerdictCache | null = null

// ── BLOCK COUNTER (Item 6: false-positive cascade detection) ────────────────

interface BlockCounter {
  consecutiveBlocks: number
  totalBlocks: number
  lastBlockTs: number
  disengageUntil: number
}

function readBlockCounter(): BlockCounter {
  try {
    if (fs.existsSync(BLOCK_COUNTER_FILE)) {
      return JSON.parse(fs.readFileSync(BLOCK_COUNTER_FILE, "utf8"))
    }
  } catch {}
  return { consecutiveBlocks: 0, totalBlocks: 0, lastBlockTs: 0, disengageUntil: 0 }
}

function writeBlockCounter(c: BlockCounter): void {
  try { fs.writeFileSync(BLOCK_COUNTER_FILE, JSON.stringify(c), "utf8") } catch {}
}

function recordBlock(reason: string): void {
  const c = readBlockCounter()
  const now = Date.now()
  c.totalBlocks++
  c.lastBlockTs = now
  if (now - c.lastBlockTs < 120_000) c.consecutiveBlocks++
  else c.consecutiveBlocks = 1
  if (c.consecutiveBlocks >= 5) {
    c.disengageUntil = now + 300_000
    console.warn("FALSE-POSITIVE CASCADE: disengaging for 5 min")
  }
  writeBlockCounter(c)
  try {
    fs.writeFileSync(BLOCK_REASON_FILE, JSON.stringify({
      reason,
      consecutive: c.consecutiveBlocks,
      ts: now,
    }), "utf8")
  } catch {}
}

function isDisengaged(): boolean {
  const c = readBlockCounter()
  if (c.disengageUntil > Date.now()) return true
  return false
}

// ── QUESTION DENY ──────────────────────────────────────────────────────────

const QUESTION_DENY_REASON = [
  "BLOCKING QUESTION DENIED — user standing directive: never interrupt work to",
  "ask. DEFAULT TO ACTION: choose the most reasonable option yourself, state in",
  "one line the assumption you are making, and PROCEED.",
].join(" ")

// ── STOP-LIKE TOOL DENY ────────────────────────────────────────────────────

const STOP_LIKE_TARGETS_RE = /^make\s+(git-commit|commit-no-verify|ship-commit|git-push-branch|git-push-branch-nv|git-push-sandboxcom|git-push-sandboxcom-main|git-push-master|git-tag-push|release-cut|release-promote|test-and-commit|repo-commit|feature-done|release-recut|release-branch-new|git-merge)(\s|$)/

function stopLikeDenyMessage(taskMd: boolean, ratchetEntries: number): string {
  return [
    "STOP-LIKE TOOL BLOCKED — PENDING WORK EXISTS:",
    `TASKS.md unchecked: ${taskMd ? "yes" : "no"}, ratchet entries: ${ratchetEntries}`,
    "Fix pending work first, then retry.",
  ].join("\n")
}

// ── TERMINAL RESPONSE DETECTOR (Items 3-5: rewritten heuristics) ────────────

const FUTURE_TENSE = /\b(will|going to|plan to|next|remaining|shall|upcoming|todo)\b/i
const COMPLETION_VERBATIM = /\b(all done|everything is complete|ready for review|waiting for (your )?feedback|(this|now) is (truly )?done)\b/i
const TOOL_CALL_INTENT = /\b(make git-|dispatch|subagent|gludd |pytest |uv run)\b/
const EVIDENCE_TOKEN = /(?:commit|sha|hash)\s*[:=]?\s*[0-9a-f]{7,40}|\[[0-9a-f]{7,}\]|gate (?:green|PASS|ALL PASSED)/i

function responseLooksTerminal(text: string): boolean {
  if (!text || text.length < 60) return false

  // Item 5: tool-call intent negates stop detection
  if (TOOL_CALL_INTENT.test(text)) return false

  // Item 5: evidence tokens (commit hashes, gate PASS) negate — they show
  // the response is substantiating work, not stopping
  if (EVIDENCE_TOKEN.test(text)) return false

  // Completion verbatim phrases — the strongest stop signal
  if (COMPLETION_VERBATIM.test(text)) return true

  // Item 4: checked boxes only flag if no future-tense verbs present
  const checked = (text.match(/- \[[xX]\]/g) || []).length
  const unchecked = (text.match(/- \[ \]/g) || []).length
  if (checked >= 3 && unchecked === 0 && !FUTURE_TENSE.test(text)) return true

  // Tables with no evidence tokens and no tool-call intent
  if (/\|[^\n|]+\|[^\n|]+\|/.test(text) && !EVIDENCE_TOKEN.test(text)) return true

  // "session summary" without future-tense
  if (/session\s+summary/i.test(text) && !FUTURE_TENSE.test(text)) return true

  return false
}

// ── STATE FUNCTIONS ────────────────────────────────────────────────────────

function readSharedState(): StopStateCache | null {
  try {
    // Item 14: read watchdog's CI cache instead of running our own ci-verdict
    const ciCachePath = "/tmp/gludd-watchdog-ci.json"
    let ciFromWatchdog: boolean | null = null
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const lastCheck = ciData.last_ci_check || 0
      const lastStatus = ciData.last_ci_status || ""
      if (Date.now() - lastCheck < 120_000) {
        ciFromWatchdog = lastStatus !== "SUCCESS"
      }
    }

    if (fs.existsSync(STATE_FILE)) {
      const raw = fs.readFileSync(STATE_FILE, "utf8")
      const state = JSON.parse(raw)
      if (ciFromWatchdog !== null) state.ciVerdictPendingOrRed = ciFromWatchdog
      return state
    }
  } catch {}
  return null
}

function ratchetHasEntries(): number {
  try {
    const ratchetPath = path.join(process.cwd(), "config", "ratchet.yml")
    if (!fs.existsSync(ratchetPath)) return 0
    const content = fs.readFileSync(ratchetPath, "utf8")
    return content.split("\n").filter(
      l => l.trim() && !l.trim().startsWith("#") && l.includes(":")
    ).length
  } catch { return 0 }
}

function tasksMdHasUnchecked(): boolean {
  try {
    const tasksPath = path.join(process.cwd(), "TASKS.md")
    if (!fs.existsSync(tasksPath)) return false
    const content = fs.readFileSync(tasksPath, "utf8")
    return /-\s+\[\s*\]/.test(content) || /\*\s+\[\s*\][^xX]/i.test(content)
  } catch { return false }
}

function gateStatusIsRed(): boolean {
  try {
    const gatePath = path.join(process.cwd(), ".gate-status")
    if (!fs.existsSync(gatePath)) return false
    const content = fs.readFileSync(gatePath, "utf8")
    for (const line of content.split("\n")) {
      if (line.startsWith("===")) continue
      if (/FAIL/.test(line)) return true
    }
    return false
  } catch { return false }
}

function repoHasPendingWork(): boolean {
  try {
    const { execSync } = require("node:child_process")
    const cwd = process.cwd()
    try {
      const unpushed = execSync("git log --oneline @{u}..HEAD", {
        cwd, encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
      })
      if (unpushed.trim().length > 0) return true
    } catch {}
    try {
      const status = execSync("git status --porcelain", {
        cwd, encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
      })
      if (status.trim().length > 0) return true
    } catch {}
    return false
  } catch { return false }
}

function ciIsPendingOrRed(): boolean {
  const now = Date.now()
  if (ciVerdictCache && (now - ciVerdictCache.ts) < 60_000) {
    return ciVerdictCache.isPendingOrRed
  }
  // Item 14: try watchdog cache first
  try {
    const ciCachePath = "/tmp/gludd-watchdog-ci.json"
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const lastCheck = ciData.last_ci_check || 0
      const lastStatus = ciData.last_ci_status || ""
      if (now - lastCheck < 120_000 && lastStatus) {
        const isGreen = lastStatus === "SUCCESS"
        ciVerdictCache = { ts: now, isPendingOrRed: !isGreen }
        return !isGreen
      }
    }
  } catch {}
  // Fallback: run ci-verdict ourselves
  try {
    const { execSync } = require("node:child_process")
    const cwd = process.cwd()
    try {
      const output = execSync("make ci-verdict BRANCH=master", {
        cwd, encoding: "utf8", timeout: 15000, stdio: ["pipe", "pipe", "pipe"],
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

function computeHealthScore(): number {
  let score = 100
  if (tasksMdHasUnchecked()) score -= 30
  if (ratchetHasEntries() > 0) score -= 20
  if (gateStatusIsRed()) score -= 40
  if (ciIsPendingOrRed()) score -= 10
  if (repoHasPendingWork()) score -= 10
  return Math.max(0, score)
}

// ── PLUGIN ─────────────────────────────────────────────────────────────────

export default (async ({ }) => {
  return {
    event: async ({ event }: { event: { type: string } }) => {
      if (event.type === "session.idle") {
        try {
          turnState.accumulatedText = ""
          turnState.toolCallMade = false

          const ratchetCount = ratchetHasEntries()
          const tasksMdUnchecked = tasksMdHasUnchecked()
          const gateRed = gateStatusIsRed()
          const repoPending = repoHasPendingWork()
          const ciVerdictPendingOrRed = ciIsPendingOrRed()
          const hasLocalWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || gateRed
          const hasPendingWork = hasLocalWork || ciVerdictPendingOrRed
          const healthScore = computeHealthScore()

          // Item 15: read watchdog disengage signal
          let watchdogDisengage = false
          try {
            const wsPath = "/tmp/gludd-watchdog-disengage.json"
            if (fs.existsSync(wsPath)) {
              const ws = JSON.parse(fs.readFileSync(wsPath, "utf8"))
              if (ws.disengage_until && ws.disengage_until > Date.now()) {
                watchdogDisengage = true
              }
            }
          } catch {}

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
            healthScore,
            watchdogDisengage,
          }

          fs.writeFileSync(STATE_FILE, JSON.stringify(state), "utf8")
        } catch {}
      }
    },

    "tool.execute.before": async (input: any, output: any) => {
      try {
        if (turnState.blocked) {
          turnState.blocked = false
        }

        // Item 5: detect tool-call intent
        turnState.toolCallMade = true

        if (input.tool === "question") {
          throw new Error(QUESTION_DENY_REASON)
        }

        if (input.tool === "bash") {
          const args = (output as Record<string, unknown> | undefined)?.args as { command?: string } | undefined
          const command = typeof args?.command === "string" ? args.command.trim() : ""
          if (command.startsWith("make ") && STOP_LIKE_TARGETS_RE.test(command)) {
            let taskMd: boolean
            let ratchetCount: number
            const cached = readSharedState()
            if (cached) {
              taskMd = cached.tasksMdUnchecked ?? tasksMdHasUnchecked()
              ratchetCount = cached.ratchetEntries ?? ratchetHasEntries()
            } else {
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
      }
    },

    "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
      try {
        if (typeof output === "string") {
          return `[orchestration] make-only commits, floor ${FLOOR}, gate-background for long ops.\n\n${output}`
        }
        return output
      } catch { return output }
    },

    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      // Item 18: env var gate — disable enforcement entirely
      if (!STOP_ENFORCE) return

      try {
        const text = output.text
        if (!text || text.trim().length === 0) return
        if (text.trim().length < 60) return

        turnState.accumulatedText += text

        // Item 15: check watchdog disengage signal
        let watchdogDisengage = false
        try {
          const wsPath = "/tmp/gludd-watchdog-disengage.json"
          if (fs.existsSync(wsPath)) {
            const ws = JSON.parse(fs.readFileSync(wsPath, "utf8"))
            if (ws.disengage_until && ws.disengage_until > Date.now()) {
              watchdogDisengage = true
            }
          }
        } catch {}

        let cache: StopStateCache | null = readSharedState()

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
            backlogOpen: 0, backlogItems: [],
            hasPendingWork, hasLocalWork, ciVerdictPendingOrRed,
            healthScore: computeHealthScore(),
            watchdogDisengage,
          }
        }

        // Item 6: check for false-positive cascade disengagement
        if (isDisengaged() || watchdogDisengage) return

        const repoPending = cache?.repoPending ?? false
        const ratchetCount = cache.ratchetEntries
        const tasksMdUnchecked = cache?.tasksMdUnchecked ?? false
        const gateRed = cache?.gateStatusRed ?? false
        const ciVerdictPendingOrRed = cache?.ciVerdictPendingOrRed ?? false
        const hasLocalWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || gateRed

        if (turnState.blocked) {
          output.text = ""
          return
        }

        // Item 5: if a tool call was just made, this response follows work — don't block
        if (turnState.toolCallMade) {
          turnState.toolCallMade = false
          // Only block if the response AFTER a tool call truly says "done"
          if (!COMPLETION_VERBATIM.test(text)) return
        }

        if (hasLocalWork && responseLooksTerminal(turnState.accumulatedText)) {
          recordBlock("hasLocalWork + looksTerminal")
          output.text = [
            "HARD STOP — STATE-BASED BLOCK: local work pending.",
            `TASKS.md unchecked: ${cache.tasksMdUnchecked ? "yes" : "no"}`,
            `ratchet entries: ${cache.ratchetEntries}`,
            `gate red: ${gateRed ? "yes" : "no"}`,
            `repo pending: ${cache.repoPending ? "yes" : "no"}`,
            `CI pending: ${ciVerdictPendingOrRed ? "yes" : "no"}`,
            "Fix pending work. Dispatch subagents.",
          ].join("\n")
          turnState.blocked = true
          return
        }
      } catch { return }
    },
  }
}) satisfies Plugin
