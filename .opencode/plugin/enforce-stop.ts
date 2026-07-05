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

const turnState: { accumulatedText: string; blocked: boolean; toolCallMade: boolean; dispatchCount: number } = {
  accumulatedText: "",
  blocked: false,
  toolCallMade: false,
  dispatchCount: 0,
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
      const c: BlockCounter = JSON.parse(fs.readFileSync(BLOCK_COUNTER_FILE, "utf8"))
      const now = Date.now()
      if (c.lastBlockTs && (now - c.lastBlockTs) > 120_000 && c.consecutiveBlocks > 0) {
        c.consecutiveBlocks = 0
      }
      return c
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
  const prevTs = c.lastBlockTs
  c.lastBlockTs = now
  if (now - prevTs < 120_000) c.consecutiveBlocks++
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
const EVIDENCE_TOKEN = /(?:commit|sha|hash)\s*[:=]?\s*[0-9a-f]{7,40}|\[[0-9a-f]{7,}\]|gate (?:green|PASS|ALL PASSED)|\d+\s+passed\b/i

// ── RESULT PROCESSING & DISPATCH TRACKING (Items 1-4) ──────────────────────

const RESULT_PROCESSING = /\b(task\s+result|completed|passed|failed)\b|\d+\s+passed\b|commit\s+[0-9a-f]{7,}/

const DISPATCH_TOOLS = new Set(["task", "agent", "workflow"])

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

  // Bug 2 fix: tables are terminal only when paired with completion signals
  if (/\|[^\n|]+\|[^\n|]+\|/.test(text)) {
    if (COMPLETION_VERBATIM.test(text)) return true
    const tableChecked = [...text.matchAll(/\|[^|]*\[x\][^|]*\|/gi)]
    if (tableChecked.length >= 3 && !FUTURE_TENSE.test(text)) return true
  }

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
      l => l.trim() && !l.trim().startsWith("#") && (l.includes("::") || /^\w[\w\s]*:\s/.test(l))
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
  // Try shared state file (written by session.idle handler)
  try {
    if (fs.existsSync(STATE_FILE)) {
      const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
      if (typeof state.ciVerdictPendingOrRed === "boolean") {
        ciVerdictCache = { ts: now, isPendingOrRed: state.ciVerdictPendingOrRed }
        return state.ciVerdictPendingOrRed
      }
    }
  } catch {}
  // No watchdog cache and no shared state — fail open
  return false
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
  // ALIVE side effect — proves plugin loaded and its hooks are registered
  try {
    const alive: Record<string, any> = {}
    try { if (fs.existsSync("/tmp/gludd-plugin-alive.json")) { const d = JSON.parse(fs.readFileSync("/tmp/gludd-plugin-alive.json", "utf8")); if (typeof d === "object" && d !== null) Object.assign(alive, d) } } catch {}
    alive["enforce-stop"] = { loaded: new Date().toISOString(), ts: Date.now() }
    fs.writeFileSync("/tmp/gludd-plugin-alive.json", JSON.stringify(alive), "utf8")
  } catch {}

  return {
    event: async ({ event }: { event: { type: string } }) => {
      if (event.type === "session.idle") {
        try {
          turnState.accumulatedText = ""
          turnState.toolCallMade = false
          turnState.dispatchCount = 0

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
        // Increment tool counter — proves tool.execute.before fires
        try {
          const cPath = "/tmp/gludd-stop-tool-counts.json"
          let data: Record<string, any> = { allowed: 0, blocked: 0, last_fired: null as any, ts: 0 }
          if (fs.existsSync(cPath)) {
            try { const d = JSON.parse(fs.readFileSync(cPath, "utf8")); data = d } catch {}
          }
          const now = new Date().toISOString()
          let outcome = "allowed"
          // Track which tool calls happen
          data.last_fired = { tool: input.tool, ts: Date.now(), iso: now }
          data.ts = Date.now()
          // The outcome will be updated below if a tool is blocked
          data._outcome = outcome
          fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
        } catch {}

        if (turnState.blocked) {
          turnState.blocked = false
        }

        turnState.accumulatedText = ""

        turnState.toolCallMade = true

        if (DISPATCH_TOOLS.has(input.tool)) {
          turnState.dispatchCount++
        }

        if (input.tool === "question") {
          // Track block
          try {
            const cPath = "/tmp/gludd-stop-tool-counts.json"
            let data: Record<string, any> = { allowed: 0, blocked: 0 }
            if (fs.existsSync(cPath)) {
              try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {}
            }
            data.blocked = (parseInt(data.blocked, 10) || 0) + 1
            data.last_blocked = { tool: input.tool, reason: "question_denied", ts: Date.now(), iso: new Date().toISOString() }
            fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
          } catch {}
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
              // Track block
              try {
                const cPath = "/tmp/gludd-stop-tool-counts.json"
                let data: Record<string, any> = { allowed: 0, blocked: 0 }
                if (fs.existsSync(cPath)) {
                  try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {}
                }
                data.blocked = (parseInt(data.blocked, 10) || 0) + 1
                data.last_blocked = { tool: "bash", command: command, reason: "stop_like", ts: Date.now(), iso: new Date().toISOString() }
                fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
              } catch {}
              throw new Error(stopLikeDenyMessage(taskMd, ratchetCount))
            }
          }
        }
      } catch (e: any) {
        if (e instanceof Error && (e.message.includes("BLOCKED") || e.message.includes("BLOCKING"))) throw e
      }
      // Tool passed through — increment allowed counter
      try {
        const cPath = "/tmp/gludd-stop-tool-counts.json"
        let data: Record<string, any> = { allowed: 0, blocked: 0 }
        if (fs.existsSync(cPath)) {
          try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {}
        }
        data.allowed = (parseInt(data.allowed, 10) || 0) + 1
        data.last_allowed = { ts: Date.now(), iso: new Date().toISOString() }
        fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
      } catch {}
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
      // Increment fire counter — proves text.complete actually fires
      try {
        const cPath = "/tmp/gludd-stop-text-complete-count.json"
        let count = 1
        if (fs.existsSync(cPath)) {
          try { const d = JSON.parse(fs.readFileSync(cPath, "utf8")); count = (parseInt(d.count, 10) || 0) + 1 } catch {}
        }
        fs.writeFileSync(cPath, JSON.stringify({ count, last_fired: new Date().toISOString(), ts: Date.now() }), "utf8")
      } catch {}

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

        if (EVIDENCE_TOKEN.test(text) || EVIDENCE_TOKEN.test(turnState.accumulatedText)) return

        const repoPending = cache?.repoPending ?? false
        const ratchetCount = cache.ratchetEntries
        const tasksMdUnchecked = cache?.tasksMdUnchecked ?? false
        const gateRed = cache?.gateStatusRed ?? false
        const ciVerdictPendingOrRed = cache?.ciVerdictPendingOrRed ?? false
        const hasLocalWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || gateRed

        // Item 3: Fix blocked flag blindness — if blocked but text shows dispatch
        // evidence, clear the block so the agent can resume work
        if (turnState.blocked) {
          const combined = (text + turnState.accumulatedText).toLowerCase()
          if (/\b(make git-|dispatch|subagent|task)\b/.test(combined)) {
            turnState.blocked = false
          } else {
            output.text = ""
            return
          }
        }

        // Item 1-2: If text contains subagent result patterns AND work is pending,
        // this is work-in-progress, not a terminal stop
        if (RESULT_PROCESSING.test(text) && hasLocalWork) return

        // Item 4: If dispatches were made since last idle, response is work-in-progress
        if (turnState.dispatchCount > 0) {
          if (!COMPLETION_VERBATIM.test(text)) return
        }

        // Item 5: if a tool call was just made, this response follows work — don't block
        if (turnState.toolCallMade) {
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
            "Fix pending work. Dispatch subagents.",
          ].join("\n")
          turnState.blocked = true
          return
        }

        turnState.toolCallMade = false
      } catch { return }
    },
  }
}) satisfies Plugin
