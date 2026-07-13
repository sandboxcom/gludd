import * as fs from "node:fs"

// ── Shared helpers for enforcement plugins ────────────────────────────────
// Extracted from 14 enforce-*.ts plugins (2026-07-13, E.5 refactor).
// Eliminates duplicated _isSubagent, disengage, JSON state, and heartbeat
// patterns. Each plugin imports what it needs instead of copy-pasting.

// ── Paths ─────────────────────────────────────────────────────────────────

export const DISENGAGE_PATH =
  process.env.GLUDD_DISENGAGE_PATH || "/tmp/gludd-watchdog-disengage.json"

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

export function isDisengaged(opts: DisengageOpts = {}): boolean {
  const maxMs = opts.maxMs ?? 3_600_000
  try {
    if (!fs.existsSync(DISENGAGE_PATH)) return false
    const d = JSON.parse(fs.readFileSync(DISENGAGE_PATH, "utf8"))
    if (typeof d.disengage_until !== "number") return false
    const now = Date.now()
    const effective = Math.min(d.disengage_until, now + maxMs)
    return effective > now
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
    fs.writeFileSync(filePath, JSON.stringify(data), "utf8")
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
      const lastTs = typeof raw.lastUpdateTs === "number" ? raw.lastUpdateTs : 0
      const STALE_MS = 60_000
      if (lastTs > 0 && now - lastTs > STALE_MS) {
        const zeroed = { streak: 0, lastDispatchTs: 0, readStreak: 0, editStreak: 0, lastUpdateTs: now, lastWriter: "stale-reset", pid: process.pid }
        try { fs.writeFileSync(SHARED_STREAK_FILE, JSON.stringify(zeroed), "utf8") } catch {}
        return zeroed
      }
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
    const alive = readJsonFile<Record<string, unknown>>(ALIVE_PATH, {})
    alive[pluginName] = { last_seen: Date.now() }
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
