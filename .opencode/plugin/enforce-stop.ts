import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

const FLOOR = parseInt(process.env.CLAUDE_AGENT_FLOOR || "10", 10)
const STOP_ENFORCE = process.env.GLUDD_STOP_ENFORCE !== "0"
const NO_WAIT_ENFORCE = process.env.GLUDD_NO_WAIT_ENFORCE !== "0"

const STATE_FILE = process.env.GLUDD_STOP_STATE_FILE || "/tmp/gludd-stop-state.json"
const BLOCK_REASON_FILE = process.env.GLUDD_BLOCK_REASON_FILE || "/tmp/gludd-block-reason.json"
const BLOCK_COUNTER_FILE = process.env.GLUDD_BLOCK_COUNTER_FILE || "/tmp/gludd-block-counter.json"
const BLANKED_RESPONSE_FILE = "/tmp/gludd-blanked-responses.json"
const FORCE_DISPATCH_FILE = "/tmp/gludd-force-dispatch.json"

// ── SHARED STREAK STATE (P3: cross-call grinding detection) ────────────────
// Shared between enforce-floor.ts and enforce-stop.ts so EITHER plugin can
// catch main-thread grinding (serial read/edit/bash with no dispatch). The
// dedup window prevents double-counting when both plugins fire on the same
// tool.execute.before event.
const SHARED_STREAK_FILE = process.env.GLUDD_STREAK_FILE || "/tmp/gludd-tool-streak.json"
const STREAK_PLUGIN_NAME = "enforce-stop"
const STREAK_DEDUP_WINDOW_MS = 500
const DELEGATE_FIRST_THRESHOLD = 8
const GRINDING_HARD_DENY_THRESHOLD = 12

interface SharedStreakState {
  streak: number
  lastDispatchTs: number
  readStreak: number
  editStreak: number
  lastUpdateTs: number
  lastWriter: string
}

function readSharedStreak(): SharedStreakState {
  try {
    if (fs.existsSync(SHARED_STREAK_FILE)) {
      const raw = JSON.parse(fs.readFileSync(SHARED_STREAK_FILE, "utf8"))
      return {
        streak: typeof raw.streak === "number" ? raw.streak : 0,
        lastDispatchTs: typeof raw.lastDispatchTs === "number" ? raw.lastDispatchTs : 0,
        readStreak: typeof raw.readStreak === "number" ? raw.readStreak : 0,
        editStreak: typeof raw.editStreak === "number" ? raw.editStreak : 0,
        lastUpdateTs: typeof raw.lastUpdateTs === "number" ? raw.lastUpdateTs : 0,
        lastWriter: typeof raw.lastWriter === "string" ? raw.lastWriter : "",
      }
    }
  } catch {}
  return { streak: 0, lastDispatchTs: 0, readStreak: 0, editStreak: 0, lastUpdateTs: 0, lastWriter: "" }
}

function writeSharedStreak(s: SharedStreakState): void {
  try { fs.writeFileSync(SHARED_STREAK_FILE, JSON.stringify(s), "utf8") } catch {}
}

function isStreakReadTool(toolName: string): boolean {
  return toolName === "read" || toolName === "grep" || toolName === "glob"
}

// Update the shared streak for a tool call. Dedupes if the other plugin
// already counted this exact call (within DEDUP_WINDOW_MS). Returns the
// updated state so the caller can check thresholds.
function updateSharedStreak(tool: string): SharedStreakState {
  const s = readSharedStreak()
  const now = Date.now()
  const isDispatch = tool === "task" || tool === "agent" || tool === "workflow"
  const isRead = isStreakReadTool(tool)
  const alreadyCounted = (now - s.lastUpdateTs) < STREAK_DEDUP_WINDOW_MS
    && s.lastWriter !== STREAK_PLUGIN_NAME
    && s.lastWriter !== ""
  if (isDispatch) {
    s.streak = 0
    s.readStreak = 0
    s.editStreak = 0
    s.lastDispatchTs = now
  } else if (!alreadyCounted) {
    s.streak++
    if (isRead) s.readStreak++
    else s.editStreak++
  }
  s.lastUpdateTs = now
  s.lastWriter = STREAK_PLUGIN_NAME
  writeSharedStreak(s)
  return s
}

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
      // Cap unreasonable disengage values (max 1 hour from now) — write back immediately
      // BUG #23 fix: clamp at 1h instead of zeroing out (which silently disengaged)
      const MAX_DISENGAGE_HERE = now + 3_600_000
      if (c.disengageUntil > MAX_DISENGAGE_HERE) {
        c.disengageUntil = MAX_DISENGAGE_HERE
        try { fs.writeFileSync(BLOCK_COUNTER_FILE, JSON.stringify(c), "utf8") } catch {}
      }
      return c
    }
  } catch {}
  return { consecutiveBlocks: 0, totalBlocks: 0, lastBlockTs: 0, disengageUntil: 0 }
}

function writeBlockCounter(c: BlockCounter): void {
  try { fs.writeFileSync(BLOCK_COUNTER_FILE, JSON.stringify(c), "utf8") } catch {}
}

interface BlankedResponseTracker {
  totalBlanked: number
  blankedThisSession: number
  lastBlankedTs: number
  escalationLevel: number
}

function readBlankedResponses(): BlankedResponseTracker {
  try {
    if (fs.existsSync(BLANKED_RESPONSE_FILE)) {
      return JSON.parse(fs.readFileSync(BLANKED_RESPONSE_FILE, "utf8"))
    }
  } catch {}
  return { totalBlanked: 0, blankedThisSession: 0, lastBlankedTs: 0, escalationLevel: 0 }
}

function recordBlankedResponse(escalationLevel: number): void {
  const b = readBlankedResponses()
  const now = Date.now()
  const sameSession = b.lastBlankedTs && (now - b.lastBlankedTs) < 300_000
  b.totalBlanked++
  b.blankedThisSession = sameSession ? b.blankedThisSession + 1 : 1
  b.lastBlankedTs = now
  b.escalationLevel = escalationLevel
  try { fs.writeFileSync(BLANKED_RESPONSE_FILE, JSON.stringify(b), "utf8") } catch {}
}

function writeForceDispatch(consecutiveBlocks: number): void {
  try {
    fs.writeFileSync(FORCE_DISPATCH_FILE, JSON.stringify({
      active: true,
      consecutiveBlocks,
      ts: Date.now(),
      message: "EMERGENCY OVERRIDE: Agent has been blocked for text-only responses. Watchdog should inject auto-generated dispatch directives.",
    }), "utf8")
  } catch {}
}

const FALSE_DONE_BLOCKS_FILE = "/tmp/gludd-false-done-blocks.json"
function logFalseDoneBlock(text: string, reason?: string): void {
  try {
    const entry = {
      ts: Date.now(),
      iso: new Date().toISOString(),
      reason: reason || "false-done-claim",
      textLength: text.length,
      textPreview: text.slice(0, 200),
    }
    let blocks: any[] = []
    if (fs.existsSync(FALSE_DONE_BLOCKS_FILE)) {
      try { blocks = JSON.parse(fs.readFileSync(FALSE_DONE_BLOCKS_FILE, "utf8")) } catch {}
      if (!Array.isArray(blocks)) blocks = []
    }
    blocks.push(entry)
    if (blocks.length > 100) blocks = blocks.slice(-100)
    fs.writeFileSync(FALSE_DONE_BLOCKS_FILE, JSON.stringify(blocks, null, 2), "utf8")
  } catch {}
}

function recordBlock(reason: string): void {
  const c = readBlockCounter()
  const now = Date.now()
  c.totalBlocks++
  const prevTs = c.lastBlockTs
  c.lastBlockTs = now
  if (now - prevTs < 120_000) c.consecutiveBlocks++
  else c.consecutiveBlocks = 1
  if (c.consecutiveBlocks >= 20) {
    c.disengageUntil = now + 120_000
    console.error("FALSE-POSITIVE CASCADE: disengaging for 2 min after 20 consecutive blocks")
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
  // BUG #23 fix: clamp disengage to max 1 hour from now
  const now = Date.now()
  const MAX_DISENGAGE = now + 3_600_000
  if (c.disengageUntil > MAX_DISENGAGE) {
    c.disengageUntil = MAX_DISENGAGE
    try { fs.writeFileSync(BLOCK_COUNTER_FILE, JSON.stringify(c), "utf8") } catch {}
  }
  if (c.disengageUntil > now) return true
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

function stopLikeDenyMessage(taskMd: boolean, ratchetEntries: number, extraReasons: string[] = []): string {
  const reasons = [
    `TASKS.md unchecked: ${taskMd ? "yes" : "no"}, ratchet entries: ${ratchetEntries}`,
    ...extraReasons,
  ]
  return [
    "STOP-LIKE TOOL BLOCKED — PENDING WORK EXISTS:",
    ...reasons,
    "Fix pending work first, then retry.",
  ].join("\n")
}

// ── FALSE-DONE DETECTION PATTERNS ─────────────────────────────────────────

// Narrowed per 2026-07-07 incident: prior COMPLETION_VERBATIM matched
// "all tasks complete", "work finished", "all objectives met", etc. —
// phrases that appear in ANY progress report, not just terminal claims.
// This caused ~40% of subagent results to be blocked. Now restricted to
// truly terminal claim phrases only.
const COMPLETION_VERBATIM = /\b(all done|everything is complete|fully shipped|ready for review|work is complete)\b|✅.*✅/i
const DIRECT_FALSE_DONE_FLAGS = ["✅", "🗸"]
// Narrowed: `summary` and `results` removed — common report headers, not done claims.
const COMPLETION_HEADER_RE = /^##\s*(done|complete)\s*$/im
const STANDALONE_DONE_RE = /(^|\n)Done\.(?:\s|$)/g
const CHECKED_BOXES_RE = /^[-*]\s*\[x\]/im
const UNCHECKED_BOXES_RE = /^[-*]\s*\[\s*\]/im
const COMMIT_HASH_RE = /(?:commit|sha)\s*[:=]?\s*(?!0{7}|deadbeef|c0ffee)[0-9a-f]{7,40}|\[[0-9a-f]{7,}\]/i
const PASS_COUNT_EVIDENCE_RE = /\b[1-9]\d*\s+(?:passed|passing)\b/

function responseLooksTerminal(text: string): boolean {
  STANDALONE_DONE_RE.lastIndex = 0
  if (DIRECT_FALSE_DONE_FLAGS.some(f => text.includes(f))) return true
  if (COMPLETION_VERBATIM.test(text)) return true
  if (COMPLETION_HEADER_RE.test(text)) return true
  if (STANDALONE_DONE_RE.test(text)) return true
  if (CHECKED_BOXES_RE.test(text) && !UNCHECKED_BOXES_RE.test(text)) return true
  return false
}

// Bypass regexes — when ANY of these appear in the response, the false-done
// block is cancelled (real work artifact / evidence present).
// FILE_PATH_RE: covers the canonical source trees AND common top-level files
//   (Makefile, README, SESSION, TASKS, playbooks/). The prior regex required
//   a trailing `/`, so "Makefile" / "TASKS.md" did NOT bypass — that was the
//   over-blocking bug.
// COMMAND_MARKER_RE: subagent final-report shapes (## Report, ## CMD:,
//   RAW OUTPUT, Files changed, Test result) PLUS tool-output tokens
//   (PYTEST, Mypy, ruff, tests? (passed|failed)).
// P5 fix (2026-07-09): MARKDOWN_TABLE_RE is intentionally NOT consulted
//   in hasWorkArtifact. A markdown table alone is not evidence of work —
//   the agent can write a summary table and stop, which is the exact
//   "summary table as stopping point" pattern AGENTS.md forbids. The
//   regex is kept only for documentation; structured evidence (commit
//   hash / pass count / gate output) is the legitimate bypass for a
//   table that accompanies real work.
const FILE_PATH_RE = /(?:src|tests|\.opencode|collections|playbooks)\/|\b(?:Makefile|README|SESSION|TASKS|BUGS)\b/
const COMMAND_MARKER_RE = /## CMD:|## Report|## RAW OUTPUT|RAW OUTPUT|Test result|Files changed|tests?\s+(?:passed|failed)|PYTEST|Mypy|ruff/i

// P5 audit fix: permission-seeking deferral phrases. AGENTS.md Anti-Stop
// Patterns names each as a hard policy violation:
//   - "Shall I continue?"
//   - "Should I proceed?"
//   - "Want me to ...?" (proceed / start building / fix / etc.)
// These differ from completion claims (✅ / Done.) — the agent is not
// claiming work is finished, it is asking the user to green-light the
// next step before doing it. Both are premature stops. Kept as a single
// regex literal (not an array) so it cannot silently grow back into the
// unbounded vocabulary lists the lean-plugin refactor removed.
const STOP_PATTERN_PHRASES = /\b(?:shall\s+i\s+continue|should\s+i\s+proceed|want\s+me\s+to)\b/i

// QA_RESPONSE_PATTERNS — detects Q&A-style "what was done" recaps that answer
// a user question but then stop without a tool call. These are premature stops
// in disguise: the agent successfully answers the question but fails to resume
// work. Distinct from COMPLETION_VERBATIM (extreme terminal claims) and
// STOP_PATTERN_PHRASES (permission-seeking deferrals).
//
// Matched shapes (from BUGS.md #7 and #10 incidents):
//   - "completed in this session", "was done since the crash"
//   - "everything committed and merged"
//   - "Here's what was done/completed/changed"
//   - Bolded question-style recap headers: "**What changed?**", "**What's done?**"
//   - "What happened/changed since the crash"
//   - "Summary of what was done"
//
// These are structurally different from subagent final reports (which carry
// "Files changed:", "Test results", "RAW OUTPUT" markers). A Q&A recap is a
// conversation directed at the user, not a structured work deliverable.
const QA_RESPONSE_PATTERNS = /\b(?:completed in this session|was done since the (?:crash|last session)|everything (?:committed|has been committed)(?:\s+and\s+merged)?|here['\u2019]s what (?:was\s+(?:done|completed|finished)|changed)|what (?:changed|was done|happened)\s+since\s+the\s+(?:crash|last session)|summary of what was (?:done|completed))\b|\*\*What\s+(?:changed|was\s+(?:done|completed)|happened|is\s+(?:left|remaining))\?\*\*/i

// ── DISPATCH TRACKING ─────────────────────────────────────────────────────

const DISPATCH_TOOLS = new Set(["task", "agent", "workflow"])

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

function countTasksMdUnchecked(): number {
  try {
    const tasksPath = path.join(process.cwd(), "TASKS.md")
    if (!fs.existsSync(tasksPath)) return 0
    const content = fs.readFileSync(tasksPath, "utf8")
    const matches = content.match(/^[-*]\s+\[ \]/gm)
    return matches ? matches.length : 0
  } catch { return 0 }
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

function repoHasPendingWork(mode?: "commit" | "push"): boolean {
  try {
    const { execSync } = require("node:child_process")
    const cwd = process.cwd()
    if (mode === undefined) {
      try {
        const unpushed = execSync("git log --oneline @{u}..HEAD", {
          cwd, encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
        })
        if (unpushed.trim().length > 0) return true
      } catch {}
    }
    try {
      if (mode === "commit") {
        const unstaged = execSync("git diff --name-only", {
          cwd, encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
        })
        if (unstaged.trim().length > 0) return true
      } else {
        const status = execSync("git status --porcelain", {
          cwd, encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
        })
        if (status.trim().length > 0) return true
      }
    } catch {}
    return false
  } catch { return false }
}

const COMMIT_TARGET_RE = /^make\s+(git-commit|commit-no-verify|git-commit-file|test-and-commit|repo-commit|feature-done|git-merge)(\s|$)/
const PUSH_TARGET_RE = /^make\s+(git-push-branch|git-push-branch-nv|git-push-sandboxcom|git-push-sandboxcom-main|git-push-master|git-tag-push|release-cut|release-promote|ship-commit|release-recut|release-branch-new)(\s|$)/

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

function bugsMdHasOpenIncidents(): boolean {
  try {
    const bugsPath = path.join(process.cwd(), "BUGS.md")
    if (!fs.existsSync(bugsPath)) return false
    const content = fs.readFileSync(bugsPath, "utf8")
    const openIncidents = content
      .split("\n")
      .filter(l => /^###\s+\d{4}-\d{2}-\d{2}\s+[-—]/.test(l))
      .filter(l => !l.includes("(resolved)"))
    return openIncidents.length > 0
  } catch { return false }
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

function _reportAlive(): void {
  try {
    const alive: Record<string, any> = {}
    try { if (fs.existsSync("/tmp/gludd-plugin-alive.json")) { const d = JSON.parse(fs.readFileSync("/tmp/gludd-plugin-alive.json", "utf8")); if (typeof d === "object" && d !== null) Object.assign(alive, d) } } catch {}
    alive["enforce-stop"] = { last_seen: Date.now() }
    fs.writeFileSync("/tmp/gludd-plugin-alive.json", JSON.stringify(alive), "utf8")
  } catch {}
}

// Per-plugin heartbeat — runtime evidence that tool.execute.before ACTUALLY
// fires. Fail-open. Distinct from the shared alive.json.
function _writeHeartbeat(): void {
  try {
    const hb = JSON.stringify({ plugin: "enforce-stop", ts: Date.now(), pid: process.pid })
    fs.writeFileSync("/tmp/gludd-plugin-heartbeat-enforce-stop.json", hb)
  } catch { /* fail-open */ }
}

export default (async ({ }) => {
  // LOADED self-check: proves opencode invoked the factory (registered, not
  // merely present on disk). Appended to the shared log.
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-stop ` +
      `event+tool.execute.before+experimental.text.complete+experimental.chat.system.transform ` +
      `pid=${process.pid}\n`,
      "utf8",
    )
  } catch { /* fail-open */ }
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
      _reportAlive()
      _writeHeartbeat()
      try {
        // Increment tool counter — proves tool.execute.before fires
        try {
          const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
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

        // P3: Update the shared cross-plugin streak counter. This tracks ALL
        // consecutive non-dispatch calls (reads + edits + bash) so grinding
        // detection works even when enforce-floor.ts's in-memory streak is
        // out of sync or its hook fails open.
        const streakState = updateSharedStreak(input.tool)

        if (input.tool === "question") {
          // Track block
          try {
            const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
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
            const bugsOpen = bugsMdHasOpenIncidents()
            const gateRed = gateStatusIsRed()
            const ciBad = ciIsPendingOrRed()
            const repoMode: "commit" | "push" | undefined =
              COMMIT_TARGET_RE.test(command) ? "commit" :
              PUSH_TARGET_RE.test(command) ? "push" : undefined
            const repoPending = repoHasPendingWork(repoMode)
            // Disengage check: when the operator has explicitly disengaged
            // enforcement, skip the stop-like-tool block so commits and pushes
            // can proceed. Fixes: disengage-enforcement was only respected in
            // session.idle, not tool.execute.before — the operator could run
            // "make disengage-enforcement" and still be blocked on every commit.
            let disengaged = false
            try {
              const disPath = "/tmp/gludd-watchdog-disengage.json"
              if (fs.existsSync(disPath)) {
                const d = JSON.parse(fs.readFileSync(disPath, "utf8"))
                if (d.disengage_until) {
                  // BUG #23 fix: clamp disengage to max 1 hour from now
                  const now = Date.now()
                  const MAX_DISENGAGE = now + 3_600_000
                  const effective = Math.min(d.disengage_until, MAX_DISENGAGE)
                  if (effective > now) disengaged = true
                }
              }
            } catch {}
            if (!disengaged && (taskMd || ratchetCount > 0 || bugsOpen || gateRed || ciBad || repoPending)) {
              // Track block
              try {
                const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
                let data: Record<string, any> = { allowed: 0, blocked: 0 }
                if (fs.existsSync(cPath)) {
                  try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {}
                }
                data.blocked = (parseInt(data.blocked, 10) || 0) + 1
                data.last_blocked = { tool: "bash", command: command, reason: "stop_like", ts: Date.now(), iso: new Date().toISOString() }
                fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
              } catch {}
              const extraReasons: string[] = []
              if (bugsOpen) extraReasons.push("BUGS.md open incidents")
              if (gateRed) extraReasons.push("gate RED")
              if (ciBad) extraReasons.push("CI pending/red")
              if (repoPending) extraReasons.push("repo dirty")
              throw new Error(stopLikeDenyMessage(taskMd, ratchetCount, extraReasons))
            }
          }
        }

        // P3: Cross-call grinding detection. If the agent has made many
        // consecutive non-dispatch calls, deny mutations to force delegation.
        // Reads are NEVER denied (the agent needs them to prepare the next
        // dispatch wave); dispatch tools are never denied; questions handled above.
        const isMutationTool = !DISPATCH_TOOLS.has(input.tool)
          && !isStreakReadTool(input.tool)
          && input.tool !== "question"
        if (isMutationTool) {
          let grindingDisengaged = false
          try {
            const disPath = "/tmp/gludd-watchdog-disengage.json"
            if (fs.existsSync(disPath)) {
              const d = JSON.parse(fs.readFileSync(disPath, "utf8"))
              if (d.disengage_until) {
                const now = Date.now()
                const MAX_DISENGAGE = now + 3_600_000
                const effective = Math.min(d.disengage_until, MAX_DISENGAGE)
                if (effective > now) grindingDisengaged = true
              }
            }
          } catch {}
          if (!grindingDisengaged) {
            if (streakState.streak > GRINDING_HARD_DENY_THRESHOLD) {
              try {
                const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
                let data: Record<string, any> = { allowed: 0, blocked: 0 }
                if (fs.existsSync(cPath)) {
                  try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {}
                }
                data.blocked = (parseInt(data.blocked, 10) || 0) + 1
                data.last_blocked = { tool: input.tool, reason: "main-thread-grinding", streak: streakState.streak, ts: Date.now(), iso: new Date().toISOString() }
                fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
              } catch {}
              recordBlock("main-thread-grinding")
              return {
                permissionDecision: "deny" as const,
                message: [
                  "⛔ MAIN-THREAD GRINDING DETECTED",
                  `${streakState.streak} consecutive non-dispatch calls.`,
                  "You are grinding on the main thread with no subagent dispatch.",
                  "DISPATCH WORK via task/agent/workflow or justify why this must be inline.",
                  "",
                  `Streak breakdown: ${streakState.readStreak} reads, ${streakState.editStreak} edits/bash.`,
                ].join("\n"),
              }
            }
            if (streakState.streak > DELEGATE_FIRST_THRESHOLD) {
              console.warn(
                `DELEGATE-FIRST: ${streakState.streak} consecutive non-dispatch calls. ` +
                `You are trending toward main-thread grinding. ` +
                `DISPATCH WORK via task/agent/workflow before continuing inline work. ` +
                `Streak breakdown: ${streakState.readStreak} reads, ${streakState.editStreak} edits/bash.`
              )
            }
          }
        }
      } catch (e: any) {
        if (e instanceof Error && (e.message.includes("BLOCKED") || e.message.includes("BLOCKING"))) throw e
        console.error("[enforce-stop] tool.execute.before error (fail-open):", e)
      }
      // Tool passed through — increment allowed counter
      try {
        const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
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
      const unchecked = countTasksMdUnchecked()
      const ratchetCount = ratchetHasEntries()
      const bugsOpen = bugsMdHasOpenIncidents()
      const gateRed = gateStatusIsRed()
      const ciBad = ciIsPendingOrRed()
      const repoPending = repoHasPendingWork()
      const hasWork = unchecked > 0 || ratchetCount > 0 || bugsOpen || gateRed || ciBad || repoPending

      if (typeof output === "string") {
        if (hasWork) {
          const indicators: string[] = []
          if (unchecked > 0) indicators.push(`${unchecked} unchecked TASKS.md items`)
          if (ratchetCount > 0) indicators.push(`${ratchetCount} ratchet entries`)
          if (bugsOpen) indicators.push("BUGS.md open incidents")
          if (gateRed) indicators.push("gate RED")
          if (ciBad) indicators.push("CI pending/red")
          if (repoPending) indicators.push("repo dirty")
          const block = [
            "",
            "══════════════════════════════════════════════════════════════",
            "⛔⛔⛔ MANDATORY PRE-GENERATION GATE ⛔⛔⛔",
            "══════════════════════════════════════════════════════════════",
            "",
            `PENDING WORK EXISTS: ${indicators.join(", ")}.`,
            "",
            "YOU ARE PHYSICALLY FORBIDDEN FROM GENERATING A TEXT-ONLY RESPONSE.",
            "",
            "Before generating ANY text character, you MUST include a tool call.",
            "Acceptable: dispatch a subagent (Task tool), read a file (Read tool),",
            "edit code (Edit/Write tool), or run a make target (Bash tool).",
            "",
            "UNACCEPTABLE AND WILL BE BLANKED:",
            "- Status reports / summaries / recaps",
            "- Tables of completed work",
            "- 'All done' / 'Everything is complete' / 'Ready for review'",
            "- Any text without an accompanying tool call",
            "- 'Here is what I'll do next' without actually DOING it",
            "",
            "Example of CORRECT response when subagent results arrive:",
            "  [Task tool: dispatch 10 more subagents to continue work]",
            "",
            "Example of INCORRECT response (will be blanked):",
            "  'All 10 subagents completed. Here's a summary of results...'",
            "",
            "YOU HAVE BEEN WARNED. GENERATE A TOOL CALL NOW.",
            "══════════════════════════════════════════════════════════════",
            "",
            output
          ].join("\n")
          return block
        }
        return `[orchestration] No pending work. Normal operation.\n\n${output}`
      }
      return output
    },

    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      // Increment fire counter — proves text.complete actually fires
      try {
        const cPath = process.env.GLUDD_STOP_TEXT_COMPLETE_COUNT || "/tmp/gludd-stop-text-complete-count.json"
        let count = 1
        if (fs.existsSync(cPath)) {
          try { const d = JSON.parse(fs.readFileSync(cPath, "utf8")); count = (parseInt(d.count, 10) || 0) + 1 } catch {}
        }
        fs.writeFileSync(cPath, JSON.stringify({ count, last_fired: new Date().toISOString(), ts: Date.now() }), "utf8")
      } catch {}

      // Item 18: env var gates — disable enforcement entirely
      if (!STOP_ENFORCE || !NO_WAIT_ENFORCE) return

      try {
        const text = output.text
        if (!text || text.trim().length === 0) return

        // BUG #12 fix: check disengaged state first — legitimate admin override
        if (isDisengaged()) return

        // P3: DELEGATE-FIRST nag — when shared streak > 8, prepend a nag
        // that survives subagent-report bypass and text-only blocks.
        // Uses the shared streak file (written by enforce-floor.ts) so it
        // catches grinding even when enforce-stop.ts's own tool.execute.before
        // hasn't fired recently.
        const shared = readSharedStreak()
        if (shared.streak > DELEGATE_FIRST_THRESHOLD) {
          output.text = [
            `⛔ DELEGATE-FIRST — ${shared.streak} consecutive non-dispatch calls.`,
            `Streak: ${shared.readStreak} reads, ${shared.editStreak} edits/bash.`,
            `DISPATCH WORK via task/agent/workflow immediately.`,
            "",
            output.text,
          ].join("\n")
          return output
        }

        // Check short completion claims (✅, "Done.") before the 60-char minimum
        if (text.trim().length < 60) {
          if (responseLooksTerminal(text)) {
            logFalseDoneBlock(text, "short-false-done")
            output.text = "⛔ FALSE-DONE CLAIM BLOCKED. DISPATCH A TOOL CALL."
            turnState.blocked = true
            return
          }
          // Fall through — short non-false-done text still needs pending-work checks
        }

        turnState.accumulatedText += text

        // Item 15: check watchdog disengage signal
        // BUG #23 fix: clamp disengage to max 1 hour from now
        let watchdogDisengage = false
        try {
          const wsPath = "/tmp/gludd-watchdog-disengage.json"
          if (fs.existsSync(wsPath)) {
            const ws = JSON.parse(fs.readFileSync(wsPath, "utf8"))
            if (ws.disengage_until) {
              const now = Date.now()
              const MAX_DISENGAGE = now + 3_600_000
              const effective = Math.min(ws.disengage_until, MAX_DISENGAGE)
              if (effective > now) watchdogDisengage = true
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

        // DIRECT FALSE-DONE DETECTION: check completion claim patterns
        // BEFORE any evidence bypass. Catches "✅", "Done.", "[x]" summary tables, etc.
        const combinedText = text + turnState.accumulatedText
        const lower = combinedText.toLowerCase()

        // P5 audit fix: permission-seeking deferral phrases ("Shall I continue?",
        // "Should I proceed?", "Want me to ...?") are premature stops in disguise.
        // Block them when no machine evidence is present.
        const hasStopPatternPhrase = STOP_PATTERN_PHRASES.test(combinedText)
        const hasDirectFalseDone = responseLooksTerminal(combinedText) || hasStopPatternPhrase

        const tasksMdUnchecked = cache?.tasksMdUnchecked ?? tasksMdHasUnchecked()
        const ratchetCount = cache?.ratchetEntries ?? ratchetHasEntries()

        if (hasDirectFalseDone) {
          // NARROWED PREDICATE (per AGENTS.md Guardrail Integrity Policy —
          // narrow the check, do NOT delete it). Block ONLY when ALL of:
          //   1. A completion-style phrase is present (hasDirectFalseDone above)
          //   2. AND no structured evidence (commit hash / nonzero pass count,
          //      length-capped so a giant pasted log can't satisfy by accident)
          //   3. AND no gate output token (PASS|FAIL|passed|failed from make)
          //   4. AND no file path edited (src/ tests/ .opencode/ collections/)
          //   5. AND no subagent-report marker (## Report, RAW OUTPUT, ## CMD:,
          //      Files changed, Test results, etc. — these are subagent final
          //      reports, which the harness marks `completed` on purpose)
          //   6. AND no tool call / dispatch made this response (work-in-progress)
          // Any ONE of conditions 2–6 failing cancels the block. This catches
          // a true terminal text-only "All done" with no evidence, but does
          // NOT catch "I edited X, ran make Y, output was Z" — the previous
          // predicate (only commit-hash || tool-call) blocked the latter
          // because subagents have no commit access and their final reports
          // arrive as text with no main-agent tool call in the same response.
          const SUBAGENT_REPORT_MARKERS = [
            "Files changed", "Files edited", "Test results",
            "## Report", "## Result", "RAW OUTPUT",
            "## CMD:", "Output:", "Exit code",
          ]
          const hasCommitHash = COMMIT_HASH_RE.test(combinedText)
          const hasPassCount = PASS_COUNT_EVIDENCE_RE.test(combinedText)
          const hasGateOutput = /\b(?:PASS|FAIL|passed|failed)\b/.test(combinedText)
          const hasFilePath = FILE_PATH_RE.test(combinedText)
          const hasCommandMarker = COMMAND_MARKER_RE.test(combinedText)
          const hasSubagentReportMarker = SUBAGENT_REPORT_MARKERS.some(m => combinedText.includes(m))
          const hasStructuredEvidence = (hasCommitHash || hasPassCount) && combinedText.length < 500
          // P5: hasMarkdownTable intentionally removed from this union. A
          // table alone is not evidence of work — the agent can write a
          // summary table and stop. Structured evidence (commit hash / pass
          // count / gate output) still cancels the block for legitimate
          // table+evidence reports.
          const hasWorkArtifact = hasFilePath || hasGateOutput || hasSubagentReportMarker || hasCommandMarker
          const isWorkResponse = turnState.dispatchCount > 0 || turnState.toolCallMade
          if (!hasStructuredEvidence && !hasWorkArtifact && !isWorkResponse) {
            recordBlock("direct-false-done-no-evidence")
            logFalseDoneBlock(combinedText, "direct-false-done-no-evidence")
            output.text = [
              "⛔ FALSE-DONE CLAIM BLOCKED: completion claim with no structured evidence.",
              "",
              `State: ratchet entries=${ratchetCount}, TASKS.md unchecked=${tasksMdUnchecked}`,
              "",
              "You claimed completion (✅, \"Done\", checkboxes, etc.) without a",
              "commit hash, gate output, file path, or subagent report marker.",
              "DISPATCH A TOOL CALL NOW.",
            ].join("\n")
            turnState.blocked = true
            return
          }
          // Has structured evidence / work artifact / made tool calls — allow
        }

        const repoPending = cache?.repoPending ?? false
        const gateRed = cache?.gateStatusRed ?? false
        const ciVerdictPendingOrRed = cache?.ciVerdictPendingOrRed ?? false
        const hasLocalWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || gateRed

        // Handle previously-blocked state
        if (turnState.blocked) {
          const combined = (text + turnState.accumulatedText).toLowerCase()
          if (/\b(make git-|dispatch|subagent|task)\b/.test(combined)) {
            turnState.blocked = false
          } else {
            output.text = ""
            return
          }
        }

        // AGGRESSIVE: if ratchet has entries and NO tool calls this response, block everything
        if (ratchetCount > 0 && turnState.dispatchCount === 0) {
          recordBlock("ratchet_entries_no_tool_calls")
          logFalseDoneBlock(combinedText, "ratchet-block-all-text")
          output.text = [
            "⛔ TEXT BLOCKED — RATCHET HAS PENDING ENTRIES.",
            `ratchet entries: ${ratchetCount}`,
            "",
            "NO TEXT-ONLY RESPONSES ALLOWED WHILE RATCHET HAS ENTRIES.",
            "DISPATCH SUBAGENTS OR MAKE TOOL CALLS NOW.",
          ].join("\n")
          turnState.blocked = true
          return
        }

        // Work-in-progress: dispatches were made THIS response (not persisted from prior)
        if (turnState.dispatchCount > 0) {
          if (!COMPLETION_VERBATIM.test(text)) return
        }

        // Work-in-progress: tool call was just made
        if (turnState.toolCallMade) {
          if (!COMPLETION_VERBATIM.test(text)) return
        }

        // NEW: Q&A response summary stop-pattern detection. When the agent answers a
        // user question with a "what was done" recap and no tool call, AND pending
        // work exists — it's a premature stop in Q&A disguise. Fires BEFORE the
        // hasLocalWork subagent-report bypass below so Q&A summaries are not
        // incorrectly classified as legitimate subagent relays.
        //
        // Pattern source: BUGS.md #7 and #10 — agent sent text-only summaries with
        // phrases like "completed in this session", "everything committed and merged",
        // and bolded question-header recaps ("**What changed?**", "**What's left?**").
        const isQaSummary = QA_RESPONSE_PATTERNS.test(combinedText)
        if (isQaSummary && (hasLocalWork || ciVerdictPendingOrRed)) {
          logFalseDoneBlock(combinedText, "qa-response-summary-stop")
          recordBlock("qa-response-summary-stop")
          output.text = [
            "⛔ QA RESPONSE SUMMARY BLOCKED — answer the question, THEN continue work.",
            "",
            `State: ratchet entries=${ratchetCount}, TASKS.md unchecked=${tasksMdUnchecked}`,
            "",
            "You answered a question with a summary of completed work but did",
            "not include a tool call. Pending work still exists. When asked a",
            "factual question with unfinished tasks, answer briefly AND dispatch",
            "the next work wave in the same response.",
            "DISPATCH A TOOL CALL NOW.",
          ].join("\n")
          turnState.blocked = true
          return
        }

        // BUG #6 fix: when hasLocalWork, block ALL text (not just terminal-looking).
        // If work is pending and the agent sends text without tool calls, it's a stop.
        // NARROWED 2026-07-07: subagent final reports (which arrive as text with no
        // tool call in the same response, because the harness marks subagents
        // `completed` on purpose) MUST NOT be blanked. Without this bypass, ~40% of
        // subagent results in this session were erased, blocking all parallel work.
        // The bypass mirrors the hasDirectFalseDone check above: if the response
        // contains a subagent-report marker OR structured evidence OR a file path,
        // it is a legitimate work report — not a premature stop.
        const SUBAGENT_REPORT_MARKERS_LATE = [
          "Files changed", "Files edited", "Test results",
          "## Report", "## Result", "## RAW OUTPUT", "RAW OUTPUT",
          "## CMD:", "Output:", "Exit code",
        ]
        const lateHasCommitHash = COMMIT_HASH_RE.test(combinedText)
        const lateHasPassCount = PASS_COUNT_EVIDENCE_RE.test(combinedText)
        const lateHasFilePath = FILE_PATH_RE.test(combinedText)
        const lateHasCommandMarker = COMMAND_MARKER_RE.test(combinedText)
        const lateHasSubagentReportMarker = SUBAGENT_REPORT_MARKERS_LATE.some(m => combinedText.includes(m))
        const lateHasStructuredEvidence = (lateHasCommitHash || lateHasPassCount) && combinedText.length < 500
        // P5: lateHasMarkdownTable intentionally removed — mirrors the
        // hasDirectFalseDone narrowing above. A summary table alone must
        // not bypass the hasLocalWork block.
        const lateHasWorkArtifact = lateHasFilePath || lateHasCommandMarker || lateHasSubagentReportMarker
        const isSubagentFinalReport = lateHasStructuredEvidence || lateHasWorkArtifact
        if ((hasLocalWork || ciVerdictPendingOrRed) && !isSubagentFinalReport) {
          logFalseDoneBlock(turnState.accumulatedText, "hasLocalWork-text-only")
          output.text = [
            "HARD STOP — STATE-BASED BLOCK: local work pending.",
            `TASKS.md unchecked: ${tasksMdUnchecked ? "yes" : "no"}`,
            `ratchet entries: ${ratchetCount}`,
            "DISPATCH SUBAGENTS NOW.",
          ].join("\n")
          turnState.blocked = true
          return
        }

        turnState.toolCallMade = false
        turnState.dispatchCount = 0
      } catch (e) {
        try {
          fs.writeFileSync('/tmp/gludd-enforce-stop-error.log',
            `${new Date().toISOString()} ${String(e)}\n`)
        } catch {}
        console.error('enforce-stop text.complete error:', String(e))
      }
    },
  }
}) satisfies Plugin
