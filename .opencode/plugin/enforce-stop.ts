import * as fs from "node:fs"
import * as path from "node:path"
import { spawn, execSync } from "node:child_process"
import { isSubagent, isDisengaged as isWatchdogDisengaged, reportAlive, writeHeartbeat, isDispatchTool, isReadTool, readSharedStreak, writeSharedStreak, updateSharedStreak, type SharedStreakState, SHARED_STREAK_FILE } from "./shared.ts"
import { loadHotModule, type HotModule } from "./hot_reload.ts"

const FLOOR = parseInt(process.env.CLAUDE_AGENT_FLOOR || "7", 10)
const STOP_ENFORCE = process.env.GLUDD_STOP_ENFORCE !== "0"
const NO_WAIT_ENFORCE = process.env.GLUDD_NO_WAIT_ENFORCE !== "0"

const STATE_FILE = process.env.GLUDD_STOP_STATE_FILE || "/tmp/gludd-stop-state.json"
const BLOCK_REASON_FILE = process.env.GLUDD_BLOCK_REASON_FILE || "/tmp/gludd-block-reason.json"
const BLOCK_COUNTER_FILE = process.env.GLUDD_BLOCK_COUNTER_FILE || "/tmp/gludd-block-counter.json"
const BLANKED_RESPONSE_FILE = "/tmp/gludd-blanked-responses.json"
const FORCE_DISPATCH_FILE = "/tmp/gludd-force-dispatch.json"
const PERSIST_BLOCK_FILE = process.env.GLUDD_PERSIST_STOP_BLOCK_FILE || "/tmp/gludd-persist-stop-block.json"

const POST_RESULTS_STATE_FILE = process.env.GLUDD_POST_RESULTS_STATE_FILE || "/tmp/gludd-post-results-state.json"
const TEXT_ONLY_STATE_FILE = process.env.GLUDD_TEXT_ONLY_STATE_FILE || "/tmp/gludd-text-only-state.json"
const WAVE_RESULT_THRESHOLD = 3

// ── SHARED STREAK STATE (P3: cross-call grinding detection) ────────────────
const DELEGATE_FIRST_THRESHOLD = 8
const GRINDING_HARD_DENY_THRESHOLD = 12

const WATCHDOG_CONTINUE_FILE = "/tmp/gludd-continue.txt"

function spawnGateRefresh(): void {
  try {
    const gatePath = path.join(process.cwd(), ".gate-status")
    if (!fs.existsSync(gatePath)) return
    const stat = fs.statSync(gatePath)
    if ((Date.now() - stat.mtimeMs) <= 300_000) return
    const child = spawn("make", ["gate-refresh"], {
      cwd: process.cwd(),
      detached: true,
      stdio: "ignore",
    })
    child.unref()
  } catch { /* fire-and-forget */ }
}

function readWatchdogContinue(): string | null {
  try {
    if (fs.existsSync(WATCHDOG_CONTINUE_FILE)) {
      const content = fs.readFileSync(WATCHDOG_CONTINUE_FILE, "utf8").trim()
      if (content.length > 0) return content
    }
  } catch {}
  return null
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

// ── PERSISTENT STOP-BLOCK FLAG (two-layer enforcement) ─────────────────────

interface PersistBlockFlag {
  blocked: boolean
  timestamp: number
  reason: string
}

function readPersistBlock(): PersistBlockFlag {
  try {
    if (fs.existsSync(PERSIST_BLOCK_FILE)) {
      const raw = JSON.parse(fs.readFileSync(PERSIST_BLOCK_FILE, "utf8"))
      return {
        blocked: !!raw.blocked,
        timestamp: typeof raw.timestamp === "number" ? raw.timestamp : 0,
        reason: typeof raw.reason === "string" ? raw.reason : "",
      }
    }
  } catch {}
  return { blocked: false, timestamp: 0, reason: "" }
}

function writePersistBlock(blocked: boolean, reason: string): void {
  try {
    fs.writeFileSync(PERSIST_BLOCK_FILE, JSON.stringify({
      blocked,
      timestamp: Date.now(),
      reason,
    }), "utf8")
  } catch {}
}

function clearPersistBlock(): void {
  try { fs.unlinkSync(PERSIST_BLOCK_FILE) } catch {}
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

const COMPLETION_VERBATIM = /\b(all done|everything is complete|fully shipped|ready for review|work is complete)\b|✅.*✅/i
const DIRECT_FALSE_DONE_FLAGS = ["✅", "🗸"]
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

const FILE_PATH_RE = /(?:src|tests|\.opencode|collections|playbooks)\/|\b(?:Makefile|README|SESSION|TASKS|BUGS)\b/
const COMMAND_MARKER_RE = /## CMD:|## Report|## RAW OUTPUT|RAW OUTPUT|Test result|Files changed|tests?\s+(?:passed|failed)|PYTEST|Mypy|ruff/i

const STOP_PATTERN_PHRASES = /\b(?:shall\s+i\s+continue|should\s+i\s+proceed|want\s+me\s+to)\b/i

const QA_RESPONSE_PATTERNS = /\b(?:completed in this session|was done since the (?:crash|last session)|everything (?:committed|has been committed)(?:\s+and\s+merged)?|here['\u2019]s what (?:was\s+(?:done|completed|finished)|changed)|what (?:changed|was done|happened)\s+since\s+the\s+(?:crash|last session)|summary of what was (?:done|completed))\b|\*\*What\s+(?:changed|was\s+(?:done|completed)|happened|is\s+(?:left|remaining))\?\*\*/i

// ── DISPATCH TRACKING ─────────────────────────────────────────────────────

// ── STATE FUNCTIONS ────────────────────────────────────────────────────────

function readSharedState(): StopStateCache | null {
  try {
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

function gateStatusIsStale(minAgeMs: number = 300_000): boolean {
  try {
    const gatePath = path.join(process.cwd(), ".gate-status")
    if (!fs.existsSync(gatePath)) return false
    const stat = fs.statSync(gatePath)
    return (Date.now() - stat.mtimeMs) > minAgeMs
  } catch { return false }
}

function repoHasPendingWork(mode?: "commit" | "push"): boolean {
  try {
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
  try {
    if (fs.existsSync(STATE_FILE)) {
      const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
      if (typeof state.ciVerdictPendingOrRed === "boolean") {
        ciVerdictCache = { ts: now, isPendingOrRed: state.ciVerdictPendingOrRed }
        return state.ciVerdictPendingOrRed
      }
    }
  } catch {}
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

interface PostResultsState {
  lastTurnHadResults: boolean
  lastTurnHadWave: boolean
  lastTurnTs: number
  lastResultCount: number
}

interface TextOnlyState {
  count: number
  lastTs: number
  sameSession: boolean
}

function readPostResultsState(): PostResultsState {
  try {
    if (fs.existsSync(POST_RESULTS_STATE_FILE)) {
      return JSON.parse(fs.readFileSync(POST_RESULTS_STATE_FILE, "utf8"))
    }
  } catch {}
  return { lastTurnHadResults: false, lastTurnHadWave: false, lastTurnTs: 0, lastResultCount: 0 }
}

function writePostResultsState(s: PostResultsState): void {
  try { fs.writeFileSync(POST_RESULTS_STATE_FILE, JSON.stringify(s), "utf8") } catch {}
}

function readTextOnlyState(): TextOnlyState {
  try {
    if (fs.existsSync(TEXT_ONLY_STATE_FILE)) {
      return JSON.parse(fs.readFileSync(TEXT_ONLY_STATE_FILE, "utf8"))
    }
  } catch {}
  return { count: 0, lastTs: 0, sameSession: false }
}

function writeTextOnlyState(s: TextOnlyState): void {
  try { fs.writeFileSync(TEXT_ONLY_STATE_FILE, JSON.stringify(s), "utf8") } catch {}
}

function textHasResultMarkers(text: string): { found: boolean; count: number } {
  let count = 0
  const lower = text.toLowerCase()
  const markers = ["task result","subagent result","workflow result","task_result","subagent_result","workflow_result","<result>","</result>"]
  for (const marker of markers) {
    const lowerMarker = marker.toLowerCase()
    let idx = 0
    while ((idx = lower.indexOf(lowerMarker, idx)) !== -1) {
      count++
      idx += lowerMarker.length
    }
  }
  return { found: count > 0, count }
}

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "event": async (input: any) => {
    const event = input.event
    if (event && event.type === "session.idle") {
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

        const watchdogDisengage = isWatchdogDisengaged()

        const state = {
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
    if (isSubagent()) return
    console.log("SUBAGENT SKIP: enforce-stop")
    reportAlive("enforce-stop")
    writeHeartbeat("enforce-stop")

    const persistBlock = readPersistBlock()
    if (persistBlock.blocked) {
      const isDispatch = isDispatchTool(input.tool)
      if (isDispatch) {
        clearPersistBlock()
      } else {
        try {
          const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
          let data: Record<string, any> = { allowed: 0, blocked: 0 }
          if (fs.existsSync(cPath)) { try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {} }
          data.blocked = (parseInt(data.blocked, 10) || 0) + 1
          data.last_blocked = { tool: input.tool, reason: `persist-stop-block: ${persistBlock.reason}`, ts: Date.now(), iso: new Date().toISOString() }
          fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
        } catch {}
        return {
          permissionDecision: "deny" as const,
          message: [
            "⛔ BLOCKED: stop-pattern detected in previous response.",
            `Reason: ${persistBlock.reason}`,
            "",
            "You sent a text-only response with pending work (blanked by enforce-stop).",
            "The ONLY valid next action is to DISPATCH SUBAGENTS via task/agent/workflow.",
            "All other tool calls (read, edit, bash) are denied until you dispatch.",
          ].join("\n"),
        }
      }
    }

    try {
      try {
        const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
        let data: Record<string, any> = { allowed: 0, blocked: 0, last_fired: null as any, ts: 0 }
        if (fs.existsSync(cPath)) {
          try { const d = JSON.parse(fs.readFileSync(cPath, "utf8")); data = d } catch {}
        }
        const now = new Date().toISOString()
        data.last_fired = { tool: input.tool, ts: Date.now(), iso: now }
        data.ts = Date.now()
        data._outcome = "allowed"
        fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
      } catch {}

      if (turnState.blocked) {
        turnState.blocked = false
        clearPersistBlock()
      }

      turnState.accumulatedText = ""
      turnState.toolCallMade = true

      if (isDispatchTool(input.tool)) {
        turnState.dispatchCount++
      }

      const streakState = updateSharedStreak(input.tool, "enforce-stop")

      if (input.tool === "question") {
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
          const disengaged = isWatchdogDisengaged()
          if (!disengaged && (taskMd || ratchetCount > 0 || bugsOpen || gateRed || ciBad || repoPending)) {
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
            if (gateRed) {
              if (gateStatusIsStale()) {
                extraReasons.push("gate stale (>5min); run make gate-refresh to update lint/typecheck/collect")
              } else {
                extraReasons.push("gate RED")
              }
            }
            if (ciBad) extraReasons.push("CI pending/red")
            if (repoPending) extraReasons.push("repo dirty")
            throw new Error(stopLikeDenyMessage(taskMd, ratchetCount, extraReasons))
          }
        }
      }

      const isMutationTool = !isDispatchTool(input.tool)
        && !isReadTool(input.tool)
        && input.tool !== "question"
      if (isMutationTool) {
        const grindingDisengaged = isWatchdogDisengaged()
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
    if (isSubagent()) return output
    console.log("SUBAGENT SKIP: enforce-stop")
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

  "experimental.text.complete": async (_input: any, output: any) => {
    if (isSubagent()) return output
    console.log("SUBAGENT SKIP: enforce-stop")
    if (/^(⛔|HARD STOP|MUST DISPATCH|ENHANCEMENT RATIO|████|BLOCKED:|MULTITASK|INSUFFICIENT DISPATCHES|ZERO-DISPATCH|DISPATCH SUBAGENTS|EARLY ENHANCEMENT|DELEGATE-FIRST|REFILL NEEDED|AFTER-RESULTS|CONSECUTIVE TEXT-ONLY|FALSE-DONE|QA RESPONSE)/.test((output?.text ?? "").trim())) return output

    const cPath = process.env.GLUDD_STOP_TEXT_COMPLETE_COUNT || "/tmp/gludd-stop-text-complete-count.json"
    let count = 1
    if (fs.existsSync(cPath)) {
      try { const d = JSON.parse(fs.readFileSync(cPath, "utf8")); count = (parseInt(d.count, 10) || 0) + 1 } catch {}
    }
    fs.writeFileSync(cPath, JSON.stringify({ count, last_fired: new Date().toISOString(), ts: Date.now() }), "utf8")

    if (!STOP_ENFORCE || !NO_WAIT_ENFORCE) return
    const text = output.text
    if (!text || text.trim().length === 0) return

    if (isDisengaged()) return

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

    const postResultsState = readPostResultsState()
    const isTextOnlyThisTurn1 = !turnState.toolCallMade && turnState.dispatchCount === 0
    if ((postResultsState.lastTurnHadResults || postResultsState.lastTurnHadWave) && isTextOnlyThisTurn1) {
      updateSharedStreak("text-only", "enforce-stop")
      logFalseDoneBlock(text, "after-results-text-only")
      recordBlock("after-results-text-only")
      output.text = [
        "⛔ AFTER-RESULTS TEXT-ONLY BLOCKED — RESUME WORK: dispatch subagents immediately.",
        "",
        `Previous turn had results: ${postResultsState.lastTurnHadResults}, wave: ${postResultsState.lastTurnHadWave}`,
        "You sent a text-only response after receiving subagent results.",
        "DISPATCH THE NEXT WAVE VIA task/agent/workflow NOW.",
      ].join("\n")
      turnState.blocked = true
      writePersistBlock(true, "after-results-text-only")
      return
    }

    if (text.trim().length < 60) {
      if (responseLooksTerminal(text)) {
        logFalseDoneBlock(text, "short-false-done")
        output.text = "⛔ FALSE-DONE CLAIM BLOCKED. DISPATCH A TOOL CALL."
        turnState.blocked = true
        writePersistBlock(true, "short-false-done")
        return
      }
    }

    turnState.accumulatedText += text

    const watchdogDisengage = isWatchdogDisengaged()

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

    const textOnly = readTextOnlyState()
    if (!turnState.toolCallMade && turnState.dispatchCount === 0) {
      const now = Date.now()
      const sameSession = textOnly.lastTs > 0 && (now - textOnly.lastTs) < 300_000
      const newCount = sameSession ? textOnly.count + 1 : 1
      textOnly.count = newCount
      textOnly.lastTs = now
      textOnly.sameSession = sameSession
      writeTextOnlyState(textOnly)
      if (!watchdogDisengage && textOnly.count >= 2 && (cache.hasLocalWork || cache.ciVerdictPendingOrRed)) {
        logFalseDoneBlock(turnState.accumulatedText, "consecutive-text-only")
        recordBlock("consecutive-text-only")
        output.text = [
          "⛔ CONSECUTIVE TEXT-ONLY RESPONSES BLOCKED.",
          `Count: ${newCount} text-only responses in ${Math.round((now - textOnly.lastTs) / 1000)}s.`,
          "",
          "Max 1 text-only response allowed when work is pending.",
          "DISPATCH A TOOL CALL NOW.",
        ].join("\n")
        turnState.blocked = true
        writePersistBlock(true, "consecutive-text-only")
        return
      }
      writeTextOnlyState({ count: 0, lastTs: 0, sameSession: false })
    }

    const combinedText = text + turnState.accumulatedText
    const lower = combinedText.toLowerCase()

    const hasStopPatternPhrase = STOP_PATTERN_PHRASES.test(combinedText)
    const hasDirectFalseDone = responseLooksTerminal(combinedText) || hasStopPatternPhrase

    const tasksMdUnchecked = cache?.tasksMdUnchecked ?? tasksMdHasUnchecked()
    const ratchetCount = cache?.ratchetEntries ?? ratchetHasEntries()

    if (!watchdogDisengage && hasDirectFalseDone) {
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
        writePersistBlock(true, "direct-false-done-no-evidence")
        return
      }

    }

    const repoPending = cache?.repoPending ?? false
      const gateRed = cache?.gateStatusRed ?? false
      const ciVerdictPendingOrRed = cache?.ciVerdictPendingOrRed ?? false
      const hasLocalWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || gateRed

      if (turnState.blocked) {
        const combined = (text + turnState.accumulatedText).toLowerCase()
        if (/\b(make git-|dispatch|subagent|task)\b/.test(combined)) {
          turnState.blocked = false
          clearPersistBlock()
        } else {
          output.text = ""
          return
        }
      }

      if (!watchdogDisengage && ratchetCount > 0 && turnState.dispatchCount === 0) {
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
        writePersistBlock(true, "ratchet-block-all-text")
        return
      }

      if (turnState.dispatchCount > 0) {
        if (!COMPLETION_VERBATIM.test(text)) return
      }

      if (turnState.toolCallMade) {
        if (!COMPLETION_VERBATIM.test(text)) return
      }

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
        writePersistBlock(true, "qa-response-summary-stop")
        return
      }

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
      const lateHasWorkArtifact = lateHasFilePath || lateHasCommandMarker || lateHasSubagentReportMarker
      const isSubagentFinalReport = lateHasStructuredEvidence || lateHasWorkArtifact

      if (ciVerdictPendingOrRed && !isSubagentFinalReport) {
        const STALE_CI_MS = 600_000
        const ciCachePath = "/tmp/gludd-watchdog-ci.json"
        let ciStatus = "RED"
        let ciLastCheck = 0
        try {
          if (fs.existsSync(ciCachePath)) {
            const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
            ciStatus = ciData.last_ci_status || "UNKNOWN"
            ciLastCheck = ciData.last_ci_check || 0
          }
        } catch {}
        const ciIsStale = (Date.now() - ciLastCheck) > STALE_CI_MS
        const ciIsRed = ciStatus !== "SUCCESS" && ciStatus !== "PENDING"
        if (ciIsRed && !ciIsStale) {
          logFalseDoneBlock(combinedText, "ci-red-text-only")
          recordBlock("ci-red-false-done")
          output.text = [
            "⛔ CI RED — COMPLETION CLAIM BLOCKED.",
            "",
            `CI status: ${ciStatus} (checked ${Math.round((Date.now() - ciLastCheck) / 1000)}s ago)`,
            "",
            "CI is RED. The pipeline is not green — no completion or done-claim",
            "is valid until CI passes. Fix the CI failure, wait for green, then report.",
            "DISPATCH A TOOL CALL NOW.",
          ].join("\n")
          turnState.blocked = true
          writePersistBlock(true, "ci-red-text-only")
          return
        }
        if (ciStatus === "PENDING" && !ciIsStale) {
          logFalseDoneBlock(combinedText, "ci-pending-text-only")
          recordBlock("ci-pending-false-done")
          output.text = [
            "⛔ CI PENDING — COMPLETION CLAIM BLOCKED.",
            "",
            `CI status: PENDING (checked ${Math.round((Date.now() - ciLastCheck) / 1000)}s ago)`,
            "",
            "CI has not finished running. The pipeline verdict is unknown —",
            "no completion or done-claim is valid until CI returns green.",
            "Continue work in the meantime. DISPATCH A TOOL CALL NOW.",
          ].join("\n")
          turnState.blocked = true
          writePersistBlock(true, "ci-pending-text-only")
          return
        }
      }

      if (!watchdogDisengage && (hasLocalWork || ciVerdictPendingOrRed) && !isSubagentFinalReport) {
        logFalseDoneBlock(turnState.accumulatedText, "hasLocalWork-text-only")
        output.text = [
          "HARD STOP — STATE-BASED BLOCK: local work pending.",
          `TASKS.md unchecked: ${tasksMdUnchecked ? "yes" : "no"}`,
          `ratchet entries: ${ratchetCount}`,
          "DISPATCH SUBAGENTS NOW.",
        ].join("\n")
        turnState.blocked = true
        writePersistBlock(true, "state-based-block")
        return
      }
      const combinedTextForResults = turnState.accumulatedText
      const resultCheck = textHasResultMarkers(combinedTextForResults)
      if (resultCheck.found) {
        writePostResultsState({
          lastTurnHadResults: true,
          lastTurnHadWave: resultCheck.count >= WAVE_RESULT_THRESHOLD,
          lastTurnTs: Date.now(),
          lastResultCount: resultCheck.count,
        })
      } else {
        writePostResultsState({
          lastTurnHadResults: false,
          lastTurnHadWave: false,
          lastTurnTs: Date.now(),
          lastResultCount: 0,
        })
      }

      turnState.toolCallMade = false
      turnState.dispatchCount = 0
  },
}

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
/**
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
 * check /tmp/gludd-hot-enforce-stop.js on every invocation.  If present
 * and newer than cached, the hot module's hook overrides the compiled-in
 * default.  Run `make hot-reload-plugins` after editing this file.
 */
export default (async ({ }) => {
  spawnGateRefresh()
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
    event: async (input: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("enforce-stop", defaultImpl)
      const fn = impl["event"]
      return fn ? await fn(input) : undefined
    },
    "tool.execute.before": async (input: any, output: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("enforce-stop", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
    "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output
      const impl = loadHotModule("enforce-stop", defaultImpl)
      const fn = impl["experimental.chat.system.transform"]
      return fn ? await fn(_input, output) : output
    },
    "experimental.text.complete": async (_input: any, output: any) => {
      if (isSubagent()) return output
      const impl = loadHotModule("enforce-stop", defaultImpl)
      const fn = impl["experimental.text.complete"]
      return fn ? await fn(_input, output) : output
    },
  }
}) satisfies Plugin