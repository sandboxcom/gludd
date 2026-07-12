import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

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

// Live overrides (parallel to the shell hooks): a valid integer in
// /tmp/gludd-floor-override / /tmp/gludd-ceiling-override wins over the env
// default, so the operator can retune floor/ceiling mid-session without a
// restart. FAIL-OPEN: unreadable/non-numeric file -> keep the env default.
function _tunable(overridePath: string, envVar: string, dflt: string): number {
  let base = parseInt(process.env[envVar] || dflt, 10)
  try {
    const raw = fs.readFileSync(overridePath, "utf8").trim()
    if (/^\d+$/.test(raw)) base = parseInt(raw, 10)
  } catch { /* no override file -> env/default */ }
  return base
}
const FLOOR = _tunable("/tmp/gludd-floor-override", "CLAUDE_AGENT_FLOOR", "7")
const CEILING = _tunable("/tmp/gludd-ceiling-override", "CLAUDE_AGENT_CEILING", "10")
const TARGET = Math.min(
  parseInt(process.env.CLAUDE_AGENT_TARGET || "6", 10),
  CEILING,
)

// BLOCKING mode (2026-06-28 user directive): the floor breach was advisory-only
// by default, which let the agent serialize work. Per AGENTS.md "Guardrail
// Integrity Policy" the fix is to make the guardrail SMARTER (block when
// warranted), not delete it. The plugin BLOCKS non-dispatch tool calls (via
// tool.execute.before) when the floor is breached AND there is known open work
// — forcing the agent to dispatch subagents rather than grind inline.
// HARD DEFAULT — ALWAYS ON. This guardrail exists because past sessions repeatedly
// demonstrated the agent collapsing to serial inline grinding with 0 subagents live.
// It is NOT opt-in and does NOT depend on environment variables. The only way to
// disable is to edit this source file directly.
// PREVIOUS BUG (2026-07-05): `process.env.GLUDD_FLOOR_ENFORCE !== "0"` was default-ON
// but .claude/settings.json explicitly set GLUDD_FLOOR_ENFORCE=0, silently disabling
// the guardrail for ALL sessions. Now unconditionally true.
const FLOOR_ENFORCE = true

// Dispatch tools are ALWAYS allowed — even when the floor is breached and
// enforce mode is on. Blocking a dispatch attempt would be counterproductive
// (the whole point is to force MORE dispatches). This helper is the load-bearing
// exemption that keeps the block from wedging the session.
function isDispatchTool(tool: string): boolean {
  return tool === "task" || tool === "agent" || tool === "workflow"
}

function isReadTool(toolName: string): boolean {
  return toolName === "read" || toolName === "grep" || toolName === "glob"
}

// ── SHARED STREAK STATE (P3: cross-call grinding detection) ────────────────
// Shared between enforce-floor.ts and enforce-stop.ts so EITHER plugin can
// catch main-thread grinding (serial read/edit/bash with no dispatch). The
// dedup window prevents double-counting when both plugins fire on the same
// tool.execute.before event.
const SHARED_STREAK_FILE = process.env.GLUDD_STREAK_FILE || "/tmp/gludd-tool-streak.json"
const STREAK_PLUGIN_NAME = "enforce-floor"
const STREAK_DEDUP_WINDOW_MS = 500

interface SharedStreakState {
  streak: number
  lastDispatchTs: number
  readStreak: number
  editStreak: number
  lastUpdateTs: number
  lastWriter: string
  pid: number
}

function readSharedStreak(): SharedStreakState {
  try {
    if (fs.existsSync(SHARED_STREAK_FILE)) {
      const raw = JSON.parse(fs.readFileSync(SHARED_STREAK_FILE, "utf8"))
      const now = Date.now()
      const lastTs = typeof raw.lastUpdateTs === "number" ? raw.lastUpdateTs : 0
      const STALE_MS = 60_000
      if (lastTs > 0 && now - lastTs > STALE_MS) {
        const zeroed = { streak: 0, lastDispatchTs: 0, readStreak: 0, editStreak: 0, lastUpdateTs: now, lastWriter: "stale-reset", pid: process.pid }
        try { fs.writeFileSync(SHARED_STREAK_FILE, JSON.stringify(zeroed), "utf8") } catch {}
        return zeroed
      }
      // PID-based cross-session guard: if the stored pid exists and does not
      // match process.pid, the file was written by a DIFFERENT opencode session.
      // Reset to 0 — each session owns its own streak; cross-session pollution
      // is the bug this check prevents. If the old pid matches process.pid, the
      // state is from this same session and the streak is valid.
      const storedPid = typeof raw.pid === "number" ? raw.pid : 0
      if (storedPid > 0 && storedPid !== process.pid) {
        const zeroed = { streak: 0, lastDispatchTs: 0, readStreak: 0, editStreak: 0, lastUpdateTs: now, lastWriter: "pid-reset", pid: process.pid }
        try { fs.writeFileSync(SHARED_STREAK_FILE, JSON.stringify(zeroed), "utf8") } catch {}
        return zeroed
      }
      return {
        streak: typeof raw.streak === "number" ? raw.streak : 0,
        lastDispatchTs: typeof raw.lastDispatchTs === "number" ? raw.lastDispatchTs : 0,
        readStreak: typeof raw.readStreak === "number" ? raw.readStreak : 0,
        editStreak: typeof raw.editStreak === "number" ? raw.editStreak : 0,
        lastUpdateTs: lastTs,
        lastWriter: typeof raw.lastWriter === "string" ? raw.lastWriter : "",
        pid: storedPid || process.pid,
      }
    }
  } catch {}
  return { streak: 0, lastDispatchTs: 0, readStreak: 0, editStreak: 0, lastUpdateTs: 0, lastWriter: "", pid: 0 }
}

function writeSharedStreak(s: SharedStreakState): void {
  s.pid = process.pid
  try { fs.writeFileSync(SHARED_STREAK_FILE, JSON.stringify(s), "utf8") } catch {}
}

// Update the shared streak for a tool call. Dedupes if the other plugin
// already counted this exact call (within DEDUP_WINDOW_MS). Returns the
// updated state so callers can check thresholds if needed.
function updateSharedStreak(tool: string): SharedStreakState {
  const s = readSharedStreak()
  const now = Date.now()
  const isDispatch = tool === "task" || tool === "agent" || tool === "workflow"
  const isRead = tool === "read" || tool === "grep" || tool === "glob"
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

function isCommitBashCommand(cmd: string): boolean {
  return /^make\s+(git-commit|commit-no-verify|git-commit-file|test-and-commit|repo-commit|feature-done|git-merge)(\s|$)/.test(cmd)
}

// Open-work probe: only block when the repo actually has pending work. Avoids
// wedging a session where the floor is breached because the work is genuinely
// done. Signals (any one triggers "open work"):
//   - ratchet.yml entries
//   - the multitasking backlog file present
//   - uncommitted git changes
//   - a live todowrite list with pending/in_progress items (state file mirror
//     at /tmp/gludd-todowrite-state.json written by enforce-todos.ts)
//   - unchecked markdown task rows in TASKS.md (`- [ ]`, `* [ ]`)
//   - open incident headers in BUGS.md
// The TASKS.md/BUGS.md scans close the gap where the agent has unchecked
// task-ledger rows but a clean git tree, so the floor gate no longer silently
// disables. Paths are configurable via GLUDD_TASKS_MD / GLUDD_BUGS_MD env vars
// (default <cwd>/TASKS.md / <cwd>/BUGS.md).
// FAIL-OPEN: any error -> false (don't block on a probe bug).
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
    // Todowrite state mirror — written by enforce-todos.ts whenever the agent
    // carries pending/in_progress items. Best-effort: absent file => no signal.
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
    } catch { /* malformed mirror -> ignore */ }
    // TASKS.md scan — count unchecked markdown task rows (`- [ ]` or `* [ ]`).
    // Configurable via GLUDD_TASKS_MD (default <cwd>/TASKS.md).
    const tasksMd = process.env.GLUDD_TASKS_MD || path.join(process.cwd(), "TASKS.md")
    try {
      if (fs.existsSync(tasksMd)) {
        const tasksSrc = fs.readFileSync(tasksMd, "utf8")
        // Match `^\s*[-*]\s+\[\s*\]` — a list marker followed by an empty
        // (unchecked) markdown task box. `- [x]` / `- [X]` do NOT match.
        const unchecked = tasksSrc
          .split("\n")
          .filter(l => /^\s*[-*]\s+\[\s*\]/.test(l))
        if (unchecked.length > 0) return true
      }
    } catch { /* unreadable TASKS.md -> ignore */ }
    // BUGS.md scan — count open incident headers (date-stamped `### YYYY-MM-DD —`
    // rows that are not marked resolved). Configurable via GLUDD_BUGS_MD.
    // Matches both em-dash (— U+2014) and hyphen (- U+002D) separators.
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
    } catch { /* unreadable BUGS.md -> ignore */ }
    // Gate status check — .gate-status exists and has FAIL lines
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
    // CI status check — read from watchdog cache or shared state
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
    // Check for uncommitted changes via .git/index vs .git/refs/heads mtime
    // — avoids the execSync("git status") dependency that may not work in
    // plugin sandboxes where `git` is not on PATH.
    // When isCommitTool: staged changes are the commit payload — skip mtime check.
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
    // BUG #21 fix: FALLBACK — run git status to catch dirty state
    // that mtime-based check misses (e.g., staged files, rebase-in-progress).
    // When isCommitTool: use `git diff --name-only` (unstaged only) because
    // staged files ARE the commit payload.
    try {
      const { execSync } = require("node:child_process")
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
    } catch { /* git not available, fallback silently fails */ }
    return false
  } catch {
    return false
  }
}

// =============================================================================
// Dispatch-lifecycle awareness (2026-07-05 — fix the "refill gap")
// =============================================================================
//
// The old streak-only model had zero awareness of the dispatch/result lifecycle:
// it treated every non-dispatch call identically, even during legitimate result
// processing or the critical refill moment after a batch drained.  This led to
// blocks at exactly the wrong time — when the agent needed to READ results and
// DISPATCH the next wave.  The block *caused* the floor breach it was trying to
// prevent.
//
// Lifecycle phases tracked:
//   (1) DISPATCH — agent sends task/agent/workflow calls → _dispatchCount ↑
//   (2) WAIT     — subagents run; agent may do read-only operations
//   (3) RESULTS  — model text contains "task result"/"completed" markers
//   (4) PROCESS  — agent reads files, digests output → result-processing grace
//   (5) REFILL   — _dispatchCount fell from peak → agent MUST dispatch again
//
// New state variables and their roles:
//   _dispatchCount         — incremented on each dispatch; heuristically
//                            decremented when result-markers appear in model text.
//   _dispatchPeak          — the highest _dispatchCount seen in the current
//                            cycle.  Reset to current count when a new dispatch
//                            wave starts.
//   _resultProcessingGrace — number of non-dispatch tool calls the agent gets
//                            AFTER results arrive, before the streak counter
//                            restarts.  Prevents blocks during legitimate
//                            result digestion (reading files, inspecting output).
//   _needsRefill           — true when _dispatchPeak was ≥ PEAK_DISPATCH but
//                            _dispatchCount has fallen below REFILL_THRESHOLD.
//                            When set, the block is DISABLED: the agent NEEDS
//                            non-dispatch calls (reads, file scans) to prepare
//                            the next dispatch wave.  Blocking here is the
//                            "refill gap" bug.
//
// Refill safety: when _needsRefill is true, the agent needs the freedom to
// survey results and dispatch — blocking non-dispatch tools would wedge the
// session.  A new dispatch resets _needsRefill to false.

const MAX_STREAK = 2

// ── Session-start window enforcement (2026-07-12) ────────────────────────
// Reads the session start timestamp from enforce-session-start.ts's shared
// state file.  During the first 90s after session start, all thresholds are
// tightened to force dispatch within the first few turns.
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

// Read-grinding detection (2026-07-09 — multitasking audit P1 fix).
// The old isReadTool() early-return exempted read/grep/glob from ALL streak
// counters, so an agent could do 100 serial reads with zero dispatches and
// no plugin caught it — the primary mechanical failure of the floor guard.
// This SEPARATE counter tracks reads independently of the edit/write/bash
// streak, with TIME-BASED detection so a legitimate investigation burst
// (many reads in a few seconds, right after a dispatch) is NOT blocked.
//   ADVISORY: >5 reads AND >30s since last dispatch -> console.warn
//   DENY:     >10 reads AND >60s since last dispatch -> permissionDecision:deny
// Reset to 0 on any dispatch (isDispatchTool branch below).
let _readStreak = 0
let _lastDispatchTs = Date.now()

let _dispatchCount = 0
let _dispatchPeak = 0
let _resultProcessingGrace = 0
let _needsRefill = false

// Gap 1: ANTI-LOOP — compulsive-check targets that must be dispatched via Task tool
const COMPULSIVE_CHECK_RE = /^make\s+(git-log|ci-verdict|git-diff)(\s|\/|$)/

// Gap 2: Message-shape dispatch tracking — counts dispatches per agent message
// so we can enforce the 5+ dispatch-per-wave rule.  _thisMessage* accumulates
// during the current message; on text.complete it is promoted to _prevMessage*
// and reset.  The next non-dispatch call is blocked if _prevMessageDispatchCount
// is 1–4 and open work exists.
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
const RESULT_GRACE_CALLS = 2  // BUG #18 fix: was 0 (dead)

function _textHasResultMarker(text: string): boolean {
  const lower = text.toLowerCase()
  return RESULT_MARKERS.some(m => lower.includes(m))
}

function _updateRefillState(): void {
  _needsRefill = _dispatchPeak >= PEAK_DISPATCH && _dispatchCount < REFILL_THRESHOLD
}

// Dispatch-command builder: extract specific tasks from TASKS.md unchecked items,
// config/ratchet.yml entries, and .gate-status FAIL lines.  Produces numbered
// commands the agent sees in the deny message — mechanical, not advisory.
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

function _reportAlive(): void {
  try {
    const alive: Record<string, any> = {}
    try { if (fs.existsSync("/tmp/gludd-plugin-alive.json")) { const d = JSON.parse(fs.readFileSync("/tmp/gludd-plugin-alive.json", "utf8")); if (typeof d === "object" && d !== null) Object.assign(alive, d) } } catch {}
    alive["enforce-floor"] = { last_seen: Date.now() }
    fs.writeFileSync("/tmp/gludd-plugin-alive.json", JSON.stringify(alive), "utf8")
  } catch {}
}

// Per-plugin heartbeat — runtime evidence that tool.execute.before ACTUALLY
// fires (not just that the module was imported). Fail-open: a write failure
// must never block the hook. Distinct from the shared alive.json so one
// plugin's bad write cannot mask another's silence.
function _writeHeartbeat(): void {
  try {
    const hb = JSON.stringify({ plugin: "enforce-floor", ts: Date.now(), pid: process.pid })
    fs.writeFileSync("/tmp/gludd-plugin-heartbeat-enforce-floor.json", hb)
  } catch { /* fail-open */ }
}

export default (async ({ }) => {
  // LOADED self-check: proves opencode actually invoked the factory (i.e. the
  // plugin is registered, not merely present on disk). Appended so all three
  // plugins share the log file.
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-floor ` +
      `tool.execute.before+session.idle+experimental.text.complete ` +
      `pid=${process.pid}\n`,
      "utf8",
    )
  } catch { /* fail-open */ }

  // Stale-state cleanup (P3 fix, 2026-07-12): when opcode restarts and reloads
  // plugins, enforce-delegate.ts's read-grind counter in /tmp/gludd-read-grind.json
  // persists from the prior process. If the lastDispatchTs or ts is older than 60s,
  // the counter is stale and must be reset — an agent in a fresh session should not
  // start with a grinding-detection counter inherited from the previous run.
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
    "tool.execute.before": async (input: { tool?: string }, output: unknown) => {
      if (process.env.OPENCODE_SUBAGENT === "1") return
      _reportAlive()
      _writeHeartbeat()
      try {
        if (!FLOOR_ENFORCE) return
        const tool = (input?.tool ?? "") as string

        // Disengage check: when the operator has explicitly disengaged
        // enforcement, skip ALL blocks including read-grind. Must be hoisted
        // ABOVE the read-grind detection so disengage-enforcement actually works.
        let disengagedEarly = false
        try {
          const disPath = "/tmp/gludd-watchdog-disengage.json"
          if (fs.existsSync(disPath)) {
            const d = JSON.parse(fs.readFileSync(disPath, "utf8"))
            if (d.disengage_until) {
              const now = Date.now()
              const effective = Math.min(d.disengage_until, now + 3_600_000)
              if (effective > now) disengagedEarly = true
            }
          }
        } catch {}
        if (disengagedEarly) {
          _streakCount = 0
          _readStreak = 0
          return
        }

        // P3: Update the shared cross-plugin streak counter so enforce-stop.ts
        // can detect grinding even when this plugin's in-memory streak is out
        // of sync or its hook fails open. Called for ALL tools (dispatch resets,
        // reads + mutations increment).
        updateSharedStreak(tool)

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
          return
        }

        // Session-start window: if >60s elapsed since session start with
        // 0 dispatches, block ALL non-dispatch tool calls.  The agent had
        // time to read task files and should be dispatching by now.
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
          // Session-start window: tighter read-grind thresholds.
          // After 3 reads → warn; after 6 reads → deny.
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
          // Hard-deny: 10+ reads AND >60s since last dispatch — clear grinding.
          // An agent doing 10+ serial reads over 1+ minute without dispatching
          // is grinding inline instead of delegating. Both conditions must hold
          // (AND) so a legitimate fast investigation burst is never blocked.
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
          // Advisory: 5+ reads AND >30s since last dispatch — warning nudge.
          // Same AND logic: a short investigation burst must NOT trigger this.
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

        // Gap 1: ANTI-LOOP — block standalone compulsive-check make targets
        // (git-log, ci-verdict, git-diff). These never produce productive
        // output; they are the looping pattern. The agent MUST dispatch via
        // Task tool instead.
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

        // Disengage check: when the operator has explicitly disengaged
        // enforcement, skip ALL blocks including message-shape. Hoisted
        // above message-shape to prevent the gap where disengage-enforcement
        // is silently ignored when prev message had 1-4 dispatches.
        // BUG #23 fix: clamp disengage to max 1 hour from now
        let disengagedForFloor = false
        try {
          const disPath = "/tmp/gludd-watchdog-disengage.json"
          if (fs.existsSync(disPath)) {
            const d = JSON.parse(fs.readFileSync(disPath, "utf8"))
            if (d.disengage_until) {
              const now = Date.now()
              const MAX_FLOOR_DISENGAGE = now + 3_600_000
              const effective = Math.min(d.disengage_until, MAX_FLOOR_DISENGAGE)
              if (effective > now) disengagedForFloor = true
            }
          }
        } catch {}
        if (disengagedForFloor) {
          _streakCount = 0
          return
        }

        // Gap 2: Message-shape enforcement — after a message that dispatched
        // only 1–4 subagents, the very next non-dispatch tool call is blocked.
        // This forces the agent to batch 5+ dispatches per wave rather than
        // dribbling 1–3 at a time.  Reset when the agent sends a 0-dispatch or
        // 5+-dispatch response (set on text.complete boundary).
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

        // Result-processing grace: after results arrive the agent gets a brief
        // window to digest output. With RESULT_GRACE_CALLS=0 (hard default),
        // this fires once then the streak counter resumes immediately.
        if (_resultProcessingGrace > 0) {
          _resultProcessingGrace--
          _streakCount = 0
          return
        }

        // Disengage check: when the operator has explicitly disengaged
        // enforcement, skip the floor-breach block so commits and pushes
        // can proceed.
        // BUG #23 fix: clamp disengage to max 1 hour from now
        let disengaged = false
        try {
          const disPath = "/tmp/gludd-watchdog-disengage.json"
          if (fs.existsSync(disPath)) {
            const d = JSON.parse(fs.readFileSync(disPath, "utf8"))
            if (d.disengage_until) {
              const now = Date.now()
              const MAX_FLOOR_DISENGAGE_2 = now + 3_600_000
              const effective = Math.min(d.disengage_until, MAX_FLOOR_DISENGAGE_2)
              if (effective > now) disengaged = true
            }
          }
        } catch {}
        if (disengaged) {
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

        // Build SPECIFIC dispatch commands from TASKS.md unchecked items,
        // ratchet entries, and gate status — so the agent sees EXACTLY what
        // to dispatch on, not a generic "do work" nudge.
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
          "█  Blocking mode is ALWAYS ON.  There is NO env-var escape hatch.               █",
          "█  To permanently disable, edit enforce-floor.ts directly.                      █",
          "█                                                                               █",
        ]
        const sep = "█" + (" ".repeat(79))
        lines.push(sep)
        lines.push("█  == SPECIFIC DISPATCH COMMANDS ==" + (" ".repeat(44)))
        lines.push(sep)
        if (commands.length === 0) {
          lines.push("█  (no TASKS.md/ratchet/gate items found — dispatch RESEARCH tasks)" + " ".repeat(9))
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

    "session.idle": async () => {
      floorTurnState.accumulatedText = ""
      // Reset dispatch-lifecycle state on idle: a new turn starts fresh.
      // _dispatchCount and _dispatchPeak carry forward (subagents launched
      // last turn may still be running), but the grace window and refill
      // flag decay so a new turn doesn't inherit a stale pass.
      _resultProcessingGrace = 0
      _readStreak = 0
      _updateRefillState()
      // Gap 2: message-shape tracking — reset per-message counters on idle
      _thisMessageDispatchCount = 0
      _thisMessageTotalCalls = 0
      _prevMessageDispatchCount = 0
    },

    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      if (process.env.OPENCODE_SUBAGENT === "1") return output
      try {
        // Increment fire counter — proves text.complete actually fires
        try {
          const cPath = process.env.GLUDD_FLOOR_TEXT_COMPLETE_COUNT || "/tmp/gludd-floor-text-complete-count.json"
          let count = 1
          if (fs.existsSync(cPath)) {
            try { const d = JSON.parse(fs.readFileSync(cPath, "utf8")); count = (parseInt(d.count, 10) || 0) + 1 } catch {}
          }
          fs.writeFileSync(cPath, JSON.stringify({ count, last_fired: new Date().toISOString(), ts: Date.now() }), "utf8")
        } catch {}

        // Gap 2: Promote this message's dispatch count to the "previous" slot
        // so the NEXT tool.execute.before can check it.  If the current message
        // dispatched 0 or ≥5, the block lifts; if 1–4, the next non-dispatch
        // tool call will be denied with the message-shape violation directive.
        _prevMessageDispatchCount = _thisMessageDispatchCount
        _thisMessageDispatchCount = 0
        _thisMessageTotalCalls = 0

        if (!output || typeof output.text !== "string") return output
        floorTurnState.accumulatedText += output.text

        // Detect result arrival: when the model's output mentions subagent
        // results (e.g. "Task result: ...", "completed"), a batch of work
        // has finished.  Heuristically decrement _dispatchCount and grant
        // a grace window so the agent can digest results without the streak
        // counter blocking file reads / output inspection.
        if (_textHasResultMarker(output.text) && _resultProcessingGrace === 0) {
          // Heuristic: each detection represents roughly 2 agents returning.
          // Single detection → -2; avoids over-decrementing from multi-line
          // result text that trips the marker multiple times.
          _dispatchCount = Math.max(0, _dispatchCount - 2)
          _resultProcessingGrace = RESULT_GRACE_CALLS
          _streakCount = 0
        }

        _updateRefillState()

        // Session-start window nudge: if >60s elapsed with 0 dispatches,
        // inject a hard directive into the agent's response text.
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

        // BUG #19 fix: when _needsRefill is set, inject a refill directive
        // instead of a hard block. This tells the agent to dispatch more
        // subagents without replacing their entire response.
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
          // REPLACE the agent's text entirely — do NOT append.  When the floor
          // is breached, the agent's only valid output is a dispatch tool call,
          // not prose.  Appending a nag that sits below the agent's summary is
          // invisible in practice; the agent ignores it.  Replacing the text
          // makes it structurally impossible for prose to reach the user while
          // the floor is breached.
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
}) satisfies Plugin
