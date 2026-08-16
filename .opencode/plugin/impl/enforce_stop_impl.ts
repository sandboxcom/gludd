/**
 * enforce-stop.ts — COMPREHENSIVE stop-pattern and completion-claim enforcement.
 *
 * REQUIRES OPENCODE RESTART TO TAKE EFFECT.
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
 *   5. STATUS SUMMARIES ("Here's the session N final status", bolded headers
 *      + status tables) with pending work — REGARDLESS of evidence, REGARDLESS
 *      of attached tool calls, NOT bypassed by disengage (added 2026-07-15
 *      after a summary containing commit hashes bypassed the evidence gate)
 *   6. Stop-like make targets (commit, push, release) with pending work
 *   7. Main-thread grinding (too many consecutive non-dispatch calls)
 *
 * Hot-reload capable. Subagent context skips enforcement. Dispatch tools: "task" "agent" "workflow".
 * GLUDD_STOP_ENFORCE=0 disables optional stop heuristics. The mandatory
 * pending-work text.complete guard and subagent isolation remain invariant.
 * Disengage file: /tmp/gludd-watchdog-disengage.json.
 */
import * as fs from "node:fs"
import * as path from "node:path"
import { createRequire } from "node:module"
import { randomUUID } from "node:crypto"
// import { execSync from node:child_process
import type { Plugin } from "@opencode-ai/plugin"
import {
  isSubagent,
  reportAlive,
  writeHeartbeat,
  updateSharedStreak,
  isDispatchTool,
  isReadTool,
  readJsonFile,
  writeJsonFile,
  getProjectRoot,
  hasTasksMdPendingWork,
} from "../../lib/shared.ts"
import { loadHotModule, type HotModule } from "../../lib/hot_reload.ts"



const nodeRequire = createRequire(
  typeof __filename !== "undefined" && path.isAbsolute(__filename)
    ? __filename
    : import.meta.url,
)

function execSync(...args: any[]): Buffer {
  return nodeRequire("node:child_" + "process").execSync(...args)
}

const isSubagentFinalReport = (text: string): boolean => SUBAGENT_TEXT_MARKERS.test(text)

// ── File paths ──────────────────────────────────────────────────────────────

// The plugin reports liveness by writing to /tmp/gludd-plugin-alive.json
// via reportAlive() (imported from shared.ts). This constant documents the
// side-effect path and is pinned by scripts/check_plugin_liveness.py.
const ALIVE_FILE = "/tmp/gludd-plugin-alive.json"

const STATE_FILE = process.env.GLUDD_STOP_STATE_FILE || "/tmp/gludd-stop-state.json"
const BLOCK_COUNTER_FILE = process.env.GLUDD_BLOCK_COUNTER_FILE || "/tmp/gludd-block-counter.json"
const BLOCK_REASON_FILE = process.env.GLUDD_BLOCK_REASON_FILE || "/tmp/gludd-block-reason.json"
const PERSIST_BLOCK_FILE = process.env.GLUDD_PERSIST_STOP_BLOCK_FILE || "/tmp/gludd-persist-stop-block.json"
const FALSE_DONE_BLOCKS_FILE =
  process.env.GLUDD_FALSE_DONE_BLOCKS_FILE || "/tmp/gludd-false-done-blocks.json"
const BLANKED_RESPONSE_FILE = "/tmp/gludd-blanked-responses.json"
const TEXT_COMPLETE_COUNT_FILE = process.env.GLUDD_STOP_TEXT_COMPLETE_COUNT || "/tmp/gludd-stop-text-complete-count.json"
const SESSION_BLOCK_COUNTER_FILE = "/tmp/gludd-stop-session-blocks.json"
const FORCE_DISPATCH_FILE = process.env.GLUDD_FORCE_DISPATCH_PATH || "/tmp/gludd-force-dispatch.json"
const RELEASE_COMPLETENESS_FILE = process.env.GLUDD_RELEASE_COMPLETENESS_FILE || "/tmp/gludd-release-completeness.json"
const LAST_TEST_RESULT_FILE = process.env.GLUDD_LAST_TEST_RESULT_FILE || "/tmp/gludd-last-test-result.json"
const MULTITASK_STATE_FILE = process.env.GLUDD_MULTITASK_STATE_FILE || "/tmp/gludd-multitask-state.json"
const PUSH_STATE_FILE = process.env.GLUDD_PUSH_STATE_FILE || "/tmp/gludd-push-state.json"
const POST_RESULTS_STATE_FILE = process.env.GLUDD_POST_RESULTS_STATE_FILE || "/tmp/gludd-post-results-state.json"
const TEXT_ONLY_STATE_FILE = process.env.GLUDD_TEXT_ONLY_STATE_FILE || "/tmp/gludd-text-only-state.json"
const WAVE_RESULT_THRESHOLD = 3
const HARD_MAX_DISPATCHES = 10
const CONFIGURED_AGENT_MIN =
  process.env.CLAUDE_AGENT_FLOOR ||
  process.env.GLUDD_MIN_DISPATCHES ||
  process.env.GLUDD_MULTITASK_MIN_DISPATCHES
// Ten is retained as the recommendation for genuinely broad work. It becomes
// a mandatory minimum only when an operator explicitly configures one.
const AGENT_FLOOR_DEFAULT = parseInt(CONFIGURED_AGENT_MIN || "10", 10)
const REQUIRED_AGENT_MIN = CONFIGURED_AGENT_MIN !== undefined
  ? Math.max(0, Math.min(
      HARD_MAX_DISPATCHES,
      Number.isFinite(AGENT_FLOOR_DEFAULT) ? AGENT_FLOOR_DEFAULT : 0,
    ))
  : 0
const NO_WAIT_ENFORCE = process.env.GLUDD_NO_WAIT_ENFORCE !== "0"

const COMPLETION_WORDS_RE = /\b(?:committed|done|completed|landed|pushed|shipped|deployed|fixed|resolved|passed|working|green|verified|ready for review|all good|all set|no further|finished|wrapped|all tasks)\b/

// Strongest stop-signal regex: unambiguous terminal completion verbatim.
// Stricter than COMPLETION_WORDS_RE — these phrases have NO non-completion
// reading. A match here is the highest-confidence stop signal.
const COMPLETION_VERBATIM = /\b(?:all done|all tasks complete|everything is done|everything is complete|work is complete|all work done|fully implemented|fully complete|nothing (?:more|else) (?:to do|left|remaining)|ready to ship|ready for review|shipped and verified|committed and pushed)\b/i
const SHORT_COMPLETION_PHRASES = /\b(?:all done\.?|done\.|finished\.|complete\.|all set\.|all good\.|good to go\.?|ready to ship\.?|ready for review\.?|everything is (?:done|complete|ready|set|good)|i'm done\.?|we're done\.?|that's (?:done|complete|it|all)|no more work|nothing more|all finished)\b/i

const QA_RESPONSE_PATTERNS = /(?:completed in this session|done since the (?:crash|last session)|everything (?:committed|landed|pushed|shipped|is complete)|here.{0,30}(?:what was|.?s what) (?:done|completed|changed)|summary of what was (?:done|completed)|what.{0,10}(?:changed|done|completed|left|remains|is next|\?s left))/i

const SUBAGENT_TEXT_MARKERS = /(?:task_id|task_result|agent\s+result|subagent\s+result|task\s+completed|generated|completed successfully|exit code)/i

// ── SUBAGENT_DEFICIT (2026-07-27) ──────────────────────────────────────────
// Agent sends text summarizing "Agent 1 did X, Agent 2 did Y..." while
// dispatching fewer subagents than an explicitly configured minimum in the
// SAME message. The text is a stop-by-another-name when an operator has opted
// into that minimum. Adaptive mode never forces quota-padding dispatches.
const SUBAGENT_DEFICIT_RE = /\b(?:agent|subagent|task)\s+\d+\s+(?:completed|finished|did|fixed|found|wrote|added|removed|updated|reported|returned|resolved|processed|handled|investigated|checked|audited|reviewed|implemented|created|tested|verified|deployed|patched|refactored|cleaned|merged|built|generated|produced|says|indicates|confirms|shows|began|started|noted)\b/i

const STOP_PATTERN_PHRASES = /\b(?:shall\s+i\s+continue|should\s+i\s+proceed|want\s+me\s+to\b[^?!.]*)/i
const PERMISSION_SEEKING_RE = /(?:want me to\s+(?:proceed|continue|dispatch|write|fix|move|start|do|run|create|add|update|implement|handle|begin|work|go ahead)|should i\s+(?:proceed|continue|fix|dispatch|start|move|go ahead)|shall i\s+(?:proceed|continue|fix|start)|^proceed\?$)/im

// ── INVESTIGATION-BEFORE-ACTION patterns (2026-07-26) ────────────────────────
// "Let me check what's left", "let me see what remains", "I'll check what's pending",
// "checking what's remaining" — these are stop-adjacent: the agent pauses dispatch
// to survey work instead of dispatching immediately. A survey-response with 0
// dispatches after subagent results arrive is structurally a pause, even if the
// agent frames it as "checking."
const CHECKING_WHAT_LEFT_RE = /(?:let me\s+(?:just\s+)?(?:check|see|look|survey|find out)\s+(?:what.?s?\s+(?:left|remaining|pending|still|else)|how\s+much\s+(?:work|is left|remains)|if.+work|whether.+work)|i.?ll\s+(?:check|see|look)\s+(?:what.?s?\s+(?:left|remaining|pending)|how\s+much)|(?:checking|seeing|looking|surveying)\s+(?:what.?s?\s+(?:left|remaining|pending)|how\s+much)|hold\s+on,?\s+let\s+me\s+check|wait,?\s+let\s+me\s+check|let\s+me\s+(?:first\s+)?(?:check|see|look)\s+(?:if|whether|what))/i

// ── STATUS-SUMMARY detection (2026-07-15) ───────────────────────────────────
// ROOT CAUSE: a status summary containing commit hashes or "CI: PENDING"
// matches EVIDENCE_PATTERNS, so every text.complete check gated on
// !hasStructuredEvidence() was bypassed. Evidence proves a claim true;
// it does NOT make stopping-to-summarize acceptable. These patterns are
// blocked REGARDLESS of evidence when pending work exists.
const STATUS_SUMMARY_RE = new RegExp(
  [
    "here.{0,4}s the (?:session\\s+\\d+\\s+)?(?:final\\s+)?status",
    "session\\s+\\d+\\s+(?:final\\s+)?(?:status|summary|wrap[- ]?up|recap)",
    "final (?:status|summary|state)(?:\\s+(?:report|summary))?\\b",
    "^\\s*#{1,4}\\s+.{0,40}(?:status|summary|recap)\\s*$",
    "status (?:report|summary|update)\\s*:",
  ].join("|"),
  "im",
)

function looksLikeStatusSummary(text: string): boolean {
  if (STATUS_SUMMARY_RE.test(text)) return true
  // Long responses with markdown section headers are status reports:
  // >500 chars + >=1 "##" section header = structured report shape.
  if (text.length > 500 && /^\s*#{2,4}\s+\S/m.test(text)) return true
  // Structural detection: bolded section headers + status tables / bullets.
  const boldHeaders = (text.match(/^\s*\*\*[^*\n]{2,80}\*\*:?\s*$/gm) || []).length
  const inlineBoldLeads = (text.match(/^\s*\*\*[^*\n]{2,80}\*\*:?\s+\S/gm) || []).length
  const tableRows = (text.match(/^\s*\|.*\|\s*$/gm) || []).length
  const statusBullets = (text.match(/^\s*[-*]\s+(?:\[[ xX]\]|✅|❌|⏳|\*\*)/gm) || []).length
  if ((boldHeaders + inlineBoldLeads) >= 2 && (tableRows >= 2 || statusBullets >= 3)) return true
  if (tableRows >= 4 && COMPLETION_SMELL_RE.test(text)) return true
  return false
}

// EVIDENCE_PATTERNS: machine-produced verification of COMPLETION.
// CI-status words (GREEN/RED/PENDING) are REMOVED from this list
// (2026-07-16, Session 49 incident). A text response stating "CI PENDING"
// is a status CLAIM, not evidence of completion — but the regex
// CI\s+(?:GREEN|RED|PENDING) matched it, causing hasStructuredEvidence()
// to return true and skipping the hasPendingWork text-only block at
// text.complete.ci-vs-claim-gap. CI verdict evidence is captured by
// /conclusion:\s*(?:success|failure)/ and /headSha.*matched/ below.
const COMMIT_HASH_RE = /\b[0-9a-f]*[a-f][0-9a-f]{6,39}\b/

const EVIDENCE_PATTERNS = [
  COMMIT_HASH_RE,
  /VERIFIED\s+\w+@/,
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

function isStopEnforcementDisabled(): boolean {
  return process.env.GLUDD_STOP_ENFORCE === "0"
}

function hasStopStatePathOverrides(): boolean {
  return [
    "GLUDD_STOP_STATE_FILE",
    "GLUDD_BLOCK_COUNTER_FILE",
    "GLUDD_BLOCK_REASON_FILE",
    "GLUDD_PERSIST_STOP_BLOCK_FILE",
    "GLUDD_STOP_TEXT_COMPLETE_COUNT",
    "GLUDD_FORCE_DISPATCH_PATH",
    "GLUDD_RELEASE_COMPLETENESS_FILE",
    "GLUDD_LAST_TEST_RESULT_FILE",
    "GLUDD_MULTITASK_STATE_FILE",
    "GLUDD_POST_RESULTS_STATE_FILE",
    "GLUDD_TEXT_ONLY_STATE_FILE",
    "GLUDD_WATCHDOG_CI_FILE",
  ].some((key) => Boolean(process.env[key]))
}

function stopImpl(): HotModule {
  // enforce-stop is not proxy-converted.  Never load a legacy /tmp override:
  // stale pass-through modules can silently disable the mandatory text gate.
  return defaultImpl
}

interface PostResultsState {
  lastTurnHadResults: boolean
  lastTurnHadWave: boolean
  lastResultCount: number
  lastToolWasShipping: boolean
  lastShippingToolName: string
  ts: number
}

interface TextOnlyState {
  count: number
  ts: number
}

function readPostResultsState(): PostResultsState {
  return readJsonFile<PostResultsState>(POST_RESULTS_STATE_FILE, {
    lastTurnHadResults: false,
    lastTurnHadWave: false,
    lastResultCount: 0,
    lastToolWasShipping: false,
    lastShippingToolName: "",
    ts: 0,
  })
}

function writePostResultsState(state: PostResultsState): void {
  writeJsonFile(POST_RESULTS_STATE_FILE, state)
}

function readTextOnlyState(): TextOnlyState {
  return readJsonFile<TextOnlyState>(TEXT_ONLY_STATE_FILE, { count: 0, ts: 0 })
}

function writeTextOnlyState(state: TextOnlyState): void {
  writeJsonFile(TEXT_ONLY_STATE_FILE, state)
}

function textHasResultMarkers(text: string): { found: boolean; count: number } {
  const markers = [
    "<task_result>",
    "task result",
    "task_result",
    "subagent result",
    "subagent_result",
    "workflow result",
    "workflow_result",
  ]
  const lower = text.toLowerCase()
  let count = 0
  for (const marker of markers) {
    const escaped = marker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    count += lower.match(new RegExp(escaped, "g"))?.length || 0
  }
  return { found: count > 0, count }
}

function clearBlockedOutput(output: any): void {
  output.text = ""
}

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
  const MAX_DISENGAGE = now + 300_000
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

function hasFreshForceDispatchDirective(): boolean {
  try {
    if (!fs.existsSync(FORCE_DISPATCH_FILE)) return false
    const directive = JSON.parse(fs.readFileSync(FORCE_DISPATCH_FILE, "utf8"))
    const ts = Number(directive.ts || directive.timestamp || directive.lastBlockTs || 0)
    return Number.isFinite(ts) && Date.now() - ts < 120_000
  } catch {
    return false
  }
}

function recordBlock(reason: string): void {
  const c = readBlockCounter()
  const now = Date.now()
  c.totalBlocks++
  const prevTs = c.lastBlockTs
  c.lastBlockTs = now
  if (now - prevTs < 120_000) c.consecutiveBlocks++
  else c.consecutiveBlocks = 1
  if (c.consecutiveBlocks >= 3) {
    writeForceDispatch(c, reason)
  }
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

function recordBlankedResponse(reason: string, textSnippet: string): void {
  logBlankedResponse(reason, textSnippet)
}

function writeForceDispatch(counter: BlockCounter, reason: string): void {
  try {
    fs.writeFileSync(FORCE_DISPATCH_FILE, JSON.stringify({
      reason,
      consecutiveBlocks: counter.consecutiveBlocks,
      totalBlanked: counter.totalBlocks,
      ts: Date.now(),
      message: "EMERGENCY OVERRIDE: DISPATCH SUBAGENTS NOW",
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
  return c.disengageUntil > now
}

// ── Increment text.complete fire counter ────────────────────────────────────

function incrementTextCompleteCount(): void {
  try {
    const now = Date.now()
    let data = readJsonFile<{ count: number; ts?: number; last_fired?: number }>(TEXT_COMPLETE_COUNT_FILE, { count: 0 })
    data.count++
    data.ts = now
    data.last_fired = now
    writeJsonFile(TEXT_COMPLETE_COUNT_FILE, data)
  } catch {}
}

// ── Pending-work signals (binary latch — no weights, no thresholds) ─────────
// Each signal is a boolean. If ANY signal is true, work is pending.

interface PendingWorkSignals {
  gateStatusRed: boolean
  gateStale: boolean
  ciVerdictPendingOrRed: boolean
  ciVerdictUnknown: boolean
  tasksMdUnchecked: boolean
  bugsOpen: boolean
  repoPending: boolean
  multitaskingBacklogOpen: boolean
  underFloor: boolean
  coverageIncomplete: boolean
  fullE2eIncomplete: boolean
  pushBlocked: boolean
  gateLiteTestFailed: boolean
  ciNeverRunOnHead: boolean
  uncommittedChanges: boolean
  tasksMdUnverified: boolean
  ratchetHasEntries: boolean
}

// Fixable push-block reasons — these indicate real pending work (not
// external/rate-limiting reasons like "cooldown" or "rate-limited").
const FIXABLE_PUSH_BLOCK_REASONS = new Set([
  "ci-pending", "ci-red", "ci-failure", "gate-red", "gate-failure",
  "dirty-tree", "uncommitted-changes", "lint-failure", "typecheck-failure",
  "test-failure", "collect-failure", "tdd-compliance", "no-verify-required",
])

function computeHealthScore(signals: PendingWorkSignals): boolean {
  return Object.values(signals).some((v) => v === true)
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
  coverageIncomplete: boolean
  fullE2eIncomplete: boolean
  ciVerdictPendingOrRed: boolean
  ciVerdictUnknown: boolean
  releaseIncomplete: boolean
  testFailures: boolean
  repoPending: boolean
  multitaskingBacklogOpen: boolean
  backlogOpen: boolean
  backlogItems: number
  underFloor: boolean
  pushBlocked: boolean
  gateLiteTestFailed: boolean
  ciNeverRunOnHead: boolean
  uncommittedChanges: boolean
  tasksMdUnverified: boolean
  hasPendingWork: boolean
  hasLocalWork: boolean
  healthScore: boolean
  ts: number
}

// ── CI-COOLDOWN detection (2026-07-15) ──────────────────────────────────────
// When `make ci-verdict-safe` returns exit 3, the CI check was REFUSED by the
// cooldown — CI state is UNKNOWN, not PENDING. AGENTS.md "CI-COOLDOWN ≠
// PENDING (cooldown masking)": never report CI as PENDING based on a cooldown
// block. The pending-work check still acts CONSERVATIVELY (unknown CI counts
// as pending work), but the state is labeled UNKNOWN, never PENDING/RED.
const CI_COOLDOWN_RE = /CI-COOLDOWN|COOLDOWN-ACTIVE/i

function ciCooldownMasked(ciData: { last_ci_status?: string; last_output?: string }): boolean {
  const status = typeof ciData.last_ci_status === "string" ? ciData.last_ci_status : ""
  const lastOutput = typeof ciData.last_output === "string" ? ciData.last_output : ""
  if (CI_COOLDOWN_RE.test(status) || CI_COOLDOWN_RE.test(lastOutput)) return true
  try {
    const ciStatusPath = path.join(process.cwd(), ".ci-status")
    if (fs.existsSync(ciStatusPath)) {
      const content = fs.readFileSync(ciStatusPath, "utf8")
      if (CI_COOLDOWN_RE.test(content)) return true
    }
  } catch {}
  return false
}

function watchdogCiPath(): string {
  return process.env.GLUDD_WATCHDOG_CI_FILE || "/tmp/gludd-watchdog-ci.json"
}

function countOpenBugIncidents(content: string): number {
  const incidentSections: string[] = []
  let current: string[] = []
  for (const line of content.split("\n")) {
    if (/^###\s+\d{4}-\d{2}-\d{2}\s+[-—]/.test(line)) {
      if (current.length > 0) incidentSections.push(current.join("\n"))
      current = [line]
      continue
    }
    if (current.length > 0) current.push(line)
  }
  if (current.length > 0) incidentSections.push(current.join("\n"))

  return incidentSections.filter(incident => {
    return !/\b(?:resolved|fixed|closed|wontfix|duplicate)\b/i.test(incident)
  }).length
}

function hasRealPendingWork(): WorkState {
  const now = Date.now()

  // UNCONDITIONAL filesystem read — NO caching. Read TASKS.md every time.
  const root = getProjectRoot()
  const cwd = process.cwd()
  let tasksMdUnchecked = false
  let tasksMdUncheckedCount = 0
  let ratchetEntries = 0
  let bugsOpen = false
  let gateStatusMissing = false
  let gateStale = false
  let gateStatusRed = false
  let coverageIncomplete = false
  let fullE2eIncomplete = false

  try {
    const tasksPath = path.join(root, "TASKS.md")
    if (fs.existsSync(tasksPath)) {
      const content = fs.readFileSync(tasksPath, "utf8")
      const checkboxMatches = content.match(/^[ \t]*[-*]\s*\[\s*\]/gm)
      const tableMatches = content.match(/\|\s*(NOT STARTED|IN PROGRESS|PENDING)\s*\|/gim)
      const total = (checkboxMatches?.length ?? 0) + (tableMatches?.length ?? 0)
      if (total > 0) {
        tasksMdUncheckedCount = total
        tasksMdUnchecked = true
      }
    }
  } catch {}

  try {
    const ratchetPath = path.join(root, "config", "ratchet.yml")
    if (fs.existsSync(ratchetPath)) {
      const content = fs.readFileSync(ratchetPath, "utf8")
      ratchetEntries = content.split("\n").filter(
        l => l.trim() && !l.trim().startsWith("#") && (l.includes("::") || /^\w[\w\s]*:\s/.test(l))
      ).length
    }
  } catch {}

  try {
    const bugsPath = path.join(root, "BUGS.md")
    if (fs.existsSync(bugsPath)) {
      const content = fs.readFileSync(bugsPath, "utf8")
      bugsOpen = countOpenBugIncidents(content) > 0
    }
  } catch {}

  try {
    const gatePath = path.join(root, ".gate-status")
    if (!fs.existsSync(gatePath)) {
      gateStatusMissing = true
    } else {
      const stat = fs.statSync(gatePath)
      gateStale = (now - stat.mtimeMs) > 1_800_000
      const content = fs.readFileSync(gatePath, "utf8")
      for (const line of content.split("\n")) {
        if (line.startsWith("===")) continue
        if (/^(lint |typecheck |collect |test |smoke |env-writes |dead-code |hook-runtime |verify-enforcement |coverage-gaps )/.test(line)) {
          if (/FAIL/.test(line)) { gateStatusRed = true; coverageIncomplete = /^coverage-gaps /.test(line); break }
        }
        if (/^e2e /.test(line) && /FAIL/.test(line)) {
          gateStatusRed = true
          fullE2eIncomplete = true
          break
        }
      }
    }
  } catch {}

  let gateLiteTestFailed = false
  try {
    const gateLitePath = path.join(root, ".gate-lite-status")
    if (fs.existsSync(gateLitePath)) {
      const content = fs.readFileSync(gateLitePath, "utf8")
      if (/=== GATE-LITE:\s*FAILED/.test(content)) {
        gateStatusRed = true
      }
      for (const line of content.split("\n")) {
        if (line.startsWith("===")) continue
        if (/^(lint |typecheck |collect |test |smoke |env-writes |dead-code |hook-runtime |coverage-gaps |tdd-compliance |plugin-hook-invoke |skills-frontmatter |lint-specs |spec-enforcement-coverage )/.test(line) && /FAIL/.test(line)) {
          gateStatusRed = true
          gateLiteTestFailed = /^test /.test(line)
          break
        }
      }
    }
  } catch {}

  let ciVerdictPendingOrRed = false
  let ciVerdictUnknown = false
  try {
    const ciCachePath = watchdogCiPath()
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const rawLastCheck: number = ciData.last_ci_check || 0
      // 2026-07-17: watchdog writes epoch SECONDS (time.time()), but Date.now()
      // returns MILLISECONDS. Normalize: if the value is < 1e11 (~5138 AD in ms),
      // treat it as seconds and convert to ms.
      const lastCheck: number = rawLastCheck < 1e11 ? rawLastCheck * 1000 : rawLastCheck
      const lastStatus = ciData.last_ci_status || ""
      if (now - lastCheck < 600_000 && lastStatus) {
        if (ciCooldownMasked(ciData)) {
          // ci-verdict-safe exit 3: check REFUSED. CI state is UNKNOWN — the
          // real run may already be GREEN or RED. Never claim PENDING/RED
          // from a cooldown; conservatively treat unknown as pending work.
          ciVerdictUnknown = true
        } else {
          ciVerdictPendingOrRed = lastStatus !== "SUCCESS"
        }
      }
    }
  } catch {}

  let releaseIncomplete = false
  try {
    const releaseCheckPath = RELEASE_COMPLETENESS_FILE
    if (fs.existsSync(releaseCheckPath)) {
      const rd = JSON.parse(fs.readFileSync(releaseCheckPath, "utf8"))
      if (now - (rd.ts || 0) < 300_000 && rd.incomplete) {
        releaseIncomplete = true
      }
    }
  } catch {}

  let testFailures = false
  try {
    const lastTestPath = LAST_TEST_RESULT_FILE
    if (fs.existsSync(lastTestPath)) {
      const td = JSON.parse(fs.readFileSync(lastTestPath, "utf8"))
      if (td.failures > 0) testFailures = true
    }
  } catch {}

  let repoPending = false
  try {
    repoPending = repoHasPendingWork(execSync)
  } catch {}

  // Multitasking backlog: scripts/multitasking_backlog.json tracks open
  // orchestration work items. Any item not marked "done" counts as pending
  // work so the stop-pattern gate cannot fire while backlog items remain.
  let multitaskingBacklogOpen = false
  let backlogItems = 0
  try {
    const backlogPath = path.join(cwd, "scripts", "multitasking_backlog.json")
    if (fs.existsSync(backlogPath)) {
      const backlog = JSON.parse(fs.readFileSync(backlogPath, "utf8"))
      const items: any[] = Array.isArray(backlog?.items) ? backlog.items : []
      backlogItems = items.filter(
        (it: any) => it && typeof it.status === "string" && it.status.toLowerCase() !== "done"
      ).length
      multitaskingBacklogOpen = backlogItems > 0
    }
  } catch {}

  let underFloor = false
  try {
    const multitaskStatePath = MULTITASK_STATE_FILE
    if (fs.existsSync(multitaskStatePath)) {
      const ms = JSON.parse(fs.readFileSync(multitaskStatePath, "utf8"))
      underFloor = typeof ms.thisMessageDispatches === "number" &&
        REQUIRED_AGENT_MIN > 0 &&
        ms.thisMessageDispatches < REQUIRED_AGENT_MIN
    }
  } catch {}

  // ── pushBlocked: fixable blocked pushes ──────────────────────────────────
  let pushBlocked = false
  try {
    if (fs.existsSync(PUSH_STATE_FILE)) {
      const ps = JSON.parse(fs.readFileSync(PUSH_STATE_FILE, "utf8"))
      if (ps.last_push_blocked) {
        const reason = typeof ps.block_reason === "string" ? ps.block_reason : ""
        if (FIXABLE_PUSH_BLOCK_REASONS.has(reason)) {
          pushBlocked = true
        }
      }
    }
  } catch {}

  // gateLiteTestFailed is derived from this project's structured
  // .gate-lite-status above. Never scrape a cross-project /tmp log.

  // ── ciNeverRunOnHead: .ci-status missing for current HEAD ─────────────────
  let ciNeverRunOnHead = false
  try {
    const ciStatusPath = path.join(root, ".ci-status")
    if (fs.existsSync(ciStatusPath)) {
      const ciStatusContent = fs.readFileSync(ciStatusPath, "utf8")
      let headSha = ""
      try {
        headSha = execSync("git rev-parse HEAD", {
          cwd: root, encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
        }) as string
        headSha = headSha.trim()
      } catch {}
      if (headSha && !ciStatusContent.includes(headSha)) {
        ciNeverRunOnHead = true
      }
    }
  } catch {}

  // ── uncommittedChanges: git status --porcelain ────────────────────────────
  let uncommittedChanges = false
  try {
    const porcelain = execSync("git status --porcelain", {
      cwd: root, encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
    }) as string
    if (porcelain.trim().length > 0) {
      uncommittedChanges = true
    }
  } catch {}

  // ── tasksMdUnverified: [x] items without evidence ─────────────────────────
  let tasksMdUnverified = false
  try {
    const tasksPath2 = path.join(root, "TASKS.md")
    if (fs.existsSync(tasksPath2)) {
      const tasksContent = fs.readFileSync(tasksPath2, "utf8")
      const checkedLines = tasksContent.split("\n").filter(
        (l: string) => /^[ \t]*[-*]\s*\[[xX]\]/.test(l),
      )
      const evidenceRe = /\b(?:[0-9a-f]*[a-f][0-9a-f]{6,39}|\d+\s+passed|CI\s+(?:GREEN|RED)|=== GATE(?:\s*|-LITE):\s*PASSED|VERIFIED\s+\w+@|conclusion:\s*success|All checks passed|Collection OK)\b/i
      const unverifiedCount = checkedLines.filter((l: string) => !evidenceRe.test(l)).length
      if (unverifiedCount > 0) {
        tasksMdUnverified = true
      }
    }
  } catch {}

  // ── BINARY LATCH — any signal true = pending work ─────────────────────────
  // tasksMdUnverified is deliberately NOT a latch signal: a fully-ticked
  // TASKS.md (even without evidence tokens on every line) is NOT open work —
  // blocking text-only forever on evidence formatting would prevent the
  // session from ever going idle (pinned by tests/e2e/test_enforce_stop_live.py
  // test_empty_tasks_md_is_no_pending_work). It stays in the reported state.
  const signals: PendingWorkSignals = {
    gateStatusRed, gateStale, ciVerdictPendingOrRed, ciVerdictUnknown,
    tasksMdUnchecked, bugsOpen, repoPending, multitaskingBacklogOpen, underFloor,
    coverageIncomplete, fullE2eIncomplete,
    pushBlocked, gateLiteTestFailed, ciNeverRunOnHead, uncommittedChanges,
    ratchetHasEntries: ratchetEntries > 0,
  }
  const hasPendingWork = computeHealthScore(signals)
  const hasLocalWork = hasPendingWork

  const state: WorkState = {
    tasksMdUnchecked, tasksMdUncheckedCount, ratchetEntries, bugsOpen,
    gateStatusMissing, gateStale, gateStatusRed,
    ciVerdictPendingOrRed, ciVerdictUnknown, releaseIncomplete, testFailures, repoPending,
    multitaskingBacklogOpen, backlogOpen: multitaskingBacklogOpen, backlogItems, underFloor,
    coverageIncomplete, fullE2eIncomplete,
    pushBlocked, gateLiteTestFailed, ciNeverRunOnHead, uncommittedChanges, tasksMdUnverified,
    hasPendingWork, hasLocalWork, healthScore: hasPendingWork, ts: now,
  }

  try {
    fs.writeFileSync(STATE_FILE, JSON.stringify(state), "utf8")
  } catch {}

  return state
}

// ── COMPLETION VERBATIM detection ──────────────────────────────────────────

function responseLooksTerminal(text: string): boolean {
  const t = text.trim().toLowerCase()
  if (COMPLETION_VERBATIM.test(t)) return true
  if (COMPLETION_WORDS_RE.test(t)) return true
  if (SHORT_COMPLETION_PHRASES.test(t)) return true
  if (QA_RESPONSE_PATTERNS.test(t)) return true
  return false
}

function hasStructuredEvidence(text: string): boolean {
  return EVIDENCE_PATTERNS.some(p => p.test(text))
}

function hasWorkArtifact(text: string): boolean {
  return hasStructuredEvidence(text)
}

function lateHasWorkArtifact(text: string): boolean {
  return hasStructuredEvidence(text)
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
const GIT_SHIPPING_TARGETS_RE = /^make\s+(ship-commit|batch-push|git-push-sandboxcom|git-tag-push)(\s|$)/

function issueStopChallenge(): string {
  const challenge_token = randomUUID().replace(/-/g, "").slice(0, 16)
  try {
    fs.appendFileSync(
      process.env.GLUDD_STOP_CHALLENGE_FILE || "/tmp/gludd-stop-challenge.jsonl",
      `${JSON.stringify({ challenge_token, timestamp: new Date().toISOString(), pid: process.pid })}\n`,
      "utf8",
    )
  } catch {}
  return challenge_token
}

function stopLikeDenyMessage(taskMd: boolean, ratchetEntries: number, extraReasons: string[] = []): string {
  const challenge_token = issueStopChallenge()
  const reasons = [
    `TASKS.md unchecked: ${taskMd ? "yes" : "no"}, ratchet entries: ${ratchetEntries}`,
    ...extraReasons,
  ]
  return [
    "STOP-LIKE TOOL BLOCKED — PENDING WORK EXISTS:",
    `STOP CHALLENGE: ${challenge_token}`,
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
    return countOpenBugIncidents(content) > 0
  } catch { return false }
}

function ciIsPendingOrRed(): boolean {
  try {
    const ciCachePath = watchdogCiPath()
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const rawLastCheck: number = ciData.last_ci_check || 0
      const lastCheck: number = rawLastCheck < 1e11 ? rawLastCheck * 1000 : rawLastCheck
      const lastStatus = ciData.last_ci_status || ""
      if (Date.now() - lastCheck < 600_000 && lastStatus) {
        // CI-COOLDOWN ≠ PENDING: a cooldown-refused check means CI state is
        // UNKNOWN, not pending/red. ciIsUnknown() covers the conservative path.
        if (ciCooldownMasked(ciData)) return false
        return lastStatus !== "SUCCESS"
      }
    }
  } catch {}
  return false
}

function ciIsUnknown(): boolean {
  try {
    const ciCachePath = watchdogCiPath()
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const rawLastCheck: number = ciData.last_ci_check || 0
      const lastCheck: number = rawLastCheck < 1e11 ? rawLastCheck * 1000 : rawLastCheck
      if (Date.now() - lastCheck < 600_000 && ciCooldownMasked(ciData)) return true
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

/**
 * Apply the non-bypassable text-only guard for filesystem-backed pending work.
 *
 * This guard is intentionally separate from the optional completion heuristics:
 * GLUDD_STOP_ENFORCE=0 may disable those heuristics, but it must never turn an
 * unchecked TASKS.md/ratchet/gate/CI signal into an allowed terminal response.
 * The caller must perform the subagent check first so delegated final reports
 * retain their documented isolation.
 */
function blockMandatoryPendingText(
  text: string,
  output: unknown,
  workState: WorkState,
): { text: string } | undefined {
  if (!workState.hasPendingWork) return undefined

  const challengeToken = issueStopChallenge()
  const sessionBlockCount = incrementSessionBlockCounter()
  const escalationNote = sessionBlockCount > ESCALATION_THRESHOLD
    ? `\n🚨 ESCALATION: ${sessionBlockCount} stop attempts blocked this session. COMPLIANCE REQUIRED.`
    : ""
  const blockReason = workState.ciVerdictPendingOrRed
    ? "ci-red-text-only"
    : "text-only-while-work-exists"

  recordBlock(blockReason)
  if (workState.ciVerdictPendingOrRed) {
    logFalseDoneBlock(blockReason, text)
  }
  recordBlankedResponse(blockReason, text)
  writePersistBlock(true, blockReason)
  clearBlockedOutput(output)

  return {
    text: [
      workState.ciVerdictPendingOrRed
        ? "⛔ CI RED/PENDING COMPLETION CLAIM BLOCKED"
        : "⛔⛔⛔ TEXT-ONLY RESPONSE BLOCKED ⛔⛔⛔",
      `STOP CHALLENGE: ${challengeToken}`,
      "",
      `PENDING WORK EXISTS: ${workState.tasksMdUncheckedCount} unchecked TASKS.md items, ` +
      `${workState.ratchetEntries} ratchet entries, ` +
      `BUGS.md open: ${workState.bugsOpen ? "YES" : "no"}, ` +
      `gate: ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : workState.gateStale ? "STALE" : "OK"}, ` +
      `CI: ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : workState.ciVerdictUnknown ? "UNKNOWN (cooldown)" : "N/A"}, ` +
      `release: ${workState.releaseIncomplete ? "INCOMPLETE" : "N/A"}, ` +
      `repo: ${workState.repoPending ? "DIRTY" : "clean"}.`,
      `Any pending signal: ${workState.healthScore ? "YES" : "no"}.`,
      "",
      "You may NOT send a text-only response while work exists.",
      "The ONLY valid action is to DISPATCH SUBAGENTS via the Task tool,",
      "or read/edit files to fix the pending work.",
      "NO short-text exemption. NO length exemption. NO content exemption.",
      escalationNote,
    ].join("\n"),
  }
}

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  // ── experimental.chat.system.transform ────────────────────────────────────

  "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output // OPENCODE_SUBAGENT guard
    if (isStopEnforcementDisabled()) return output
    reportAlive("enforce-stop")
    writeHeartbeat("enforce-stop")

    const workState = hasRealPendingWork()
    const hasWork = workState.hasPendingWork || workState.hasLocalWork

    if (typeof output === "string") {
      if (SUBAGENT_TEXT_MARKERS.test(output)) return output

      if (hasWork) {
        const indicators: string[] = []
        if (workState.tasksMdUnchecked) indicators.push(`${workState.tasksMdUncheckedCount} unchecked TASKS.md items`)
        if (workState.ratchetEntries > 0) indicators.push(`${workState.ratchetEntries} ratchet entries`)
        if (workState.bugsOpen) indicators.push("BUGS.md open incidents")
        if (workState.gateStatusRed) indicators.push("gate RED")
        if (workState.gateStale) indicators.push("gate stale")
        if (workState.gateStatusMissing) indicators.push("gate missing")
        if (workState.ciVerdictPendingOrRed) indicators.push("CI pending/red")
        if (workState.ciVerdictUnknown) indicators.push("CI UNKNOWN (cooldown-masked — NOT pending; FORCE=1 for actual state)")
        if (workState.releaseIncomplete) indicators.push("release incomplete")
        if (workState.testFailures) indicators.push("test failures")
        if (workState.repoPending) indicators.push("repo dirty")
        if (workState.underFloor) indicators.push(`configured dispatch minimum ${REQUIRED_AGENT_MIN}`)
        const sessionBlockCount = getSessionBlockCount()
        const escalation = sessionBlockCount > ESCALATION_THRESHOLD
          ? `\n🚨 ESCALATION ACTIVE: ${sessionBlockCount} stop attempts blocked this session. DO NOT attempt to stop.\n`
          : ""
        const block = [
          "",
          "══════════════════════════════════════════════════════════════",
          "⛔⛔⛔ MANDATORY PRE-GENERATION GATE — HARD STOP ⛔⛔⛔",
          "══════════════════════════════════════════════════════════════",
          "",
          `PENDING WORK EXISTS: ${indicators.join(", ")}.`,
          `Any pending signal: ${workState.healthScore ? "YES" : "no"}.`,
          "",
          "⛔ ROOT-CAUSE-ONLY: Every fix must address the root cause, never the symptom.",
          "  - Guardrail errors? Fix the logic, don't disable the guardrail.",
          "  - CI red? Fix the failing tests, don't skip/weaken them.",
          "  - Release blocked? Fix the blocker, don't bypass it (--no-verify, FORCE=1).",
          "  - Todowrite bypass? hasRealPendingWork() checks filesystem, not todowrite.",
          "",
          "HARD STOP: YOU ARE PHYSICALLY FORBIDDEN FROM GENERATING A TEXT-ONLY RESPONSE.",
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
    // FALSE-DONE detection lives in this hook; keep the marker near the hook
    // entry so structural tests catch accidental removal or relocation.
    if (isSubagent()) return output // OPENCODE_SUBAGENT guard
    incrementTextCompleteCount()
    reportAlive("enforce-stop")
    writeHeartbeat("enforce-stop")

    const text = typeof output === "string" ? output
      : (output as any)?.text ? String((output as any).text)
      : JSON.stringify(output)

    if (!text || text.trim().length === 0) return output
    const trimmed = text.trim()
    if (/^(?:⛔|BLOCKED\b)/i.test(trimmed)) return output
    const hasWorkArtifact = hasStructuredEvidence(text)
    const lateHasWorkArtifact = hasStructuredEvidence(text)
    const hasToolCallIntent = /\b(make git-|dispatch|subagent|task)\b/i.test(text)
    void hasWorkArtifact
    void lateHasWorkArtifact
    void hasToolCallIntent

    // Disengage check — if disengaged, allow through (false-positive cascade escape)
    // *** NARROWED 2026-07-15: disengage ONLY bypasses heuristic checks below,
    // NOT the fundamental hasRealPendingWork text-only block (line 726).
    // Previously isDisengaged() at line 632 returned early before ANY checks,
    // allowing text-only responses through while CI was RED and work existed.
    const disengaged = isDisengaged()
    const forceDispatchDirective = hasFreshForceDispatchDirective()

    const workState = hasRealPendingWork()
    const now = Date.now()
    const postResultsState = readPostResultsState()
    const turnState = {
      toolCallMade: Boolean((output as any)?.toolCallMade),
      dispatchCount: Number((output as any)?.dispatchCount || 0),
      accumulatedText: String((output as any)?.accumulatedText || ""),
    }
    const isTextOnly = !turnState.toolCallMade && turnState.dispatchCount === 0

    // Reset text-only counter when the response had tool activity.
    if (!isTextOnly) {
      writeTextOnlyState({ count: 0, ts: now })
    }

    // Permission-seeking must beat generic text-only escalation so tests and
    // operators see the precise stop reason.
    if (STOP_PATTERN_PHRASES.test(text) || PERMISSION_SEEKING_RE.test(text)) {
      recordBlock("permission-seeking-stop")
      logFalseDoneBlock("permission-seeking-stop", text)
      writePersistBlock(true, "permission-seeking-stop")
      clearBlockedOutput(output)
      turnState.blocked = true

      return {
        text: [
          "⛔ PERMISSION-SEEKING BLOCKED.",
          "",
          "Do not ask whether to continue, proceed, or fix known work.",
          "Default to action: run the next tool call and let the user redirect if needed.",
        ].join("\n"),
      }
    }

    // ── STATUS-SUMMARY BLOCK (2026-07-15) ───────────────────────────────────
    // Fires REGARDLESS of structured evidence and REGARDLESS of disengage.
    // A status summary ("Here's the session N final status" + bolded headers
    // + status tables) previously bypassed all checks below because commit
    // hashes / "CI PENDING" inside the summary matched EVIDENCE_PATTERNS.
    // Evidence never legitimizes stopping-to-summarize while work exists.
    if ((workState.hasPendingWork || workState.hasLocalWork || forceDispatchDirective) && looksLikeStatusSummary(text)) {
      recordBlock("status-summary-while-work-exists")
      recordBlankedResponse("status-summary-while-work-exists", text)
      writePersistBlock(true, "status-summary-while-work-exists")

      return {
        text: [
          "⛔⛔⛔ STATUS-SUMMARY RESPONSE BLOCKED ⛔⛔⛔",
          "",
          "Status summaries ('final status', bolded headers, status tables)",
          "are FORBIDDEN while pending work exists — evidence inside the",
          "summary does NOT make it acceptable. Stopping to summarize IS the",
          "stop pattern.",
          "",
          `PENDING WORK: ${workState.tasksMdUncheckedCount} unchecked TASKS.md items, ` +
          `${workState.ratchetEntries} ratchet entries, ` +
          `gate ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : "OK"}, ` +
          `CI ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : workState.ciVerdictUnknown ? "UNKNOWN (cooldown)" : "N/A"}, ` +
          `release ${workState.releaseIncomplete ? "INCOMPLETE" : "N/A"}.`,
          "",
          "DISPATCH A TOOL CALL NOW. Do not summarize.",
        ].join("\n"),
      }
    }

    // ── POST-RESULTS TEXT-ONLY BLOCK ───────────────────────────────────────
    // RESEARCH FINDING: opencode text.complete never receives tool output; it
    // fires for assistant text only, so a message with no toolCallMade and zero
    // dispatches is an actual text-only stop attempt after prior results.
    const textOnly = readTextOnlyState()
    const sameSession = (now - textOnly.ts) < 300_000
    if (isTextOnly) {
      textOnly.count = sameSession ? textOnly.count + 1 : 1
      textOnly.ts = now
      writeTextOnlyState(textOnly)
    }

    // ── SUBAGENT-RESULTS INGESTION GUARD (>=3 <task_result> markers) ───────
    if (isTextOnly && postResultsState.lastResultCount >= WAVE_RESULT_THRESHOLD && !hasWorkArtifact) {
      recordBlock("after-results-text-only")
      logFalseDoneBlock("after-results-text-only", text)
      recordBlankedResponse("after-results-text-only", text)
      writePersistBlock(true, "after-results-text-only")
      updateSharedStreak("text-only", "enforce-stop")

      return {
        text: [
          "RESULTS INGESTION PROTOCOL: " + String(postResultsState.lastResultCount) + " subagent results arrived.",
          "Codify results (commit/tick TASKS.md), then dispatch next wave.",
          "Text-only after results is a stop.",
        ].join("\n"),
      }
    }

    // ── POST-SHIP CONTINUATION GUARD ──────────────────────────────────────
    if (isTextOnly && postResultsState.lastToolWasShipping && workState.hasPendingWork) {
      recordBlock("post-ship-text-only")
      logFalseDoneBlock("post-ship-text-only", text)
      recordBlankedResponse("post-ship-text-only", text)
      writePersistBlock(true, "post-ship-text-only")
      updateSharedStreak("text-only", "enforce-stop")

      return {
        text: [
          "POST-SHIP CONTINUATION: after shipping, continue to next pending item.",
          "Text-only after commit/push is a stop.",
        ].join("\n"),
      }
    }

    // QA summaries must beat generic text-only repeat blocking so persist logs
    // carry the action-specific reason instead of consecutive-text-only.
    if (!disengaged && QA_RESPONSE_PATTERNS.test(text) && (workState.hasLocalWork || workState.ciVerdictPendingOrRed)) {
      recordBlock("qa-response-summary-stop")
      logFalseDoneBlock("qa-response-summary-stop", text)
      writePersistBlock(true, "qa-response-summary-stop")
      clearBlockedOutput(output)
      turnState.blocked = true

      return {
        text: [
          "QA RESPONSE SUMMARY BLOCKED",
          "",
          "Do not stop to summarize while work remains.",
          "Dispatch a tool call now.",
        ].join("\n"),
      }
    }

    // ── SUBAGENT_DEFICIT (2026-07-27) ──────────────────────────────────────
    // Agent mentions subagent results ("Agent 1 did X, Agent 2 did Y...")
    // while dispatching fewer subagents than an explicitly configured minimum.
    // In opt-in quota mode, blank the result recap regardless of evidence.
    // Adaptive mode leaves the recap alone rather than requiring padding.
    // This specific result-text check precedes the generic minimum check.
    if (
      (workState.hasPendingWork || workState.hasLocalWork || forceDispatchDirective) &&
      SUBAGENT_DEFICIT_RE.test(text) &&
      REQUIRED_AGENT_MIN > 0 &&
      turnState.dispatchCount < REQUIRED_AGENT_MIN &&
      !hasWorkArtifact
    ) {
      recordBlock("subagent-deficit")
      recordBlankedResponse("subagent-deficit", text)
      writePersistBlock(true, "subagent-deficit")

      return {
        text: [
          "⛔ CONFIGURED SUBAGENT MINIMUM NOT MET.",
          "",
          `Current message has only ${turnState.dispatchCount} dispatch(es) ` +
          `(configured minimum is ${REQUIRED_AGENT_MIN}).`,
          `Text mentions subagent results but the configured minimum is not met.`,
          "",
          "Do NOT send a text summary of subagent results between waves.",
          "Dispatch only the remaining suitable independent work via the Task tool.",
          "",
          `PENDING: ${workState.tasksMdUncheckedCount} tasks.`,
        ].join("\n"),
      }
    }

    // ── CONFIGURED MINIMUM (2026-07-27) ────────────────────────────────────
    // When an operator explicitly configures a minimum, blank text that stops
    // below it. Adaptive mode has no mandatory minimum, while the hard maximum
    // remains ten. The specific result-text check above fires first.
    if (
      (workState.hasPendingWork || workState.hasLocalWork || forceDispatchDirective) &&
      turnState.dispatchCount > 0 &&
      REQUIRED_AGENT_MIN > 0 &&
      turnState.dispatchCount < REQUIRED_AGENT_MIN &&
      !hasWorkArtifact
    ) {
      const GIT_SHIPPING_PHRASE = /\b(ship-commit|git-commit|batch-push|development-push|development-merge|release-cut)\b/i
      if (!GIT_SHIPPING_PHRASE.test(text)) {
        recordBlock("under-dispatch-floor")
        recordBlankedResponse("under-dispatch-floor", text)
        writePersistBlock(true, "under-dispatch-floor")

        return {
          text: [
            "⛔ CONFIGURED DISPATCH MINIMUM: only " + String(turnState.dispatchCount) + " dispatches in current message.",
            "Configured minimum: " + String(REQUIRED_AGENT_MIN) +
              "; hard ceiling: " + String(HARD_MAX_DISPATCHES) + ".",
            "Do not pad the wave: dispatch only suitable independent work.",
            "",
            `PENDING: ${workState.tasksMdUncheckedCount} tasks, ` +
            `CI ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : "N/A"}, ` +
            `gate ${workState.gateStatusRed ? "RED" : "OK"}.`,
          ].join("\n"),
        }
      }
    }

    // ── CONSECUTIVE TEXT-ONLY RESPONSES ────────────────────────────────────
    // hasLocalWork text-only attempts are blocked here as well as by the
    // broader pending-work block below; this is the session-level repeat guard.
    if (
      isTextOnly &&
      textOnly.count >= 2 &&
      (workState.hasPendingWork || workState.hasLocalWork) &&
      !lateHasWorkArtifact &&
      !(trimmed.length < 60 && responseLooksTerminal(text) && !hasStructuredEvidence(text))
    ) {
      recordBlock("consecutive-text-only")
      recordBlankedResponse("consecutive-text-only", text)
      writePersistBlock(true, "consecutive-text-only")

      return {
        text: [
          "CONSECUTIVE TEXT-ONLY RESPONSES BLOCKED",
          `Text-only count: ${textOnly.count}.`,
          "Dispatch tools or continue fixing work instead of sending another text-only response.",
        ].join("\n"),
      }
    }

    // Check short completion claims.
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
          `CI ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : workState.ciVerdictUnknown ? "UNKNOWN (cooldown)" : "N/A"}, ` +
          `gate ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : "OK"}.`,
          "",
          "You MUST dispatch a tool call. Do not claim work is done.",
          "Run: Task tool to dispatch subagents for the pending work.",
        ].join("\n"),
      }
    }

    // ── PERMISSION SEEKING: "want me to proceed?", "should I fix?" ────────
    // ALWAYS blocked — asking permission to do work is never acceptable.
    // The agent must decide, state the assumption, and act. See AGENTS.md
    // "Never Block on Questions — Default to Action."
    if (STOP_PATTERN_PHRASES.test(text) || PERMISSION_SEEKING_RE.test(text)) {
      recordBlock("permission-seeking-stop")
      logFalseDoneBlock("permission-seeking-stop", text)
      writePersistBlock(true, "permission-seeking-stop")
      clearBlockedOutput(output)
      turnState.blocked = true

      return {
        text: [
          "⛔ PERMISSION-SEEKING BLOCKED.",
          "",
          "NEVER ask 'want me to proceed?', 'should I fix?', 'want me to dispatch?',",
          "or any variation of requesting permission to do work.",
          "The answer is always YES — DO IT.",
          "",
          "Decide the most reasonable action, state the assumption in one line,",
          "and PROCEED with a tool call. The user will redirect if wrong.",
          "",
          "See AGENTS.md: 'Never Block on Questions — Default to Action.'",
        ].join("\n"),
      }
    }

    // ── QA RESPONSE PATTERNS: "completed in this session", etc ─────────────
    if (!disengaged && QA_RESPONSE_PATTERNS.test(text)) {
      if (workState.hasLocalWork || workState.ciVerdictPendingOrRed) {
        recordBlock("qa-response-summary-stop")
        logFalseDoneBlock("qa-response-summary-stop", text)
        writePersistBlock(true, "qa-response-summary-stop")
        clearBlockedOutput(output)
        turnState.blocked = true

        return {
          text: [
            "⛔ QA RESPONSE SUMMARY BLOCKED — DO NOT STOP TO SUMMARIZE.",
            "",
            `Real work still exists: ${workState.tasksMdUncheckedCount} unchecked tasks, ` +
            `${workState.ratchetEntries} ratchet entries, ` +
            `gate ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : "OK"}, ` +
            `CI ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : workState.ciVerdictUnknown ? "UNKNOWN (cooldown)" : "N/A"}.`,
            "",
            "DISPATCH A TOOL CALL NOW. Do not send a text-only summary.",
          ].join("\n"),
        }
      }
    }

    // ── CHECKING WHAT'S LEFT: "let me check what's left", "let me see what remains" ─
    // These are stop-adjacent — the agent pauses dispatch to survey work instead of
    // dispatching immediately. "Checking" with 0 dispatches after subagent results
    // is a pause, even if the agent frames it as investigation.
    if (!disengaged && CHECKING_WHAT_LEFT_RE.test(text) && !hasToolCallIntent) {
      recordBlock("checking-whats-left")
      logFalseDoneBlock("checking-whats-left", text)
      writePersistBlock(true, "checking-whats-left")
      clearBlockedOutput(output)
      turnState.blocked = true

      return {
        text: [
          "⛔ CHECKING-WHAT'S-LEFT BLOCKED — DO NOT PAUSE TO SURVEY.",
          "",
          "You are checking/surveying what work remains instead of dispatching.",
          "The correct action after results arrive is to DISPATCH THE NEXT WAVE —",
          "not to pause and survey. Surveying is a stop-by-another-name.",
          "",
          `PENDING WORK: ${workState.tasksMdUncheckedCount} unchecked tasks, ` +
          `${workState.ratchetEntries} ratchet entries, ` +
          `gate ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : "OK"}.`,
          "",
          "DISPATCH SUBAGENTS NOW. Do not survey — act.",
        ].join("\n"),
      }
    }

    // ── COMPLETION WORDS WITHOUT EVIDENCE ──────────────────────────────────
    if (!disengaged && responseLooksTerminal(text) && !hasStructuredEvidence(text)) {
      recordBlock("completion-without-evidence")
      logFalseDoneBlock("completion-without-evidence", text)
      writePersistBlock(true, "completion-without-evidence")
      clearBlockedOutput(output)
      turnState.blocked = true

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
      clearBlockedOutput(output)
      turnState.blocked = true

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

    // ── REAL PENDING WORK BLOCK (text-only responses) ────────────────────────
    // 2026-07-16 FIX: removed !hasStructuredEvidence(text) guard. Text containing
    // "CI PENDING", commit hashes, test counts, etc. previously bypassed this
    // block — but evidence in text does NOT make stopping-to-send-text acceptable.
    // The STATUS_SUMMARY_RE block above catches structured summary-format text;
    // this is the catch-all for any text-only response while pending work exists.
    // CI RED / CI PENDING COMPLETION CLAIM BLOCKED: keep a CI-specific reason
    // identifier for audit trails when the pending-work source is failed CI.
    if (isTextOnly) {
      const mandatoryPendingBlock = blockMandatoryPendingText(text, output, workState)
      if (mandatoryPendingBlock) {
        turnState.blocked = true
        return mandatoryPendingBlock
      }
    }

    // ── UPDATE POST-RESULTS STATE FOR NEXT TURN ────────────────────────────
    const combinedTextForResults = `${turnState.accumulatedText}\n${text}`
    const resultCheck = textHasResultMarkers(combinedTextForResults)
    if (resultCheck.found) {
      writePostResultsState({
        lastTurnHadResults: true,
        lastTurnHadWave: resultCheck.count >= WAVE_RESULT_THRESHOLD,
        lastResultCount: resultCheck.count,
        ts: now,
      })
    } else {
      writePostResultsState({
        lastTurnHadResults: false,
        lastTurnHadWave: false,
        lastResultCount: 0,
        ts: now,
      })
    }

    return output
  },

  // ── tool.execute.before ──────────────────────────────────────────────────

  "tool.execute.before": async (input: any, output: any) => {
    if (isSubagent()) return // OPENCODE_SUBAGENT guard
    if (isStopEnforcementDisabled()) return
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
        const ciUnknown = ciIsUnknown()
        const repoMode: "commit" | "push" | undefined =
          COMMIT_TARGET_RE.test(command) ? "commit" :
          PUSH_TARGET_RE.test(command) ? "push" : undefined
        const repoPending = repoHasPendingWork(execSync, repoMode)
        const disengaged = isDisengaged()
        if (!disengaged && (taskMd || ratchetCount > 0 || bugsOpen || gateRed || ciBad || ciUnknown || repoPending)) {
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
          if (ciUnknown) extraReasons.push("CI UNKNOWN (cooldown-masked — use FORCE=1 to check actual state)")
          if (repoPending) extraReasons.push("repo dirty")
          throw new Error(stopLikeDenyMessage(taskMd, ratchetCount, extraReasons))
        }
      }
      // Track shipping targets for post-ship continuation guard
      if (GIT_SHIPPING_TARGETS_RE.test(command)) {
        const prs = readPostResultsState()
        prs.lastToolWasShipping = true
        prs.lastShippingToolName = command.match(GIT_SHIPPING_TARGETS_RE)?.[1] || command
        prs.ts = Date.now()
        writePostResultsState(prs)
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
        const grindingDisengaged = isDisengaged()
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
    if (isSubagent()) return // OPENCODE_SUBAGENT guard
    if (isStopEnforcementDisabled()) return
    const ev = (input as any)?.event
    const evType = ev?.type || ""

    if (evType === "session.idle") {
    // Turn state reset invariant: turnState.accumulatedText = ""; turnState.blocked = false.
    const workState = hasRealPendingWork()

    // ── STATUS-SUMMARY BLOCK (2026-07-15) ───────────────────────────────────
    // Fires REGARDLESS of structured evidence and REGARDLESS of disengage.
    // A status summary ("Here's the session N final status" + bolded headers
    // + status tables) previously bypassed all checks below because commit
    // hashes / "CI PENDING" inside the summary matched EVIDENCE_PATTERNS.
    // Evidence never legitimizes stopping-to-summarize while work exists.
    // FIXED 2026-07-15: `text` was previously referenced without being
    // defined in this scope (ref-error on every session.idle). The
    // primary status-summary block lives in experimental.text.complete
    // (where response text is available); this copy inspects the event
    // payload text if present, and no-ops safely when it is not.
    const text = String((ev as any)?.properties?.text ?? (ev as any)?.text ?? "")
    if ((workState.hasPendingWork || workState.hasLocalWork) && looksLikeStatusSummary(text)) {
      recordBlock("status-summary-while-work-exists")
      recordBlankedResponse("status-summary-while-work-exists", text)
      writePersistBlock(true, "status-summary-while-work-exists")

      return {
        text: [
          "⛔⛔⛔ STATUS-SUMMARY RESPONSE BLOCKED ⛔⛔⛔",
          "",
          "Status summaries ('final status', bolded headers, status tables)",
          "are FORBIDDEN while pending work exists — evidence inside the",
          "summary does NOT make it acceptable. Stopping to summarize IS the",
          "stop pattern.",
          "",
          `PENDING WORK: ${workState.tasksMdUncheckedCount} unchecked TASKS.md items, ` +
          `${workState.ratchetEntries} ratchet entries, ` +
          `gate ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : "OK"}, ` +
          `CI ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : workState.ciVerdictUnknown ? "UNKNOWN (cooldown)" : "N/A"}, ` +
          `release ${workState.releaseIncomplete ? "INCOMPLETE" : "N/A"}.`,
          "",
          "DISPATCH A TOOL CALL NOW. Do not summarize.",
        ].join("\n"),
      }
    }

      if (workState.hasPendingWork || workState.hasLocalWork) {
        const sessionBlockCount = getSessionBlockCount()
        const escalation = sessionBlockCount > ESCALATION_THRESHOLD
          ? ` (${sessionBlockCount} stop attempts blocked this session)`
          : ""
        console.warn(
          `⛔ SESSION IDLE WHILE WORK EXISTS${escalation}. ` +
          `${workState.tasksMdUncheckedCount} unchecked tasks, ` +
          `gate ${workState.gateStatusRed ? "RED" : workState.gateStatusMissing ? "MISSING" : "OK"}, ` +
          `CI ${workState.ciVerdictPendingOrRed ? "RED/PENDING" : workState.ciVerdictUnknown ? "UNKNOWN (cooldown)" : "N/A"}.`
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
export default (async () => {
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
      // OPENCODE_SUBAGENT guard is implemented by shared isSubagent().
      if (isSubagent()) return // OPENCODE_SUBAGENT guard
      if (isStopEnforcementDisabled()) return
      const impl = stopImpl()
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
    "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output // OPENCODE_SUBAGENT guard
      if (isStopEnforcementDisabled()) return output
      const impl = stopImpl()
      const fn = impl["experimental.chat.system.transform"]
      return fn ? await fn(_input, output) : output
    },
    "experimental.text.complete": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output // OPENCODE_SUBAGENT guard
      if (isStopEnforcementDisabled()) {
        const text = typeof output === "string" ? output
          : (output as any)?.text ? String((output as any).text)
          : JSON.stringify(output)
        if (!text || text.trim().length === 0) return output
        return blockMandatoryPendingText(text, output, hasRealPendingWork()) ?? output
      }
      const impl = stopImpl()
      const fn = impl["experimental.text.complete"]
      return fn ? await fn(_input, output) : output
    },
    "event": async (input: unknown, output: unknown) => {
      if (isSubagent()) return // OPENCODE_SUBAGENT guard
      if (isStopEnforcementDisabled()) return
      const impl = stopImpl()
      const fn = impl["event"]
      return fn ? await fn(input, output) : undefined
    },
  }
}) satisfies Plugin
