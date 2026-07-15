/**
 * enforce-stop.ts — COMPREHENSIVE stop-pattern and completion-claim enforcement.
 *
 * VULNERABILITY FIXED (2026-07-15): text.complete bypass via short-text
 * exemption and WORK_STATE_CACHE eliminated.
 *
 * VULNERABILITY FIXED (2026-07-14): todowrite bypass eliminated.
 * The old plugin checked todowrite state but not actual project state.
 * An agent marking all todos as "completed" in todowrite could bypass
 * all stop detection even when CI was RED, beta.1 had only 1/12 assets,
 * and 300+ spec items remained unimplemented.
 *
 * FIX: hasRealPendingWork() reads TASKS.md UNCONDITIONALLY on every
 * invocation (NO caching). The todowrite state is NEVER consulted.
 * The text.complete hook blocks ALL text-only responses (0 tool calls)
 * when hasRealPendingWork() returns true — regardless of text length,
 * regardless of text content. Short-text exemption REMOVED.
 * COMPLETION_SMELL check blocks any completion-adjacent language
 * (substring "complete", "done", "finished", "ready", etc.) when
 * hasRealPendingWork() is true — fires even for short text.
 *
 * WHAT IS BLOCKED:
 *   1. ALL text-only responses when hasRealPendingWork() returns true
 *   2. Completion-word claims (committed, done, landed, etc.) without evidence
 *   3. QA-response-summary patterns ("completed in this session", etc.)
 *   4. COMPLETION_SMELL: any completion-adjacent substring with pending work
 *   5. Stop-like make targets (commit, push, release) with pending work
 *   6. Main-thread grinding (too many consecutive non-dispatch calls)
 *
 * Hot-reload capable. Subagent context skips enforcement.
 * GLUDD_STOP_ENFORCE=0 disables ALL enforcement.
 */
import * as fs from "node:fs"
import * as path from "node:path"
import { execSync, spawn } from "node:child_process"
import type { Plugin } from "@opencode-ai/plugin"
import {
  isSubagent,
  isDisengaged as isWatchdogDisengaged,
  reportAlive,
  writeHeartbeat,
  updateSharedStreak,
  isDispatchTool,
  isReadTool,
  readJsonFile,
  writeJsonFile,
} from "../lib/shared.ts"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"

// ── File paths ──────────────────────────────────────────────────────────────

const STATE_FILE = process.env.GLUDD_STOP_STATE_FILE || "/tmp/gludd-stop-state.json"
const BLOCK_COUNTER_FILE = process.env.GLUDD_BLOCK_COUNTER_FILE || "/tmp/gludd-block-counter.json"
const BLOCK_REASON_FILE = process.env.GLUDD_BLOCK_REASON_FILE || "/tmp/gludd-block-reason.json"
const PERSIST_BLOCK_FILE = process.env.GLUDD_PERSIST_STOP_BLOCK_FILE || "/tmp/gludd-persist-stop-block.json"
const FALSE_DONE_BLOCKS_FILE = "/tmp/gludd-false-done-blocks.json"
const BLANKED_RESPONSE_FILE = "/tmp/gludd-blanked-responses.json"
const TEXT_COMPLETE_COUNT_FILE = process.env.GLUDD_STOP_TEXT_COMPLETE_COUNT || "/tmp/gludd-stop-text-complete-count.json"
const SESSION_BLOCK_COUNTER_FILE = "/tmp/gludd-stop-session-blocks.json"

const COMPLETION_WORDS_RE = /\b(?:committed|done|completed|landed|pushed|shipped|deployed|fixed|resolved|passed|working|green|verified|ready for review|all good|all set|no further|finished|wrapped|all tasks)\b/
const SHORT_COMPLETION_PHRASES = /\b(?:all done\.?|done\.|finished\.|complete\.|all set\.|all good\.|good to go\.?|ready to ship\.?|ready for review\.?|everything is (?:done|complete|ready|set|good)|i'm done\.?|we're done\.?|that's (?:done|complete|it|all)|no more work|nothing more|all finished)\b/i

const QA_RESPONSE_PATTERNS = /(?:completed in this session|done since the (?:crash|last session)|everything (?:committed|landed|pushed|shipped|is complete)|here.{0,30}(?:what was|.?s what) (?:done|completed|changed)|summary of what was (?:done|completed)|what.{0,10}(?:changed|done|completed|left|remains|is next|\?s left))/i

const EVIDENCE_PATTERNS = [
  /\b[0-9a-f]*[a-f][0-9a-f]{6,39}\b/,
  /VERIFIED\s+\w+@/,
  /CI\s+(?:GREEN|RED|PENDING)/,
  /\d+\s+passed/,
  /===\s*(?:GATE|GATE-LITE):\s*(?:PASSED|FAILED)/,
  /Collection OK/,
  /All checks passed/,
  /Success: no issues found/,
  /\b\w+@[0-9a-f]{7,40}\b/,
  /conclusion:\s*(?:success|failure)/,
  /headSha.*matched/,
]

const COMPLETION_SMELL_RE = /\b(?:complete|done|finished|ready|landed|shipped|pushed|committed|fixed|passed|passing|working|green|resolved|deployed|verified|wrapped|all done|all set|all good|all tasks|continuing|no more|nothing more|RED|beta|alpha)\b/i

const DELEGATE_FIRST_THRESHOLD = 8
const GRINDING_HARD_DENY_THRESHOLD = 12
const ESCALATION_THRESHOLD = 3

// ── Session-level block escalation counter ──────────────────────────────────

function incrementSessionBlockCounter(): number {
  try {
    let data = readJsonFile<{ count: number }>(SESSION_BLOCK_COUNTER_FILE, { count: 0 })
    data.count++
    writeJsonFile(SESSION_BLOCK_COUNTER_FILE, data)
    return data.count
  } catch { return 0 }
}

function getSessionBlockCount(): number {
  try {
    return readJsonFile<{ count: number }>(SESSION_BLOCK_COUNTER_FILE, { count: 0 }).count
  } catch { return 0 }
}

// ── Spot gate refresh (background) ──────────────────────────────────────────

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
  } catch {}
}

// ── Persist block flag ──────────────────────────────────────────────────────

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

// ── Block counter (false-positive cascade detection) ────────────────────────

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
      const MAX_DISENGAGE = now + 3_600_000
      if (c.disengageUntil > MAX_DISENGAGE) {
        c.disengageUntil = MAX_DISENGAGE
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

function logFalseDoneBlock(reason: string, textSnippet: string): void {
  try {
    const existing = readJsonFile<any[]>(FALSE_DONE_BLOCKS_FILE, [])
    existing.push({ reason, text: textSnippet.substring(0, 200), ts: Date.now(), iso: new Date().toISOString() })
    writeJsonFile(FALSE_DONE_BLOCKS_FILE, existing)
  } catch {}
}

function logBlankedResponse(reason: string, textSnippet: string): void {
  try {
    const existing = readJsonFile<any[]>(BLANKED_RESPONSE_FILE, [])
    existing.push({ reason, text: textSnippet.substring(0, 200), ts: Date.now(), iso: new Date().toISOString() })
    writeJsonFile(BLANKED_RESPONSE_FILE, existing)
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
  return c.disengageUntil > now
}

// ── Increment text.complete fire counter ────────────────────────────────────

function incrementTextCompleteCount(): void {
  try {
    let data = readJsonFile<{ count: number }>(TEXT_COMPLETE_COUNT_FILE, { count: 0 })
    data.count++
    writeJsonFile(TEXT_COMPLETE_COUNT_FILE, data)
  } catch {}
}

// ── WORK-STATE CHECKERS (filesystem, no tоdowrite dependency) ───────────────

interface WorkState {
  tasksMdUnchecked: boolean
  tasksMdUncheckedCount: number
  ratchetEntries: number
  bugsOpen: boolean
  gateStatusMissing: boolean
  gateStale: boolean
  gateStatusRed: boolean
  ciVerdictPendingOrRed: boolean
  releaseIncomplete: boolean
  testFailures: boolean
  repoPending: boolean
  hasPendingWork: boolean
  hasLocalWork: boolean
  healthScore: number
  ts: number
}

function hasRealPendingWork(): WorkState {
  const now = Date.now()

  // UNCONDITIONAL filesystem read — NO caching. Read TASKS.md every time.
  const cwd = process.cwd()
  let tasksMdUnchecked = false
  let tasksMdUncheckedCount = 0
  let ratchetEntries = 0
  let bugsOpen = false
  let gateStatusMissing = false
  let gateStale = false
  let gateStatusRed = false

  try {
    const tasksPath = path.join(cwd, "TASKS.md")
    if (fs.existsSync(tasksPath)) {
      const content = fs.readFileSync(tasksPath, "utf8")
      const matches = content.match(/^[-*]\s+\[ \]/gm)
      if (matches) {
        tasksMdUncheckedCount = matches.length
        tasksMdUnchecked = true
      }
    }
  } catch {}

  try {
    const ratchetPath = path.join(cwd, "config", "ratchet.yml")
    if (fs.existsSync(ratchetPath)) {
      const content = fs.readFileSync(ratchetPath, "utf8")
      ratchetEntries = content.split("\n").filter(
        l => l.trim() && !l.trim().startsWith("#") && (l.includes("::") || /^\w[\w\s]*:\s/.test(l))
      ).length
    }
  } catch {}

  try {
    const bugsPath = path.join(cwd, "BUGS.md")
    if (fs.existsSync(bugsPath)) {
      const content = fs.readFileSync(bugsPath, "utf8")
      const openIncidents = content
        .split("\n")
        .filter(l => /^###\s+\d{4}-\d{2}-\d{2}\s+[-—]/.test(l))
        .filter(l => !l.includes("(resolved)"))
      bugsOpen = openIncidents.length > 0
    }
  } catch {}

  try {
    const gatePath = path.join(cwd, ".gate-status")
    if (!fs.existsSync(gatePath)) {
      gateStatusMissing = true
    } else {
      const stat = fs.statSync(gatePath)
      gateStale = (now - stat.mtimeMs) > 1_800_000
      const content = fs.readFileSync(gatePath, "utf8")
      for (const line of content.split("\n")) {
        if (line.startsWith("===")) continue
        if (/^(lint |typecheck |collect |test |smoke |env-writes |dead-code |hook-runtime |verify-enforcement |coverage-gaps )/.test(line)) {
          if (/FAIL/.test(line)) { gateStatusRed = true; break }
        }
      }
    }
  } catch {}

  let ciVerdictPendingOrRed = false
  try {
    const ciCachePath = "/tmp/gludd-watchdog-ci.json"
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const lastCheck = ciData.last_ci_check || 0
      const lastStatus = ciData.last_ci_status || ""
      if (now - lastCheck < 120_000 && lastStatus) {
        ciVerdictPendingOrRed = lastStatus !== "SUCCESS"
      }
    }
  } catch {}

  let releaseIncomplete = false
  try {
    const releaseCheckPath = "/tmp/gludd-release-completeness.json"
    if (fs.existsSync(releaseCheckPath)) {
      const rd = JSON.parse(fs.readFileSync(releaseCheckPath, "utf8"))
      if (now - (rd.ts || 0) < 300_000 && rd.incomplete) {
        releaseIncomplete = true
      }
    }
  } catch {}

  let testFailures = false
  try {
    const lastTestPath = "/tmp/gludd-last-test-result.json"
    if (fs.existsSync(lastTestPath)) {
      const td = JSON.parse(fs.readFileSync(lastTestPath, "utf8"))
      if (td.failures > 0) testFailures = true
    }
  } catch {}

  let repoPending = false
  try {
    const status = execSync("git status --porcelain", {
      cwd, encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
    }) as string
    repoPending = status.trim().split("\n").filter(l => l.trim().length > 0).length > 0
  } catch {}

  const hasLocalWork = tasksMdUnchecked || ratchetEntries > 0 || bugsOpen || gateStatusRed
  const hasPendingWork = hasLocalWork || ciVerdictPendingOrRed || releaseIncomplete || testFailures || repoPending
  let healthScore = 100
  if (gateStatusRed) healthScore -= 30
  if (gateStale) healthScore -= 10
  if (ciVerdictPendingOrRed) healthScore -= 20
  if (tasksMdUnchecked) healthScore -= 10
  if (bugsOpen) healthScore -= 10
  if (repoPending) healthScore -= 5
  if (healthScore < 0) healthScore = 0

  const state: WorkState = {
    tasksMdUnchecked, tasksMdUncheckedCount, ratchetEntries, bugsOpen,
    gateStatusMissing, gateStale, gateStatusRed,
    ciVerdictPendingOrRed, releaseIncomplete, testFailures, repoPending,
    hasPendingWork, hasLocalWork, healthScore, ts: now,
  }

  try {
    fs.writeFileSync(STATE_FILE, JSON.stringify(state), "utf8")
  } catch {}

  return state
}

// ── COMPLETION VERBATIM detection ──────────────────────────────────────────

function responseLooksTerminal(text: string): boolean {
  const t = text.trim().toLowerCase()
  if (COMPLETION_WORDS_RE.test(t)) return true
  if (SHORT_COMPLETION_PHRASES.test(t)) return true
  if (QA_RESPONSE_PATTERNS.test(t)) return true
  return false
}

function hasStructuredEvidence(text: string): boolean {
  return EVIDENCE_PATTERNS.some(p => p.test(text))
}

// ── QUESTION DENY ──────────────────────────────────────────────────────────

const QUESTION_DENY_REASON = [
  "BLOCKING QUESTION DENIED — user standing directive: never interrupt work to",
  "ask. DEFAULT TO ACTION: choose the most reasonable option yourself, state in",
  "one line the assumption you are making, and PROCEED.",
].join(" ")

// ── STOP-LIKE TOOL DENY ────────────────────────────────────────────────────

const STOP_LIKE_TARGETS_RE = /^make\s+(git-commit|commit-no-verify|ship-commit|git-push-branch|git-push-branch-nv|git-push-sandboxcom|git-push-sandboxcom-main|git-push-master|git-tag-push|release-cut|release-promote|test-and-commit|repo-commit|feature-done|release-recut|release-branch-new|git-merge)(\s|$)/
const COMMIT_TARGET_RE = /^make\s+(git-commit|commit-no-verify|git-commit-file|test-and-commit|repo-commit|feature-done|git-merge)(\s|$)/
const PUSH_TARGET_RE = /^make\s+(git-push-branch|git-push-branch-nv|git-push-sandboxcom|git-push-sandboxcom-main|git-push-master|git-tag-push|release-cut|release-promote|ship-commit|release-recut|release-branch-new)(\s|$)/

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

// ── Legacy checkers (used by tool.execute.before for backwards compat) ──────

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

function gateStatusIsRed(): boolean {
  try {
    const gatePath = path.join(process.cwd(), ".gate-status")
    if (!fs.existsSync(gatePath)) return false
    const content = fs.readFileSync(gatePath, "utf8")
    for (const line of content.split("\n")) {
      if (line.startsWith("===")) continue
      if (/^(lint |typecheck |collect |test |smoke |env-writes |dead-code |hook-runtime |verify-enforcement |coverage-gaps )/.test(line)) {
        if (/FAIL/.test(line)) return true
      }
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

function ciIsPendingOrRed(): boolean {
  try {
    const ciCachePath = "/tmp/gludd-watchdog-ci.json"
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const lastCheck = ciData.last_ci_check || 0
      const lastStatus = ciData.last_ci_status || ""
      if (Date.now() - lastCheck < 120_000 && lastStatus) {
        return lastStatus !== "SUCCESS"
      }
    }
  } catch {}
  return false
}

function repoHasPendingWork(inExecSync: any, mode?: "commit" | "push"): boolean {
  try {
    const cwd = process.cwd()
    try {
      const status = inExecSync("git status --porcelain", {
        cwd, encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
      }) as string
      const lines = status.trim().split("\n").filter(l => l.trim().length > 0)
      if (lines.length === 0) return false
      if (mode === "commit") {
        return lines.some(l => {
          const y = l.length > 1 ? l[1] : " "
          return y !== " "
        })
      }
      return true
    } catch {}
    return false
  } catch { return false }
}

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  // ── experimental.chat.system.transform ────────────────────────────────────

  "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    reportAlive("enforce-stop")
    writeHeartbeat("enforce-stop")

    const workState = hasRealPendingWork()
    const hasWork = workState.hasPendingWork || workState.hasLocalWork

    if (typeof output === "string") {
      const subagentResultMarkers = /(?:task_id|task_result|agent\s+result|subagent\s+result|task\s+completed|generated|completed successfully|exit code)/i
      if (subagentResultMarkers.test(output)) return output

      if (hasWork) {
        const indicators: string[] = []
        if (workState.tasksMdUnchecked) indicators.push(`${workState.tasksMdUncheckedCount} unchecked TASKS.md items`)
        if (workState.ratchetEntries > 0) indicators.push(`${workState.ratchetEntries} ratchet entries`)
        if (workState.bugsOpen) indicators.push("BUGS.md open incidents")
        if (workState.gateStatusRed) indicators.push("gate RED")
        if (workState.gateStale) indicators.push("gate stale")
        if (workState.gateStatusMissing) indicators.push("gate missing")
        if (workState.ciVerdictPendingOrRed) indicators.push("CI pending/red")
        if (workState.releaseIncomplete) indicators.push("release incomplete")
        if (workState.testFailures) indicators.push("test failures")
        if (workState.repoPending) indicators.push("repo dirty")
        const sessionBlockCount = getSessionBlockCount()
        const escalation = sessionBlockCount > ESCALATION_THRESHOLD
          ? `\n🚨 ESCALATION ACTIVE: ${sessionBlockCount} stop attempts blocked this session. DO NOT attempt to stop.\n`
          : ""
        const block = [
          "",
          "══════════════════════════════════════════════════════════════",
          "⛔⛔⛔ MANDATORY PRE-GENERATION GATE ⛔⛔⛔",
          "══════════════════════════════════════════════════════════════",
          "",
          `PENDING WORK EXISTS: ${indicators.join(", ")}.`,
          `Health score: ${workState.healthScore}/100.`,
          "",
          "⛔ ROOT-CAUSE-ONLY: Every fix must address the root cause, never the symptom.",
          "  - Guardrail errors? Fix the logic, don't disable the guardrail.",
          "  - CI red? Fix the failing tests, don't skip/weaken them.",
          "  - Release blocked? Fix the blocker, don't bypass it (--no-verify, FORCE=1).",
          "  - Todowrite bypass? hasRealPendingWork() checks filesystem, not todowrite.",
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
          escalation,
          "YOU HAVE BEEN WARNED. GENERATE A TOOL CALL NOW.",
          "══════════════════════════════════════════════════════════════",
          "",
          output,
        ].join("\n")
        return block
      }
      return `[orchestration] No pending work. Normal operation.\n\n${output}`
    }
    return output
  },

  // ── experimental.text.complete ────────────────────────────────────────────

  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    if (process.env.GLUDD_STOP_ENFORCE === "0") return undefined
    incrementTextCompleteCount()
    reportAlive("enforce-stop")
    writeHeartbeat("enforce-stop")

    const text = typeof output === "string" ? output
      : (output as any)?.text ? String((output as any).text)
      : JSON.stringify(output)

    if (!text || text.trim().length === 0) return output
    const trimmed = text.trim()

    // Disengage check — if disengaged, allow through (false-positive cascade escape)
    // *** NARROWED 2026-07-15: disengage ONLY bypasses heuristic checks below,
    // NOT the fundamental hasRealPendingWork text-only block (line 726).
    // Previously isDisengaged() at line 632 returned early before ANY checks,
    // allowing text-only responses through while CI was RED and work existed.
    const disengaged = isDisengaged()

    const workState = hasRealPendingWork()

    // ── SHORT FALSE-DONE PATH: short text with completion verbatim ──────────
    if (!disengaged && trimmed.length < 60 && responseLooksTerminal(text) && !hasStructuredEvidence(text)) {
      recordBlock("short-false-done")
      logFalseDoneBlock("short-false-done", text)
      writePersistBlock(true, "short-false-done")

      return {
        text: [
          "⛔ FALSE-DONE CLAIM BLOCKED — short completion phrase without evidence.",
          "",
          `PENDING WORK: ${workState.tasksMdUncheckedCount} unchecked tasks, ` +
          `${workState.ratchetEntries} ratchet entries, ` +
          `CI ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : "N/A"}, ` +
          `gate ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : "OK"}.`,
          "",
          "You MUST dispatch a tool call. Do not claim work is done.",
          "Run: Task tool to dispatch subagents for the pending work.",
        ].join("\n"),
      }
    }

    // ── QA RESPONSE PATTERNS: "completed in this session", etc ─────────────
    if (!disengaged && QA_RESPONSE_PATTERNS.test(text)) {
      if (workState.hasLocalWork || workState.ciVerdictPendingOrRed) {
        recordBlock("qa-response-summary-stop")
        logFalseDoneBlock("qa-response-summary-stop", text)
        writePersistBlock(true, "qa-response-summary-stop")

        return {
          text: [
            "⛔ QA RESPONSE SUMMARY BLOCKED — DO NOT STOP TO SUMMARIZE.",
            "",
            `Real work still exists: ${workState.tasksMdUncheckedCount} unchecked tasks, ` +
            `${workState.ratchetEntries} ratchet entries, ` +
            `gate ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : "OK"}, ` +
            `CI ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : "N/A"}.`,
            "",
            "DISPATCH A TOOL CALL NOW. Do not send a text-only summary.",
          ].join("\n"),
        }
      }
    }

    // ── COMPLETION WORDS WITHOUT EVIDENCE ──────────────────────────────────
    if (!disengaged && responseLooksTerminal(text) && !hasStructuredEvidence(text)) {
      recordBlock("completion-without-evidence")
      logFalseDoneBlock("completion-without-evidence", text)
      writePersistBlock(true, "completion-without-evidence")

      return {
        text: [
          "⛔ BLOCKED: completion claim without verification evidence.",
          "",
          "Words like 'committed', 'done', 'pushed', 'passed', 'green' etc.",
          "require machine-produced evidence in the SAME response:",
          "- commit hash (7+ hex chars),",
          "- test pass counts ('42 passed'),",
          "- CI verdict (CI GREEN),",
          "- gate status (=== GATE: PASSED ===),",
          "- VERIFIED <branch>@<sha>.",
          "",
          "If work exists, DISPATCH A TOOL CALL instead of sending a summary.",
        ].join("\n"),
      }
    }

    // ── COMPLETION SMELL: any completion-adjacent substring without evidence ─
    if (!disengaged && COMPLETION_SMELL_RE.test(text) && !hasStructuredEvidence(text)) {
      recordBlock("completion-smell")
      logFalseDoneBlock("completion-smell", text)
      writePersistBlock(true, "completion-smell")

      return {
        text: [
          "⛔ BLOCKED: completion-adjacent language without evidence.",
          "",
          "Your text contains completion-adjacent words (complete, done, finished,",
          "ready, landed, shipped, pushed, committed, fixed, passed, working,",
          "green, RED, resolved, deployed, verified, continuing, alpha, beta, etc.)",
          "but NO machine-produced verification evidence.",
          "",
          "When hasRealPendingWork() is true, ANY completion-adjacent language",
          "in a text-only response is a HARD BLOCK — regardless of text length.",
          "",
          "DISPATCH A TOOL CALL. Do not send completion-adjacent text without evidence.",
        ].join("\n"),
      }
    }

    // ── REAL PENDING WORK BLOCK (text-only responses without evidence) ──────
    // DO NOT fire when: (a) text has structured evidence, or (b) no real pending work.
    if (workState.hasPendingWork && !hasStructuredEvidence(text)) {
      const sessionBlockCount = incrementSessionBlockCounter()
      const escalationNote = sessionBlockCount > ESCALATION_THRESHOLD
        ? `\n🚨 ESCALATION: ${sessionBlockCount} stop attempts blocked this session. COMPLIANCE REQUIRED.`
        : ""

      recordBlock("text-only-while-work-exists")
      logBlankedResponse("text-only-while-work-exists", text)
      writePersistBlock(true, "text-only-while-work-exists")

      return {
        text: [
          "⛔⛔⛔ TEXT-ONLY RESPONSE BLOCKED ⛔⛔⛔",
          "",
          `PENDING WORK EXISTS: ${workState.tasksMdUncheckedCount} unchecked TASKS.md items, ` +
          `${workState.ratchetEntries} ratchet entries, ` +
          `BUGS.md open: ${workState.bugsOpen ? "YES" : "no"}, ` +
          `gate: ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : workState.gateStale ? "STALE" : "OK"}, ` +
          `CI: ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : "N/A"}, ` +
          `release: ${workState.releaseIncomplete ? "INCOMPLETE" : "N/A"}, ` +
          `repo: ${workState.repoPending ? "DIRTY" : "clean"}.`,
          `Health: ${workState.healthScore}/100.`,
          "",
          "You may NOT send a text-only response while work exists.",
          "The ONLY valid action is to DISPATCH SUBAGENTS via the Task tool,",
          "or read/edit files to fix the pending work.",
          "NO short-text exemption. NO length exemption. NO content exemption.",
          escalationNote,
        ].join("\n"),
      }
    }

    return output
  },

  // ── tool.execute.before ──────────────────────────────────────────────────

  "tool.execute.before": async (input: any, output: any) => {
    if (isSubagent()) return
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
            "The ONLY valid next action is to DISPATCH SUBAGENTS via task/agent/workflow.",
            "All other tool calls are denied until you dispatch.",
          ].join("\n"),
        }
      }
    }

    if (input.tool === "question") {
      try {
        const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
        let data: Record<string, any> = { allowed: 0, blocked: 0 }
        if (fs.existsSync(cPath)) { try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {} }
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
        const taskMd = tasksMdHasUnchecked()
        const ratchetCount = ratchetHasEntries()
        const bugsOpen = bugsMdHasOpenIncidents()
        const gateRed = gateStatusIsRed()
        const ciBad = ciIsPendingOrRed()
        const repoMode: "commit" | "push" | undefined =
          COMMIT_TARGET_RE.test(command) ? "commit" :
          PUSH_TARGET_RE.test(command) ? "push" : undefined
        const repoPending = repoHasPendingWork(execSync, repoMode)
        const disengaged = isWatchdogDisengaged()
        if (!disengaged && (taskMd || ratchetCount > 0 || bugsOpen || gateRed || ciBad || repoPending)) {
          try {
            const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
            let data: Record<string, any> = { allowed: 0, blocked: 0 }
            if (fs.existsSync(cPath)) { try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {} }
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

    try {
      const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
      let data: Record<string, any> = { allowed: 0, blocked: 0 }
      if (fs.existsSync(cPath)) { try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {} }
      data.last_fired = { tool: input.tool, ts: Date.now(), iso: new Date().toISOString() }
      data.ts = Date.now()
      data._outcome = "allowed"
      fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
    } catch {}

    if (input.tool !== "question") {
      const streakState = updateSharedStreak(input.tool, "enforce-stop")
      const isMutationTool = !isDispatchTool(input.tool) && !isReadTool(input.tool)

      if (isMutationTool) {
        const grindingDisengaged = isWatchdogDisengaged()
        if (!grindingDisengaged) {
          if (streakState.streak > GRINDING_HARD_DENY_THRESHOLD) {
            try {
              const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
              let data: Record<string, any> = { allowed: 0, blocked: 0 }
              if (fs.existsSync(cPath)) { try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {} }
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
    }

    try {
      const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
      let data: Record<string, any> = { allowed: 0, blocked: 0 }
      if (fs.existsSync(cPath)) { try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {} }
      data.allowed = (parseInt(data.allowed, 10) || 0) + 1
      data.last_allowed = { ts: Date.now(), iso: new Date().toISOString() }
      fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
    } catch {}
  },

  // ── event (session.idle / session.created / session.deleted) ──────────────

  "event": async (input: unknown, _output: unknown) => {
    if (isSubagent()) return
    const ev = (input as any)?.event
    const evType = ev?.type || ""

    if (evType === "session.idle") {
      const workState = hasRealPendingWork()
      if (workState.hasPendingWork || workState.hasLocalWork) {
        const sessionBlockCount = getSessionBlockCount()
        const escalation = sessionBlockCount > ESCALATION_THRESHOLD
          ? ` (${sessionBlockCount} stop attempts blocked this session)`
          : ""
        console.warn(
          `⛔ SESSION IDLE WHILE WORK EXISTS${escalation}. ` +
          `${workState.tasksMdUncheckedCount} unchecked tasks, ` +
          `gate ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : "OK"}, ` +
          `CI ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : "N/A"}.`
        )
      }
      return
    }

    if (evType === "session.deleted") {
      return
    }
  },
}

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  spawnGateRefresh()
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-stop ` +
      `tool.execute.before+experimental.text.complete+experimental.chat.system.transform+event ` +
      `pid=${process.pid}\n`,
      "utf8",
    )
  } catch {}
  return {
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
    "experimental.text.complete": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output
      const impl = loadHotModule("enforce-stop", defaultImpl)
      const fn = impl["experimental.text.complete"]
      return fn ? await fn(_input, output) : output
    },
    "event": async (input: unknown, output: unknown) => {
      if (isSubagent()) return
      const impl = loadHotModule("enforce-stop", defaultImpl)
      const fn = impl["event"]
      return fn ? await fn(input, output) : undefined
    },
  }
}) satisfies Plugin
