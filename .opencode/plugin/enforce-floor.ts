import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { execSync } from "node:child_process"
import { isSubagent, isDisengaged, reportAlive, readJsonFile, writeJsonFile, isDispatchTool, isReadTool, ALIVE_PATH, DISENGAGE_PATH, updateSharedStreak, writeHeartbeat } from "../lib/shared.ts"

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
//
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
// check /tmp/gludd-hot-floor.js on every invocation.  Run `make hot-reload-plugins`
// after editing this file to generate the hot module.

// Live overrides
// isSubagent imported from shared.ts

function _tunable(overridePath: string, envVar: string, dflt: string): number {
  let base = parseInt(process.env[envVar] || dflt, 10)
  try {
    const raw = fs.readFileSync(overridePath, "utf8").trim()
    if (/^\d+$/.test(raw)) base = parseInt(raw, 10)
  } catch { /* no override file -> env/default */ }
  return base
}
const FLOOR = _tunable("/tmp/gludd-floor-override", "CLAUDE_AGENT_FLOOR", "10")
const CEILING = _tunable("/tmp/gludd-ceiling-override", "CLAUDE_AGENT_CEILING", "10")
const TARGET = Math.min(
  parseInt(process.env.CLAUDE_AGENT_TARGET || "10", 10),
  CEILING,
)

const FLOOR_ENFORCE = process.env.GLUDD_FLOOR_ENFORCE !== "0"

// ── SHARED STREAK STATE ─── imported from shared.ts ────────────────────────────
const STREAK_PLUGIN_NAME = "enforce-floor"

function isCommitBashCommand(cmd: string): boolean {
  return /^make\s+(git-commit|commit-no-verify|git-commit-file|test-and-commit|repo-commit|feature-done|git-merge)(\s|$)/.test(cmd)
}

// Open-work probe
function openWorkExists(options?: { isCommitTool?: boolean }): boolean {
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
    const todoState = process.env.GLUDD_TODOWRITE_STATE || "/tmp/gludd-todowrite-state.json"
    try {
      if (fs.existsSync(todoState)) {
        const todos = JSON.parse(fs.readFileSync(todoState, "utf8"))
        if (Array.isArray(todos)) {
          const hasPending = todos.some((t: any) =>
            (t.status === "pending" || t.status === "in_progress"))
          if (hasPending) return true
        }
      }
    } catch { /* ignore */ }
    const tasksMd = process.env.GLUDD_TASKS_MD || path.join(process.cwd(), "TASKS.md")
    try {
      if (fs.existsSync(tasksMd)) {
        const tasksSrc = fs.readFileSync(tasksMd, "utf8")
        const unchecked = tasksSrc
          .split("\n")
          .filter(l => /^\s*[-*]\s+\[\s*\]/.test(l))
        if (unchecked.length > 0) return true
      }
    } catch { /* ignore */ }
    const bugsMd = process.env.GLUDD_BUGS_MD || path.join(process.cwd(), "BUGS.md")
    try {
      if (fs.existsSync(bugsMd)) {
        const bugsSrc = fs.readFileSync(bugsMd, "utf8")
        const openIncidents = bugsSrc
          .split("\n")
          .filter(l => /^###\s+\d{4}-\d{2}-\d{2}\s+[-—]/.test(l))
          .filter(l => !/\b(resolved|fixed|closed|wontfix|duplicate)\b/i.test(l))
        if (openIncidents.length > 0) return true
      }
    } catch { /* ignore */ }
    try {
      const gatePath = path.join(process.cwd(), ".gate-status")
      if (fs.existsSync(gatePath)) {
        const content = fs.readFileSync(gatePath, "utf8")
        for (const line of content.split("\n")) {
          if (line.startsWith("===")) continue
          if (/FAIL/.test(line) || /incomplete/i.test(line)) return true
        }
      }
    } catch { /* ignore */ }
    try {
      const ciCachePath = "/tmp/gludd-watchdog-ci.json"
      if (fs.existsSync(ciCachePath)) {
        const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
        const lastCheck = ciData.last_ci_check || 0
        const lastStatus = ciData.last_ci_status || ""
        if (Date.now() - lastCheck < 120_000 && lastStatus && lastStatus !== "SUCCESS") return true
      }
      const stopStatePath = "/tmp/gludd-stop-state.json"
      if (fs.existsSync(stopStatePath)) {
        const state = JSON.parse(fs.readFileSync(stopStatePath, "utf8"))
        if (state.ciVerdictPendingOrRed) return true
      }
    } catch { /* ignore */ }
    try {
      if (options?.isCommitTool) { /* skip mtime check for commit tools */ }
      else {
        const index = path.join(process.cwd(), ".git", "index")
        const headRef = path.join(process.cwd(), ".git", "refs", "heads", "master")
        if (fs.existsSync(index) && fs.existsSync(headRef)) {
          const idxMtime = fs.statSync(index).mtimeMs
          const refMtime = fs.statSync(headRef).mtimeMs
          if (Math.abs(idxMtime - refMtime) > 2000) return true
        }
      }
    } catch { /* ignore */ }
    try {
      if (options?.isCommitTool) {
        const unstaged = execSync("git diff --name-only", {
          cwd: process.cwd(), encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
        })
        if (unstaged.trim().length > 0) return true
      } else {
        const status = execSync("git status --porcelain", {
          cwd: process.cwd(), encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
        })
        if (status.trim().length > 0) return true
      }
    } catch { /* ignore */ }
    return false
  } catch {
    return false
  }
}

// =============================================================================
// Dispatch-lifecycle awareness
// =============================================================================

const MAX_STREAK = 2
let _streakCount = 0

const POST_RESULT_READ_LIMIT = 3

const SESSION_START_WINDOW_MS = 90_000
const SESSION_START_TIME_BLOCK_MS = 60_000
const SESSION_START_READ_WARN = 3
const SESSION_START_READ_DENY = 6
const SESSION_START_STREAK_MAX = 1
function _getSessionStartTs(): number {
  try {
    const stateFile = process.env.GLUDD_SESSION_STATE || "/tmp/gludd-session-start.json"
    if (fs.existsSync(stateFile)) {
      const raw = JSON.parse(fs.readFileSync(stateFile, "utf8"))
      if (typeof raw.started_at === "number" && raw.started_at > 0) return raw.started_at
    }
  } catch {}
  return 0
}
function _isInSessionStartWindow(): boolean {
  const sst = _getSessionStartTs()
  if (sst === 0) return false
  return (Date.now() - sst) < SESSION_START_WINDOW_MS
}
let _sessionDispatchCount = 0

let _readStreak = 0
let _lastDispatchTs = Date.now()

let _dispatchCount = 0
let _dispatchPeak = 0
let _resultProcessingGrace = 0
let _needsRefill = false
let _consecutiveReadsAfterResults = 0

const COMPULSIVE_CHECK_RE = /^make\s+(git-log|ci-verdict|git-diff)(\s|\/|$)/

let _thisMessageDispatchCount = 0
let _thisMessageTotalCalls = 0
let _prevMessageDispatchCount = 0

const RESULT_MARKERS = [
  "task result",
  "completed",
  "agent result",
  "workflow result",
  "subagent result",
  "returning result",
  "final result",
]
const REFILL_THRESHOLD = 3
const PEAK_DISPATCH = 5
const RESULT_GRACE_CALLS = 2

function _textHasResultMarker(text: string): boolean {
  const lower = text.toLowerCase()
  return RESULT_MARKERS.some(m => lower.includes(m))
}

function _updateRefillState(): void {
  _needsRefill = _dispatchPeak >= PEAK_DISPATCH && _dispatchCount < REFILL_THRESHOLD
}

interface DispatchCommand { index: number; tool: string; task_item: string }
function _buildDispatchCommands(): DispatchCommand[] {
  const commands: DispatchCommand[] = []
  let idx = 1
  try {
    const tasksMd = process.env.GLUDD_TASKS_MD || path.join(process.cwd(), "TASKS.md")
    if (fs.existsSync(tasksMd)) {
      for (const line of fs.readFileSync(tasksMd, "utf8").split("\n")) {
        if (/^\s*[-*]\s+\[\s*\]/.test(line)) {
          const item = line.replace(/^\s*[-*]\s+\[\s*\]\s*/, "").trim().substring(0, 100)
          commands.push({ index: idx++, tool: "task", task_item: item })
        }
      }
    }
  } catch { /* best-effort */ }
  try {
    const ratchet = path.join(process.cwd(), "config", "ratchet.yml")
    if (fs.existsSync(ratchet)) {
      const count = fs.readFileSync(ratchet, "utf8")
        .split("\n")
        .filter(l => l.trim() && !l.trim().startsWith("#") && l.includes(":"))
        .length
      if (count > 0) {
        commands.push({ index: idx++, tool: "task", task_item: `ratchet: fix ${count} entries` })
      }
    }
  } catch { /* best-effort */ }
  try {
    const gs = path.join(process.cwd(), ".gate-status")
    if (fs.existsSync(gs)) {
      const content = fs.readFileSync(gs, "utf8")
      if (/FAIL/.test(content)) {
        commands.push({ index: idx++, tool: "task", task_item: "gate: red — fix failures" })
      }
    }
  } catch { /* best-effort */ }
  return commands
}

const floorTurnState: { accumulatedText: string } = { accumulatedText: "" }

// reportAlive + writeHeartbeat imported from shared.ts

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, output: any) => {
    if (isSubagent()) return
    console.log("SUBAGENT SKIP: enforce-floor")
    reportAlive("enforce-floor")
    writeHeartbeat("enforce-floor")

    try {
      if (!FLOOR_ENFORCE) return
      const tool = (input?.tool ?? "") as string

      if (isDisengaged()) {
        _streakCount = 0
        _readStreak = 0
        return
      }

      updateSharedStreak(tool, STREAK_PLUGIN_NAME)

      if (isDispatchTool(tool)) {
        _streakCount = 0
        _readStreak = 0
        _lastDispatchTs = Date.now()
        _dispatchCount++
        _thisMessageDispatchCount++
        _thisMessageTotalCalls++
        _sessionDispatchCount++
        if (_dispatchCount > _dispatchPeak) {
          _dispatchPeak = _dispatchCount
        }
        _needsRefill = false
        _consecutiveReadsAfterResults = 0
        return
      }

      if (_isInSessionStartWindow() && _sessionDispatchCount === 0) {
        const sst = _getSessionStartTs()
        if ((Date.now() - sst) > SESSION_START_TIME_BLOCK_MS && openWorkExists()) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "⛔  SESSION-START DISPATCH STALL — TOOL CALL BLOCKED",
              "",
              `${Math.round((Date.now() - sst) / 1000)}s elapsed since session start with ZERO dispatches.`,
              "The agent read task files but has not dispatched any subagents.",
              "All non-dispatch tool calls are blocked until a dispatch wave starts.",
              "",
              "REQUIRED: Dispatch task/agent subagents on pending work NOW.",
              "No more reads, edits, or status probes.  DISPATCH FIRST.",
            ].join("\n"),
          }
        }
      }

      if (isReadTool(tool)) {
        _thisMessageTotalCalls++
        _readStreak++
        if (_isInSessionStartWindow()) {
          if (_readStreak > SESSION_START_READ_DENY) {
            return {
              permissionDecision: "deny" as const,
              message: [
                "SESSION-START READ-GRINDING — READ BLOCKED",
                "",
                `${_readStreak} consecutive reads with 0 dispatches in session-start window.`,
                "DISPATCH subagents NOW — reading more files before dispatching",
                "is the session-start grind anti-pattern.",
              ].join("\n"),
            }
          }
          if (_readStreak > SESSION_START_READ_WARN) {
            console.warn(
              `SESSION-START READ NUDGE: ${_readStreak} reads, 0 dispatches. DISPATCH NOW.`
            )
          }
        }
        if (_resultProcessingGrace > 0) {
          _consecutiveReadsAfterResults++
          if (_consecutiveReadsAfterResults > POST_RESULT_READ_LIMIT) {
            return {
              permissionDecision: "deny" as const,
              message: [
                "⛔  POST-RESULT READ LIMIT EXCEEDED — READ BLOCKED",
                "",
                `${_consecutiveReadsAfterResults} consecutive reads after subagent results arrived.`,
                `Maximum ${POST_RESULT_READ_LIMIT} reads allowed in the post-result phase.`,
                "File inspection between result waves is the dispatch-gap anti-pattern.",
                "",
                "REQUIRED: Dispatch task/agent subagents on pending work NOW.",
                "Reads are a bridge to dispatch, not a replacement for it.",
              ].join("\n"),
            }
          }
        }

        if (_readStreak > 10 && (Date.now() - _lastDispatchTs) > 60_000) {
          const sinceDispatchMs = Date.now() - _lastDispatchTs
          return {
            permissionDecision: "deny" as const,
            message: [
              "READ-GRINDING DETECTED — READ BLOCKED",
              "",
              `${_readStreak} consecutive reads with no dispatch, ${Math.round(sinceDispatchMs / 1000)}s since last dispatch.`,
              "An agent doing 10+ serial reads over 1+ minute without dispatching",
              "is grinding inline instead of delegating. DISPATCH WORK.",
              "",
              "REQUIRED: Dispatch task/agent subagents on pending work.",
              "Reads are for investigating BETWEEN dispatch waves, not replacing them.",
            ].join("\n"),
          }
        }
        if (_readStreak > 5 && (Date.now() - _lastDispatchTs) > 30_000) {
          const sinceDispatchMs = Date.now() - _lastDispatchTs
          console.warn(
            `READ-GRINDING DETECTED: ${_readStreak} consecutive reads, ` +
            `${Math.round(sinceDispatchMs / 1000)}s since last dispatch. DISPATCH WORK.`
          )
        }
        return
      }

      _thisMessageTotalCalls++

      let commitToolMode = false
      if (tool === "bash") {
        const outArgs = (output as Record<string, unknown> | undefined)?.args as { command?: string } | undefined
        const cmd = typeof outArgs?.command === "string" ? outArgs.command.trim() : ""
        commitToolMode = isCommitBashCommand(cmd)
        if (COMPULSIVE_CHECK_RE.test(cmd) && openWorkExists()) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "⛔ COMPULSIVE-CHECK LOOP BLOCKED",
              `Command: ${cmd}`,
              "",
              "make git-log / make ci-verdict / make git-diff as a standalone",
              "bash call is the compulsive-check loop pattern. If you are reaching",
              "for one of these, you are in the loop.",
              "",
              "REQUIRED: Dispatch the check to a Task/agent subagent instead.",
              "The main thread must only dispatch, not run status probes.",
              "",
              "BREAK THE LOOP: dispatch ≥5 subagents on pending work now.",
            ].join("\n"),
          }
        }
      }

      if (isDisengaged()) {
        _streakCount = 0
        return
      }

      if (_prevMessageDispatchCount > 0 && _prevMessageDispatchCount < 5 && openWorkExists({ isCommitTool: commitToolMode })) {
        return {
          permissionDecision: "deny" as const,
          message: [
            "⛔ MESSAGE-SHAPE VIOLATION — MUST DISPATCH ≥5 PER WAVE",
            "",
            `Previous message dispatched only ${_prevMessageDispatchCount} subagent(s).`,
            "The message-shape rule (AGENTS.md) requires ZERO or ≥5 dispatches per",
            "agent response.  1–4 dispatches is the dribbling anti-pattern.",
            "",
            "Your next message MUST contain ≥5 parallel task/agent dispatches.",
            "Do not proceed with serial tool calls.  BATCH YOUR DISPATCHES.",
            "",
            "CORRECT: send one message with 5+ Task tool calls in parallel.",
            "INCORRECT: send one dispatch, wait, send another.",
          ].join("\n"),
        }
      }

      if (_resultProcessingGrace > 0) {
        _resultProcessingGrace--
        _streakCount = 0
        return {
          permissionDecision: "deny" as const,
          message: [
            "⛔  DISPATCH GAP — RESULTS RECEIVED, DISPATCH FIRST",
            "",
            "Subagent results just arrived.  You are running inline tool calls",
            "(bash/edit/write) instead of dispatching the next wave.  This",
            "creates a dispatch gap where the subagent floor drains.",
            "",
            "ALLOWED right now:",
            "  → task/agent dispatches (REQUIRED — dispatch pending work NOW)",
            "  → read/grep/glob (free — to survey results and prepare next wave)",
            "",
            "FORBIDDEN right now:",
            "  → bash, edit, write — NO inline work between results and dispatch",
            "",
            "DISPATCH FIRST.  Reads are free.  Inline work after results is a bug.",
          ].join("\n"),
        }
      }

      if (isDisengaged()) {
        _streakCount = 0
        return
      }

      _streakCount++
      const effectiveMax = _isInSessionStartWindow() ? SESSION_START_STREAK_MAX : MAX_STREAK
      if (_streakCount <= effectiveMax) return
      if (!openWorkExists({ isCommitTool: commitToolMode })) {
        _streakCount = 0
        return
      }

      const commands = _buildDispatchCommands()

      const lines = [
        "",
        "█████████████████████████████████████████████████████████████████████████████",
        "█                                                                               █",
        "█  ⛔  AGENT-FLOOR BREACH — NON-DISPATCH CALL BLOCKED  ⛔                      █",
        "█                                                                               █",
        `█  ${_streakCount} consecutive non-dispatch calls (MAX allowed: ${effectiveMax}).                      █`,
        `█  FLOOR = ${FLOOR} subagents.  TARGET = ${TARGET}.  Current pool: BELOW FLOOR.                  █`,
        "█                                                                               █",
        "█  THE ONLY ALLOWED TOOL CALLS RIGHT NOW:                                       █",
        "█    → task   (launch a subagent on concrete work)                              █",
        "█    → agent  (launch a subagent on concrete work)                              █",
        "█                                                                               █",
        "█  DO NOT: read, write, edit, grep, glob, or bash.  DISPATCH SUBAGENTS.         █",
        "█                                                                               █",
        "█  Blocking mode is ON.  Set GLUDD_FLOOR_ENFORCE=0 to disable.            █",
        "█  Set GLUDD_FLOOR_ENFORCE=0 to disable.                                     █",
        "█                                                                               █",
      ]
      const sep = "█" + (" ".repeat(79))
      lines.push(sep)
      lines.push("█  == SPECIFIC DISPATCH COMMANDS ==" + (" ".repeat(44)))
      lines.push(sep)
      if (commands.length === 0) {
        lines.push("█  (no TASKS.md/ratchet/gate items found — dispatch RESEARCH tasks)" + (" ".repeat(9)))
      } else {
        for (const c of commands) {
          const item = `  ${c.index}. tool="${c.tool}" | ${c.task_item}`
          lines.push("█  " + item + (" ".repeat(Math.max(0, 76 - item.length))))
        }
      }
      lines.push(sep)
      lines.push("█████████████████████████████████████████████████████████████████████████████")
      lines.push("")
      return {
        permissionDecision: "deny" as const,
        message: lines.join("\n"),
      }
    } catch {
      return
    }
  },

  "session.idle": async (..._args: any[]) => {
    floorTurnState.accumulatedText = ""
    _resultProcessingGrace = 0
    _readStreak = 0
    _consecutiveReadsAfterResults = 0
    _updateRefillState()
    _thisMessageDispatchCount = 0
    _thisMessageTotalCalls = 0
    _prevMessageDispatchCount = 0
  },

  "experimental.text.complete": async (_input: any, output: any) => {
    if (isSubagent()) return output
    console.log("SUBAGENT SKIP: enforce-floor")
    if (/^(⛔|HARD STOP|MUST DISPATCH|ENHANCEMENT RATIO|████|BLOCKED:|MULTITASK|INSUFFICIENT DISPATCHES|ZERO-DISPATCH|DISPATCH SUBAGENTS|EARLY ENHANCEMENT|DELEGATE-FIRST|REFILL NEEDED|AFTER-RESULTS|CONSECUTIVE TEXT-ONLY|FALSE-DONE|QA RESPONSE)/.test((output?.text ?? "").trim())) return output
    try {
      try {
        const cPath = process.env.GLUDD_FLOOR_TEXT_COMPLETE_COUNT || "/tmp/gludd-floor-text-complete-count.json"
        let count = 1
        if (fs.existsSync(cPath)) {
          try { const d = JSON.parse(fs.readFileSync(cPath, "utf8")); count = (parseInt(d.count, 10) || 0) + 1 } catch {}
        }
        fs.writeFileSync(cPath, JSON.stringify({ count, last_fired: new Date().toISOString(), ts: Date.now() }), "utf8")
      } catch {}

      _prevMessageDispatchCount = _thisMessageDispatchCount
      _thisMessageDispatchCount = 0
      _thisMessageTotalCalls = 0

      if (!output || typeof output.text !== "string") return output
      floorTurnState.accumulatedText += output.text

      if (_textHasResultMarker(output.text) && _resultProcessingGrace === 0) {
        _dispatchCount = Math.max(0, _dispatchCount - 2)
        _resultProcessingGrace = RESULT_GRACE_CALLS
        _streakCount = 0
        _consecutiveReadsAfterResults = 0
      }

      _updateRefillState()

      if (_isInSessionStartWindow() && _sessionDispatchCount === 0) {
        const sst = _getSessionStartTs()
        if ((Date.now() - sst) > SESSION_START_TIME_BLOCK_MS) {
          return {
            text: [
              "⛔⛔⛔ DISPATCH NOW — SESSION-START WINDOW ⛔⛔⛔",
              "",
              `${Math.round((Date.now() - sst) / 1000)}s since session start with ZERO dispatches.`,
              "The session-start window requires dispatching subagents within",
              "the first ~60s.  Read task files — now DISPATCH.",
              "",
              "DO NOT respond with prose, planning, or analysis.",
              "Your next message MUST contain parallel Task/agent dispatches.",
              "",
              output.text,
            ].join("\n"),
          }
        }
      }

      if (_needsRefill && _streakCount <= MAX_STREAK) {
        return {
          text: [
            "⛔ REFILL NEEDED — Subagent pool has drained below threshold.",
            `Dispatch peak was ${_dispatchPeak}, current count estimate: ${_dispatchCount}.`,
            "Dispatch ≥5 new subagents on the next wave.",
            "",
            output.text,
          ].join("\n"),
        }
      }

      if (_streakCount > MAX_STREAK) {
        const cmds = _buildDispatchCommands()
        const cmdLines: string[] = []
        if (cmds.length === 0) {
          cmdLines.push("(no TASKS.md/ratchet/gate items — dispatch research tasks)")
        } else {
          for (const c of cmds) {
            cmdLines.push(`  ${c.index}. tool="${c.tool}" | ${c.task_item}`)
          }
        }
        return {
          text: [
            "",
            "████████████████████████████████████████████████████████████████████████",
            "█                                                                      █",
            "█  ⛔  FLOOR BREACH — TEXT RESPONSE REPLACED BY GUARDRAIL  ⛔          █",
            "█                                                                      █",
            `█  ${_streakCount} non-dispatch calls without a dispatch.  Floor=${FLOOR}, target=${TARGET}.    █`,
            "█  THE AGENT MAY NOT SEND PROSE WHILE THE SUBAGENT POOL IS EMPTY.      █",
            "█  ALL USER-FACING TEXT HAS BEEN SUPPRESSED BY THIS GUARDRAIL.         █",
            "█                                                                      █",
            "█  REQUIRED NEXT ACTION:                                               █",
            "█    → Dispatch task/agent calls (≥5 in parallel) on pending work.     █",
            "█                                                                      █",
            "█  DISPATCH COMMANDS:                                                  █",
            ...cmdLines.map(l => "█  " + l + (" ".repeat(Math.max(0, 66 - l.length))) + "█"),
            "█                                                                      █",
            "█  Do not deliberate.  Do not explain.  DISPATCH NOW.                  █",
            "█                                                                      █",
            "████████████████████████████████████████████████████████████████████████",
            "",
          ].join("\n"),
        }
      }
      return output
    } catch (e) {
      try {
        fs.writeFileSync('/tmp/gludd-enforce-floor-error.log',
          `${new Date().toISOString()} ${String(e)}\n`)
      } catch {}
      console.error('enforce-floor text.complete error:', String(e))
      return output
    }
  },
}

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  // LOADED self-check
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-floor ` +
      `tool.execute.before+session.idle+experimental.text.complete ` +
      `pid=${process.pid}\n`,
      "utf8",
    )
  } catch { /* fail-open */ }

  // Stale-state cleanup (P3 fix, 2026-07-12)
  try {
    const GRIND_FILE = process.env.GLUDD_READ_GRIND_FILE || "/tmp/gludd-read-grind.json"
    if (fs.existsSync(GRIND_FILE)) {
      const obj = JSON.parse(fs.readFileSync(GRIND_FILE, "utf8"))
      const ts = typeof obj.lastDispatchTs === "number" ? obj.lastDispatchTs
               : typeof obj.ts === "number" ? obj.ts : 0
      const age = Date.now() - ts
      if (age > 60_000) {
        const tmp = GRIND_FILE + ".tmp"
        fs.writeFileSync(tmp, JSON.stringify({ count: 0, lastDispatchTs: Date.now(), ts: Date.now() }), "utf8")
        fs.renameSync(tmp, GRIND_FILE)
      }
    }
  } catch { /* fail-open */ }

  return {
    "tool.execute.before": async (input: any, output: any) => {
      const impl = loadHotModule("floor", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },

    "experimental.text.complete": async (_input: any, output: any) => {
      const impl = loadHotModule("floor", defaultImpl)
      const fn = impl["experimental.text.complete"] || impl["text.complete"]
      return fn ? await fn(_input, output) : output
    },
  }
}) satisfies Plugin
