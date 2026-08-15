import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { createRequire } from "node:module"
import { isSubagent, isDisengaged, reportAlive, readJsonFile, writeJsonFile, isDispatchTool, isReadTool, ALIVE_PATH, DISENGAGE_PATH, updateSharedStreak, writeHeartbeat, getProjectRoot, getSessionStartMtimeMs, isInPressureRelease, isInInlineRecovery, readDispatchOutcomes, hasTasksMdPendingWork } from "../lib/shared.ts"
const nodeRequire = typeof require === "function" ? require : createRequire(import.meta.url)
function execSync(...args: any[]): Buffer {
  return nodeRequire("node:child_" + "process").execSync(...args)
}
// Floor+ceiling enforcement guardrail. FAIL-OPEN: any error -> do nothing.
//
// opencode ≥1.17.9 removed text.complete. This version is self-contained in
// tool.execute.before: message boundaries detected via 5s inter-call timeout,
// result-processing grace via time-since-last-dispatch, streak tracking in
// tool.execute.before module-level state.
// Live overrides
function _tunable(overridePath: string, envVar: string, dflt: string): number {
  let base = parseInt(process.env[envVar] || dflt, 10)
  try {
    const raw = fs.readFileSync(overridePath, "utf8").trim()
    if (/^\d+$/.test(raw)) base = parseInt(raw, 10)
  } catch {}
  return base
}
const FLOOR = _tunable("/tmp/gludd-floor-override", "CLAUDE_AGENT_FLOOR", "10")
const CEILING = _tunable("/tmp/gludd-ceiling-override", "CLAUDE_AGENT_CEILING", "10")
const TARGET = Math.min(
  parseInt(process.env.CLAUDE_AGENT_TARGET || "10", 10),
  CEILING,
)
// A wave is the complete set of parallel dispatches in one assistant message.
// Keep this independently tunable for plugin e2e tests, but default to the
// project-wide ten-agent ceiling.
const WAVE_WIDTH = _tunable(
  "/tmp/gludd-dispatch-wave-width",
  "GLUDD_DISPATCH_WAVE_WIDTH",
  "10",
)
const FLOOR_ENFORCE = process.env.GLUDD_FLOOR_ENFORCE !== "0"
const STREAK_PLUGIN_NAME = "enforce-floor"
const DISPATCH_PREFLIGHT_PATH = "/tmp/gludd-dispatch-preflight.json"
const MISSED_COMMIT_FILE = "/tmp/gludd-missed-commit-dispatch.json"
const MISSED_COMMIT_THRESHOLD = 3
const MISSED_COMMIT_REMINDER_MS = 300_000

interface MissedCommitState {
  misses: number
  last_miss_ts: number
  last_reminder_ts: number
  pid: number
}

function readMissedCommitState(): MissedCommitState {
  const state = readJsonFile(MISSED_COMMIT_FILE) as Partial<MissedCommitState> | null
  return {
    misses: typeof state?.misses === "number" ? state.misses : 0,
    last_miss_ts: typeof state?.last_miss_ts === "number" ? state.last_miss_ts : 0,
    last_reminder_ts: typeof state?.last_reminder_ts === "number" ? state.last_reminder_ts : 0,
    pid: typeof state?.pid === "number" ? state.pid : 0,
  }
}

function recordMissedCommit(): void {
  try {
    const state = readMissedCommitState()
    writeJsonFile(MISSED_COMMIT_FILE, {
      ...state,
      misses: state.misses + 1,
      last_miss_ts: Date.now(),
      pid: process.pid,
    })
  } catch {}
}

function maybeRemindMissedCommitDispatch(): void {
  try {
    const state = readMissedCommitState()
    const now = Date.now()
    if (state.misses < MISSED_COMMIT_THRESHOLD) return
    if (now - state.last_reminder_ts < MISSED_COMMIT_REMINDER_MS) return
    console.warn(
      "DP.1: Use one dispatch slot for make ship-commit — keeps 9 productive tasks running.",
    )
    writeJsonFile(MISSED_COMMIT_FILE, { ...state, last_reminder_ts: now })
  } catch {}
}
// Shared state filenames used through shared.ts: gludd-tool-streak.json, watchdog disengage state.
// ── Time-based message boundary detection ──────────────────────────────────
// Inter-call gap that marks a new agent message. Env-tunable so e2e tests can
// drive the real boundary logic without 5s sleeps; production default unchanged.
const MESSAGE_BOUNDARY_MS = parseInt(
  process.env.GLUDD_MESSAGE_BOUNDARY_MS || "5000", 10,
)
const POST_DISPATCH_GRACE_MS = 15000
const RESULT_PHASE_READ_LIMIT = 3
const THROTTLE_PATH = "/tmp/gludd-load-throttle"
const THROTTLE_ACTIVE_MS = 120_000
const THROTTLE_STALE_MS = 300_000
function getEffectiveFloor(): { floor: number; waveWidth: number; target: number; throttled: boolean } {
  try {
    if (!fs.existsSync(THROTTLE_PATH)) return { floor: FLOOR, waveWidth: WAVE_WIDTH, target: TARGET, throttled: false }
    const raw = JSON.parse(fs.readFileSync(THROTTLE_PATH, "utf8"))
    const ts = typeof raw.ts === "number" ? raw.ts : 0
    const age = Date.now() - ts
    if (ts === 0 || age > THROTTLE_STALE_MS || age < 0) {
      return { floor: FLOOR, waveWidth: WAVE_WIDTH, target: TARGET, throttled: false }
    }
    if (age > THROTTLE_ACTIVE_MS) {
      return { floor: FLOOR, waveWidth: WAVE_WIDTH, target: TARGET, throttled: false }
    }
    const load = typeof raw.load === "number" ? raw.load : 0
    const throttleFloor = typeof raw.floor === "number" && raw.floor >= 0 ? raw.floor : FLOOR
    const effectiveFloor = Math.min(FLOOR, throttleFloor)
    console.warn(
      `LOAD THROTTLE ACTIVE: load=${load.toFixed(2)}, age=${Math.round(age / 1000)}s, ` +
      `throttle_floor=${throttleFloor}, effective_floor=${effectiveFloor}, normal_floor=${FLOOR}`
    )
    if (effectiveFloor === 0) {
      return { floor: 0, waveWidth: 0, target: 0, throttled: true }
    }
    const ratio = effectiveFloor / FLOOR
    return {
      floor: effectiveFloor,
      waveWidth: Math.max(2, Math.round(WAVE_WIDTH * ratio)),
      target: Math.max(2, Math.round(TARGET * ratio)),
      throttled: true,
    }
  } catch {
    return { floor: FLOOR, waveWidth: WAVE_WIDTH, target: TARGET, throttled: false }
  }
}
// ── Helpers ────────────────────────────────────────────────────────────────
function isCommitBashCommand(cmd: string): boolean {
  return /^make\s+(git-commit|commit-no-verify|git-commit-file|test-and-commit|repo-commit|feature-done|git-merge)(\s|$)/.test(cmd)
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
  return incidentSections.filter(incident =>
    !/\b(?:resolved|fixed|closed|wontfix|duplicate)\b/i.test(incident)
  ).length
}
function openWorkExists(options?: { isCommitTool?: boolean }): boolean {
  const root = getProjectRoot()
  try {
    const ratchet = path.join(root, "config", "ratchet.yml")
    if (fs.existsSync(ratchet)) {
      const entries = fs.readFileSync(ratchet, "utf8")
        .split("\n")
        .filter(l => l.trim() && !l.trim().startsWith("#") && l.includes(":"))
      if (entries.length > 0) return true
    }
    const backlog = path.join(root, "scripts", "multitasking_backlog.json")
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
    } catch {}
    const tasksMd = process.env.GLUDD_TASKS_MD || path.join(root, "TASKS.md")
    try {
      if (hasTasksMdPendingWork(tasksMd)) return true
    } catch {}
    const bugsMd = process.env.GLUDD_BUGS_MD || path.join(root, "BUGS.md")
    try {
      if (fs.existsSync(bugsMd)) {
        if (countOpenBugIncidents(fs.readFileSync(bugsMd, "utf8")) > 0) return true
      }
    } catch {}
    try {
      const gatePath = path.join(getProjectRoot(), ".gate-status")
      if (fs.existsSync(gatePath)) {
        const content = fs.readFileSync(gatePath, "utf8")
        if (/=== GATE:\s*FAILED/.test(content)) return true
        for (const line of content.split("\n")) {
          if (line.startsWith("===")) continue
          if (/FAIL/.test(line) || /RUNNING/i.test(line) || /incomplete/i.test(line)) return true
        }
      }
    } catch {}
    try {
      const gateLitePath = path.join(getProjectRoot(), ".gate-lite-status")
      if (fs.existsSync(gateLitePath)) {
        const content = fs.readFileSync(gateLitePath, "utf8")
        if (/=== GATE-LITE:\s*FAILED/.test(content)) return true
        for (const line of content.split("\n")) {
          if (line.startsWith("===")) continue
          if (/FAIL/.test(line)) return true
        }
      }
    } catch {}
    try {
      const releasePath = process.env.GLUDD_RELEASE_COMPLETENESS_FILE || "/tmp/gludd-release-completeness.json"
      if (fs.existsSync(releasePath)) {
        const rd = JSON.parse(fs.readFileSync(releasePath, "utf8"))
        if (Date.now() - (rd.ts || 0) < 300_000 && rd.incomplete) return true
      }
    } catch {}
    try {
      const ciCachePath = process.env.GLUDD_CI_CACHE_PATH || "/tmp/gludd-watchdog-ci.json"
      if (fs.existsSync(ciCachePath)) {
        const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
        const lastCheck = ciData.last_ci_check || 0
        const lastStatus = ciData.last_ci_status || ""
        if (Date.now() - lastCheck < 120_000 && lastStatus && lastStatus !== "SUCCESS") return true
      }
      const stopStatePath = process.env.GLUDD_STOP_STATE_PATH || "/tmp/gludd-stop-state.json"
      if (fs.existsSync(stopStatePath)) {
        const state = JSON.parse(fs.readFileSync(stopStatePath, "utf8"))
        if (state.ciVerdictPendingOrRed) return true
      }
    } catch {}
    try {
      if (options?.isCommitTool) {
        const unstaged = execSync("git diff --name-only", {
          cwd: getProjectRoot(), encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
        })
        if (unstaged.trim().length > 0) return true
      } else {
        const status = execSync("git status --porcelain", {
          cwd: getProjectRoot(), encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
        })
        if (status.trim().length > 0) return true
      }
    } catch {}
    return false
  } catch {
    return false
  }
}
// ── Dispatch command builder ───────────────────────────────────────────────
interface DispatchCommand { index: number; tool: string; task_item: string }
function _buildDispatchCommands(): DispatchCommand[] {
  const commands: DispatchCommand[] = []
  let idx = 1
  try {
    const tasksMd = process.env.GLUDD_TASKS_MD || path.join(getProjectRoot(), "TASKS.md")
    if (fs.existsSync(tasksMd)) {
      for (const line of fs.readFileSync(tasksMd, "utf8").split("\n")) {
        if (/^\s*[-*]\s+\[\s*\]/.test(line)) {
          const item = line.replace(/^\s*[-*]\s+\[\s*\]\s*/, "").trim().substring(0, 100)
          commands.push({ index: idx++, tool: "task", task_item: item })
        }
      }
    }
  } catch {}
  try {
    const ratchet = path.join(getProjectRoot(), "config", "ratchet.yml")
    if (fs.existsSync(ratchet)) {
      const count = fs.readFileSync(ratchet, "utf8")
        .split("\n")
        .filter(l => l.trim() && !l.trim().startsWith("#") && l.includes(":"))
        .length
      if (count > 0) {
        commands.push({ index: idx++, tool: "task", task_item: `ratchet: fix ${count} entries` })
      }
    }
  } catch {}
  try {
    const gs = path.join(getProjectRoot(), ".gate-status")
    if (fs.existsSync(gs)) {
      const content = fs.readFileSync(gs, "utf8")
      if (/FAIL/.test(content)) {
        commands.push({ index: idx++, tool: "task", task_item: "gate: red — fix failures" })
      }
    }
  } catch {}
  return commands
}

/** Persist an observable preflight before the first member of every wave. */
function recordDispatchPreflight(commands: DispatchCommand[]): void {
  try {
    writeJsonFile(DISPATCH_PREFLIGHT_PATH, {
      created_at: new Date().toISOString(),
      required_width: WAVE_WIDTH,
      planned_items: commands.slice(0, WAVE_WIDTH).map(command => command.task_item),
      status: "in_progress",
    })
  } catch {}
}

function recordDispatchWaveComplete(dispatches: number): void {
  try {
    const prior = readJsonFile(DISPATCH_PREFLIGHT_PATH) as Record<string, unknown> | null
    writeJsonFile(DISPATCH_PREFLIGHT_PATH, {
      ...(prior || {}),
      completed_at: new Date().toISOString(),
      observed_dispatches: dispatches,
      required_width: WAVE_WIDTH,
      status: "complete",
    })
  } catch {}
}
// ── Module-level state (persists across tool.execute.before calls) ─────────
const MAX_STREAK = 2
let _streakCount = 0
let _readStreak = 0
let _lastDispatchTs = Date.now()
let _dispatchCount = 0
let _dispatchPeak = 0
let _consecutiveReadsInResultPhase = 0
let _thisMessageDispatchCount = 0
let _thisMessageTotalCalls = 0
let _prevMessageDispatchCount = 0
let _sessionDispatchCount = 0
let _lastCallTs = 0
// PID-based staleness detection: tracks which process initialized the module-
// level state. If a different process (prior session / crashed plugin) owns
// the state, all counters are reset on the next hook call.
//
// PID-only detection fails when opencode reuses PIDs across restarts — the
// PID matches but the state is from a prior session. _floorSessionStartMtime
// guards against this: the session-start file is refreshed on every session
// boot, so if its mtime has advanced past the value recorded at init, the
// state is stale and must be reset.
let _floorInitPid = process.pid
let _floorSessionStartMtime = getSessionStartMtimeMs()
function _resetFloorState(): void {
  _streakCount = 0
  _readStreak = 0
  _lastDispatchTs = Date.now()
  _dispatchCount = 0
  _dispatchPeak = 0
  _consecutiveReadsInResultPhase = 0
  _thisMessageDispatchCount = 0
  _thisMessageTotalCalls = 0
  _prevMessageDispatchCount = 0
  _sessionDispatchCount = 0
  _lastCallTs = 0
  _floorInitPid = process.pid
  _floorSessionStartMtime = getSessionStartMtimeMs()
}
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
const COMPULSIVE_CHECK_RE = /^make\s+(git-log|ci-verdict|git-diff|gate-refresh)(\s|\/|$)/
// ── Block-message builders ─────────────────────────────────────────────────
function _buildFloorBreachBlock(streakCount: number, effectiveMax: number, commands: DispatchCommand[], displayFloor: number, displayTarget: number): string {
  const lines = [
    "",
    "█████████████████████████████████████████████████████████████████████████████",
    "█                                                                               █",
    "█  FLOOR BREACH                                                              █",
    "█  ⛔  AGENT-FLOOR BREACH — NON-DISPATCH CALL BLOCKED  ⛔                      █",
    "█                                                                               █",
    `█  ${streakCount} consecutive non-dispatch calls (MAX allowed: ${effectiveMax}).                      █`,
    `█  FLOOR = ${displayFloor} subagents.  TARGET = ${displayTarget}.  Current pool: BELOW FLOOR.                  █`,
    "█                                                                               █",
    "█  THE ONLY ALLOWED TOOL CALLS RIGHT NOW:                                       █",
    "█    → task   (launch a subagent on concrete work)                              █",
    "█    → agent  (launch a subagent on concrete work)                              █",
    "█                                                                               █",
    "█  DO NOT: read, write, edit, grep, glob, or bash.  DISPATCH SUBAGENTS.         █",
    "█                                                                               █",
    "█  Blocking mode is ON.  Set GLUDD_FLOOR_ENFORCE=0 to disable.            █",
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
  return lines.join("\n")
}
// ============================================================================
// DEFAULT IMPLEMENTATION (tool.execute.before only — self-contained)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, output: any) => {
    if (isSubagent()) return
    reportAlive("enforce-floor")
    writeHeartbeat("enforce-floor")
    if (_floorInitPid !== process.pid || getSessionStartMtimeMs() !== _floorSessionStartMtime) {
      _resetFloorState()
    }
    const eff = getEffectiveFloor()
    try {
      if (!FLOOR_ENFORCE) return
      const tool = (input?.tool ?? "") as string
      const now = Date.now()
      if (isDisengaged()) {
        _streakCount = 0
        _readStreak = 0
        _consecutiveReadsInResultPhase = 0
        return
      }
      // PRESSURE-RELEASE: skip ALL grinding blocks when in pressure-release
      // or inline-recovery mode. The agent needs inline tools to recover from
      // empty/failed dispatches. Streaks and read limits are bypassed;
      // only the wave-width ceiling (above) remains active to enforce the
      // concurrent-agent cap.
      const pressureRelief = isInPressureRelease() || isInInlineRecovery()
      updateSharedStreak(tool, STREAK_PLUGIN_NAME)
      // ── Message boundary detection (5s inter-call timeout) ───────────
      const isNewMessage = _lastCallTs > 0 && (now - _lastCallTs) > MESSAGE_BOUNDARY_MS
      if (isNewMessage) {
        _prevMessageDispatchCount = _thisMessageDispatchCount
        _thisMessageDispatchCount = 0
        _thisMessageTotalCalls = 0
      }
      _lastCallTs = now
      // ── Time-based result-processing phase detection ─────────────────
      const msSinceDispatch = now - _lastDispatchTs
      const inResultPhase = _dispatchCount > 0 && msSinceDispatch < POST_DISPATCH_GRACE_MS && msSinceDispatch > 2000
      if (isDispatchTool(tool) && _thisMessageDispatchCount >= eff.waveWidth && openWorkExists()) {
        return {
          permissionDecision: "deny" as const,
          message: [
            "⛔ WAVE WIDTH VIOLATION — DISPATCH BLOCKED",
            "",
            `This wave already contains ${_thisMessageDispatchCount} dispatches; the required width is ${eff.waveWidth}.`,
            "Do not exceed the configured concurrent-agent ceiling.",
          ].join("\n"),
        }
      }
      if (isDispatchTool(tool) && _thisMessageDispatchCount === 0) {
        recordDispatchPreflight(_buildDispatchCommands())
      }
      // ── Dispatch tool → reset streaks, count, return ─────────────────
      if (isDispatchTool(tool)) {
        _streakCount = 0
        _readStreak = 0
        _lastDispatchTs = now
        _dispatchCount++
        _thisMessageDispatchCount++
        _thisMessageTotalCalls++
        _sessionDispatchCount++
        if (_dispatchCount > _dispatchPeak) _dispatchPeak = _dispatchCount
        _consecutiveReadsInResultPhase = 0
        if (_thisMessageDispatchCount === eff.waveWidth) {
          recordDispatchWaveComplete(_thisMessageDispatchCount)
        }
        return
      }
      // A completed assistant message containing dispatches must be an exact
      // ten-wide wave before the main thread can resume inline activity.
      if (
        _prevMessageDispatchCount > 0 &&
        _prevMessageDispatchCount < eff.waveWidth &&
        openWorkExists()
      ) {
        return {
          permissionDecision: "deny" as const,
          message: [
            "⛔ WAVE WIDTH VIOLATION — INLINE WORK BLOCKED",
            "",
            `Previous message dispatched ${_prevMessageDispatchCount}; required wave width is ${eff.waveWidth}.`,
            "Run the pre-dispatch audit and submit one parallel wave of exactly 10 concrete tasks.",
            "Do not resume reads, edits, or bash calls after an undersized wave.",
          ].join("\n"),
        }
      }
      // ── Session-start dispatch stall ─────────────────────────────────
      if (_isInSessionStartWindow() && _sessionDispatchCount === 0) {
        const sst = _getSessionStartTs()
        if ((now - sst) > SESSION_START_TIME_BLOCK_MS && openWorkExists()) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "⛔  SESSION-START DISPATCH STALL — TOOL CALL BLOCKED",
              "",
              `${Math.round((now - sst) / 1000)}s elapsed since session start with ZERO dispatches.`,
              "The agent read task files but has not dispatched any subagents.",
              "All non-dispatch tool calls are blocked until a dispatch wave starts.",
              "",
              "REQUIRED: Dispatch task/agent subagents on pending work NOW.",
              "No more reads, edits, or status probes.  DISPATCH FIRST.",
            ].join("\n"),
          }
        }
      }
      // ── Read-tool handling ───────────────────────────────────────────
      if (isReadTool(tool)) {
        _thisMessageTotalCalls++
        _readStreak++
        // PRESSURE-RELEASE: skip all read-grinding and result-phase blocks.
        // The agent needs to read files to recover from empty/failed dispatches.
        if (pressureRelief) return
        // Session-start read grinding
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
        // Result-phase read limit
        if (inResultPhase) {
          _consecutiveReadsInResultPhase++
          if (_consecutiveReadsInResultPhase > RESULT_PHASE_READ_LIMIT) {
            return {
              permissionDecision: "deny" as const,
              message: [
                "⛔  POST-RESULT READ LIMIT EXCEEDED — READ BLOCKED",
                "",
                `${_consecutiveReadsInResultPhase} consecutive reads in result-processing phase.`,
                `Maximum ${RESULT_PHASE_READ_LIMIT} reads allowed before dispatching next wave.`,
                "File inspection between result waves is the dispatch-gap anti-pattern.",
                "",
                "REQUIRED: Dispatch task/agent subagents on pending work NOW.",
              ].join("\n"),
            }
          }
        }
        // Read-grinding (15+ reads, >60s since dispatch)
        if (_readStreak > 15 && msSinceDispatch > 60_000 && !inResultPhase) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "READ-GRINDING DETECTED — READ BLOCKED",
              "",
              `${_readStreak} consecutive reads with no dispatch, ${Math.round(msSinceDispatch / 1000)}s since last dispatch.`,
              "An agent doing 15+ serial reads over 1+ minute without dispatching",
              "is grinding inline instead of delegating. DISPATCH WORK.",
              "",
              "REQUIRED: Dispatch task/agent subagents on pending work.",
            ].join("\n"),
          }
        }
        // Read-grinding warn (8+ reads, >30s)
        if (_readStreak > 8 && msSinceDispatch > 30_000 && !inResultPhase) {
          console.warn(
            `READ-GRINDING DETECTED: ${_readStreak} consecutive reads, ` +
            `${Math.round(msSinceDispatch / 1000)}s since last dispatch. DISPATCH WORK.`
          )
        }
        return
      }
      // ── Bash/edit/write (non-read, non-dispatch) ─────────────────────
      _thisMessageTotalCalls++
      // Compulsive-check block
      let commitToolMode = false
      if (tool === "bash") {
        const outArgs = (output as Record<string, unknown> | undefined)?.args as { command?: string } | undefined
        const cmd = typeof outArgs?.command === "string" ? outArgs.command.trim() : ""
        commitToolMode = isCommitBashCommand(cmd)
        if (commitToolMode) {
          recordMissedCommit()
          maybeRemindMissedCommitDispatch()
        }
        if (COMPULSIVE_CHECK_RE.test(cmd) && openWorkExists()) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "⛔ COMPULSIVE-CHECK LOOP BLOCKED",
              `Command: ${cmd}`,
              "",
              "make git-log / make ci-verdict / make git-diff / make gate-refresh as a standalone",
              "bash call is the compulsive-check loop pattern. If you are reaching",
              "for one of these, you are in the loop.",
              "",
              "REQUIRED: Dispatch the check to a Task/agent subagent instead.",
              "The main thread must only dispatch, not run status probes.",
              "",
              "BREAK THE LOOP: dispatch ≥2 subagents on pending work now.",
            ].join("\n"),
          }
        }
      }
      // Re-check disengage (may have changed during bash processing)
      if (isDisengaged()) {
        _streakCount = 0
        return
      }
      // Message-shape enforcement (prev message had exactly 1 dispatch)
      if (_prevMessageDispatchCount === 1 && openWorkExists({ isCommitTool: commitToolMode })) {
        return {
          permissionDecision: "deny" as const,
          message: [
            "⛔ MESSAGE-SHAPE VIOLATION — MUST DISPATCH ≥2 PER WAVE",
            "",
            "Previous message dispatched only 1 subagent.",
            "The message-shape rule (AGENTS.md) requires ZERO or ≥2 dispatches per",
            "agent response when work exists. 1 dispatch is the dribbling anti-pattern.",
            "",
            "Your next message MUST contain ≥2 parallel task/agent dispatches.",
            "Do not proceed with serial tool calls.  BATCH YOUR DISPATCHES.",
            "",
            "CORRECT: send one message with 2+ Task tool calls in parallel.",
            "INCORRECT: send one dispatch, wait, send another.",
          ].join("\n"),
        }
      }
      // Post-dispatch result-phase grace: block mutations, allow reads
      if (inResultPhase && _consecutiveReadsInResultPhase <= RESULT_PHASE_READ_LIMIT) {
        _streakCount = 0
        if (tool === "bash" || tool === "edit" || tool === "write") {
          return {
            permissionDecision: "deny" as const,
            message: [
              "⛔  DISPATCH GAP — INLINE MUTATION BLOCKED",
              "",
              "Subagent results appear to have arrived. You are trying to mutate files",
              `(tool: ${tool}) instead of dispatching the next wave.`,
              "This creates a dispatch gap where the subagent floor drains.",
              "",
              "ALLOWED now:  task/agent dispatches + read/grep/glob",
              "BLOCKED now:  bash, edit, write (inline work between waves is a bug)",
              "",
              "DISPATCH FIRST.  Inline mutation between waves is the gap.",
            ].join("\n"),
          }
        }
        return
      }
      // Re-check disengage
      if (isDisengaged()) {
        _streakCount = 0
        return
      }
      // ── Streak increment + floor breach ──────────────────────────────
      // PRESSURE-RELEASE: skip floor breach entirely. The agent needs
      // inline mutations to recover from empty/failed dispatches.
      if (pressureRelief) {
        _streakCount = 0
        return
      }
      _streakCount++
      const effectiveMax = _isInSessionStartWindow() ? SESSION_START_STREAK_MAX : MAX_STREAK
      // FLOOR BREACH is gated by _streakCount > MAX_STREAK in normal sessions.
      if (_streakCount <= effectiveMax) {
        // Refill-needed nudge (console only, not a block)
        if (_streakCount > 0 && msSinceDispatch > 15000 && _dispatchPeak >= 5 && openWorkExists()) {
          console.warn(
            `REFILL NEEDED: ${_streakCount} non-dispatch calls since last dispatch ` +
            `${Math.round(msSinceDispatch / 1000)}s ago. Dispatch peak was ${_dispatchPeak}. ` +
            "DISPATCH ≥2 subagents now."
          )
        }
        return
      }
      if (!openWorkExists({ isCommitTool: commitToolMode })) {
        _streakCount = 0
        return
      }
      const commands = _buildDispatchCommands()
      return {
        permissionDecision: "deny" as const,
        message: _buildFloorBreachBlock(_streakCount, effectiveMax, commands, eff.floor, eff.target),
      }
    } catch {
      return
    }
  },
}
// ============================================================================
// PROXY PLUGIN (hot-reload aware — tool.execute.before only)
// ============================================================================
export default (({ }) => {
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-floor ` +
      `tool.execute.before (self-contained; no text.complete) ` +
      `pid=${process.pid}\n`,
      "utf8",
    )
  } catch {}
  // Stale-state cleanup
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
  } catch {}
  return {
    "tool.execute.before": async (input: any, output: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("floor", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
    // opencode 1.17.9 only registers the experimental key. Declared as a
    // pass-through so the pinned plugin contract (supported-hooks surface)
    // holds while the actual enforcement stays self-contained in
    // tool.execute.before.
    "experimental.text.complete": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output
      const impl = loadHotModule("floor", defaultImpl)
      const fn = impl["text.complete"] || impl["experimental.text.complete"]
      return fn ? await fn(_input, output) : output
    },
  }
}) satisfies Plugin
