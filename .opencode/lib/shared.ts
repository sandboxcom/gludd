import * as fs from "node:fs"
import * as path from "node:path"

// ── Shared helpers for enforcement plugins ────────────────────────────────
// Extracted from 14 enforce-*.ts plugins (2026-07-13, E.5 refactor).
// Eliminates duplicated _isSubagent, disengage, JSON state, and heartbeat
// patterns. Each plugin imports what it needs instead of copy-pasting.

// ── Paths ─────────────────────────────────────────────────────────────────

export const DISENGAGE_PATH =
  process.env.GLUDD_DISENGAGE_PATH || "/tmp/gludd-watchdog-disengage.json"

export const DISENGAGE_AUDIT_PATH =
  process.env.GLUDD_DISENGAGE_AUDIT_PATH || "/tmp/gludd-disengage-audit.jsonl"

export const ALIVE_PATH =
  process.env.GLUDD_ALIVE_PATH || "/tmp/gludd-plugin-alive.json"

export const SUBAGENT_MARKER = (pid: number) =>
  `/tmp/gludd-subagent-${pid}.json`

// ── Subagent guard ────────────────────────────────────────────────────────
// Every plugin must skip enforcement inside a subagent context.

export function isSubagent(): boolean {
  if (process.env.OPENCODE_SUBAGENT === "1") return true
  try {
    return fs.existsSync(SUBAGENT_MARKER(process.pid))
  } catch {
    return false
  }
}

// ── Disengage check ───────────────────────────────────────────────────────
// Reads the watchdog disengage file and returns true when a valid (non-
// expired, clamped) disengage_until timestamp is in effect.

export interface DisengageOpts {
  maxMs?: number // maximum forward duration (default 3_600_000 = 1 hour)
}

const _sessionUuid = `${process.pid}-${Math.floor(Date.now() / 1000)}`

export function isDisengaged(opts: DisengageOpts = {}): boolean {
  const maxMs = opts.maxMs ?? 300_000
  try {
    if (!fs.existsSync(DISENGAGE_PATH)) return false
    const d = JSON.parse(fs.readFileSync(DISENGAGE_PATH, "utf8"))

    // Single-use disengage (make disengage-next): arms for ONE tool call,
    // then the file is deleted so the next operation re-arms enforcement.
    if (d.expires === 1) {
      try {
        fs.unlinkSync(DISENGAGE_PATH)
      } catch { /* fail-open */ }
      try {
        const audit = JSON.stringify({ ts: Date.now(), pid: process.pid, sessionUuid: _sessionUuid, single: true }) + "\n"
        fs.appendFileSync(DISENGAGE_AUDIT_PATH, audit, "utf8")
      } catch { /* fail-open */ }
      return true
    }

    if (typeof d.disengage_until !== "number") return false
    const now = Date.now()
    const effective = Math.min(d.disengage_until, now + maxMs)
    if (effective > now) {
      try {
        const audit = JSON.stringify({ ts: now, pid: process.pid, sessionUuid: _sessionUuid }) + "\n"
        fs.appendFileSync(DISENGAGE_AUDIT_PATH, audit, "utf8")
      } catch { /* fail-open */ }
      return true
    }
    return false
  } catch {
    return false
  }
}

// ── JSON state file helpers ───────────────────────────────────────────────
// Safe read/write with fail-open semantics. Never throw — return defaults.

export function readJsonFile<T>(filePath: string, defaultVal: T): T {
  try {
    if (fs.existsSync(filePath)) {
      return JSON.parse(fs.readFileSync(filePath, "utf8")) as T
    }
  } catch { /* corrupt / unreadable → default */ }
  return defaultVal
}

export function writeJsonFile(filePath: string, data: unknown): void {
  try {
    const tmp = `${filePath}.tmp.${process.pid}`
    fs.writeFileSync(tmp, JSON.stringify(data), "utf8")
    fs.renameSync(tmp, filePath)
  } catch { /* permission / disk-full → silently skip */ }
}

// ── Tool classification helpers ────────────────────────────────────────────
// Canonical definitions for dispatch-tool and read-tool classification.
// Eliminates 1-3 line duplicated functions across 8+ enforcement plugins.

export const DISPATCH_TOOLS = Object.freeze(["task", "agent", "workflow"]) as readonly string[]

export function isDispatchTool(tool: string): boolean {
  return DISPATCH_TOOLS.includes(tool)
}

export const READ_TOOLS = Object.freeze(["read", "grep", "glob"]) as readonly string[]

export function isReadTool(tool: string): boolean {
  return READ_TOOLS.includes(tool)
}

// ── Shared Streak State (P3: cross-call grinding detection) ────────────────
// Shared between enforce-floor.ts and enforce-stop.ts so EITHER plugin can
// catch main-thread grinding (serial read/edit/bash with no dispatch). The
// dedup window prevents double-counting when both plugins fire on the same
// tool.execute.before event.

export const SHARED_STREAK_FILE = process.env.GLUDD_STREAK_FILE || "/tmp/gludd-tool-streak.json"
export const STREAK_DEDUP_WINDOW_MS = 500

export interface SharedStreakState {
  streak: number
  lastDispatchTs: number
  readStreak: number
  editStreak: number
  lastUpdateTs: number
  lastWriter: string
  pid: number
}

export function readSharedStreak(): SharedStreakState {
  try {
    if (fs.existsSync(SHARED_STREAK_FILE)) {
      const raw = JSON.parse(fs.readFileSync(SHARED_STREAK_FILE, "utf8"))
      const now = Date.now()
      const fileMtime = fs.statSync(SHARED_STREAK_FILE).mtimeMs
      const lastTs = typeof raw.lastUpdateTs === "number" ? raw.lastUpdateTs : 0
      const STALE_MS = 60_000
      const sessionStartMtime = getSessionStartMtimeMs()
      const mtimeStale = sessionStartMtime > 0 && fileMtime < sessionStartMtime
      const timeStale = lastTs > 0 && now - lastTs > STALE_MS
      const storedPid = typeof raw.pid === "number" ? raw.pid : 0
      const pidMismatch = storedPid > 0 && storedPid !== process.pid
      if (mtimeStale || timeStale || pidMismatch) {
        const reason = mtimeStale ? "mtime-reset" : timeStale ? "stale-reset" : "pid-reset"
        const zeroed = { streak: 0, lastDispatchTs: 0, readStreak: 0, editStreak: 0, lastUpdateTs: now, lastWriter: reason, pid: process.pid }
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

export function writeSharedStreak(s: SharedStreakState): void {
  s.pid = process.pid
  try { fs.writeFileSync(SHARED_STREAK_FILE, JSON.stringify(s), "utf8") } catch {}
}

export function updateSharedStreak(tool: string, pluginName: string): SharedStreakState {
  const s = readSharedStreak()
  const now = Date.now()
  const isDispatch = isDispatchTool(tool)
  const isRead = isReadTool(tool)
  const alreadyCounted = (now - s.lastUpdateTs) < STREAK_DEDUP_WINDOW_MS
    && s.lastWriter !== pluginName
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
  s.lastWriter = pluginName
  writeSharedStreak(s)
  return s
}

// ── Heartbeat / liveness probe ────────────────────────────────────────────
// Reports plugin liveness to ALIVE_PATH so watchdogs can detect dead plugins.

export function reportAlive(pluginName: string): void {
  try {
    const now = Date.now()
    const alive = readJsonFile<Record<string, Record<string, unknown>>>(ALIVE_PATH, {})
    const existing = alive[pluginName] || {}
    // Write all three timestamp fields so liveness checkers that read any
    // of last_seen / ts / loaded all see a current value. `loaded` is
    // stamped once (first load) and preserved across subsequent heartbeats.
    alive[pluginName] = {
      last_seen: now,
      ts: now,
      loaded: existing.loaded || now,
    }
    writeJsonFile(ALIVE_PATH, alive)
  } catch { /* fail-open */ }
}

// ── Per-plugin heartbeat ──────────────────────────────────────────────────
// Writes a timestamp file proving the plugin's tool.execute.before fired.

export function writeHeartbeat(pluginName: string): void {
  try {
    writeJsonFile(`/tmp/gludd-plugin-heartbeat-${pluginName}.json`, {
      plugin: pluginName, ts: Date.now(), pid: process.pid
    })
  } catch { /* fail-open */ }
}

// ── Project root detection ────────────────────────────────────────────────
// Robustly finds the project root regardless of process.cwd(). Plugin worker
// processes may have a different cwd than the main opencode process, causing
// hasPendingWork()/openWorkExists() to fail finding TASKS.md/ratchet.yml.
// Resolution order: GLUDD_PROJECT_ROOT env (unconditional when the directory
// exists — T34: an explicit root with no TASKS.md means "no pending work",
// never "borrow another project's ledger") → walk up from cwd → cwd fallback.
// The cache is keyed on (GLUDD_PROJECT_ROOT, cwd) so a mid-session change to
// either invalidates the cached resolution.

let _cachedRoot: string | null = null
let _cachedRootKey: string | null = null

function _projectRootCacheKey(): string {
  let cwd = ""
  try { cwd = process.cwd() } catch { /* cwd deleted → key on env only */ }
  return `${process.env.GLUDD_PROJECT_ROOT || ""}\u0000${cwd}`
}

export function invalidateProjectRootCache(): void {
  _cachedRoot = null
  _cachedRootKey = null
}

// ── Session-start mtime staleness guards ─────────────────────────────────
// When opencode reuses PIDs across restarts, PID-only detection fails and
// stale state persists. This complements the PID check with mtime-based
// detection: if a state file was last modified before the current session
// started, it is stale and must be discarded.

export const SESSION_START_STATE_FILE = "/tmp/gludd-session-start.json"

export function getSessionStartMtimeMs(): number {
  try {
    if (fs.existsSync(SESSION_START_STATE_FILE)) {
      return fs.statSync(SESSION_START_STATE_FILE).mtimeMs
    }
  } catch { /* missing / unreadable */ }
  return 0
}

export function isStateFileMtimeStale(stateFilePath: string): boolean {
  try {
    const sessionMtime = getSessionStartMtimeMs()
    if (sessionMtime === 0) return false
    if (!fs.existsSync(stateFilePath)) return false
    return fs.statSync(stateFilePath).mtimeMs < sessionMtime
  } catch {
    return false
  }
}

export function getProjectRoot(): string {
  const key = _projectRootCacheKey()
  if (_cachedRoot !== null && _cachedRootKey === key) return _cachedRoot
  try {
    const envRoot = process.env.GLUDD_PROJECT_ROOT
    if (envRoot && fs.existsSync(envRoot) && fs.statSync(envRoot).isDirectory()) {
      _cachedRoot = envRoot
      _cachedRootKey = key
      return _cachedRoot
    }
  } catch { /* ignore */ }
  try {
    let dir = process.cwd()
    for (let i = 0; i < 15; i++) {
      if (fs.existsSync(path.join(dir, "TASKS.md"))) {
        _cachedRoot = dir
        _cachedRootKey = key
        return _cachedRoot
      }
      if (fs.existsSync(path.join(dir, "opencode.json")) && fs.existsSync(path.join(dir, "Makefile"))) {
        _cachedRoot = dir
        _cachedRootKey = key
        return _cachedRoot
      }
      const parent = path.dirname(dir)
      if (parent === dir) break
      dir = parent
    }
  } catch { /* ignore */ }
  _cachedRoot = process.cwd()
  _cachedRootKey = key
  return _cachedRoot
}
