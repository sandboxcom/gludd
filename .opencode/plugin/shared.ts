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

// ── Heartbeat / liveness probe ────────────────────────────────────────────
// Reports plugin liveness to ALIVE_PATH so watchdogs can detect dead plugins.

export function reportAlive(pluginName: string): void {
  try {
    const alive = readJsonFile<Record<string, unknown>>(ALIVE_PATH, {})
    alive[pluginName] = { last_seen: Date.now() }
    writeJsonFile(ALIVE_PATH, alive)
  } catch { /* fail-open */ }
}
