import * as fs from "node:fs"
import * as path from "node:path"

// ── Shared helpers for enforcement plugins ────────────────────────────────
// Extracted from 14 enforce-*.ts plugins (2026-07-13, E.5 refactor).
// Eliminates duplicated _isSubagent, disengage, JSON state, and heartbeat
// patterns. Each plugin imports what it needs instead of copy-pasting.

// ── Paths ─────────────────────────────────────────────────────────────────

export const DISENGAGE_PATH =
  process.env.GLUDD_DISENGAGE_PATH || "/tmp/gludd-watchdog-disengage.json"

// Dedicated single-use disengage marker (BP.5). `make disengage-next` writes
// this file; isDisengaged() consumes it on first read (delete + return true).
// Separate from DISENGAGE_PATH so the two modes cannot interfere.
export const DISENGAGE_NEXT_PATH =
  process.env.GLUDD_DISENGAGE_NEXT_PATH || "/tmp/gludd-disengage-next"

export const DISENGAGE_AUDIT_PATH =
  process.env.GLUDD_DISENGAGE_AUDIT_PATH || "/tmp/gludd-disengage-audit.jsonl"

export const ALIVE_PATH =
  process.env.GLUDD_ALIVE_PATH || "/tmp/gludd-plugin-alive.json"

export const SUBAGENT_MARKER = (pid: number) =>
  `${process.env.GLUDD_SUBAGENT_MARKER_PREFIX || "/tmp/gludd-subagent-"}${pid}.json`

export interface GateRefreshProcess {
  pid?: number
  unref(): void
}

export type GateRefreshSpawner = (
  command: string,
  args: string[],
  options: { cwd: string; detached: true; stdio: "ignore" },
) => GateRefreshProcess

interface GateRefreshLease {
  pid: number
  started_at: number
  token: string
}

const GATE_REFRESH_STALE_MS = 300_000

function _gateRefreshNamespace(root: string): string {
  let hash = 2166136261
  for (const character of root) {
    hash ^= character.charCodeAt(0)
    hash = Math.imul(hash, 16777619)
  }
  const name = path.basename(root).replace(/[^a-zA-Z0-9_-]/g, "-") || "workspace"
  return `${name}-${(hash >>> 0).toString(16)}`
}

function _gateRefreshLeasePath(root: string): string {
  return process.env.GLUDD_GATE_REFRESH_LEASE_PATH ||
    `/tmp/gludd-gate-refresh-${_gateRefreshNamespace(root)}.json`
}

function _readGateRefreshLease(leasePath: string): GateRefreshLease | null {
  try {
    const value = JSON.parse(fs.readFileSync(leasePath, "utf8"))
    if (
      typeof value.pid === "number" &&
      typeof value.started_at === "number" &&
      typeof value.token === "string"
    ) {
      return value
    }
  } catch {}
  return null
}

function _gateRefreshPidAlive(pid: number): boolean {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

function _createGateRefreshLease(
  leasePath: string,
  lease: GateRefreshLease,
): boolean {
  let descriptor: number | undefined
  try {
    descriptor = fs.openSync(leasePath, "wx", 0o600)
    fs.writeFileSync(descriptor, JSON.stringify(lease), "utf8")
    return true
  } catch {
    return false
  } finally {
    if (descriptor !== undefined) {
      try {
        fs.closeSync(descriptor)
      } catch {}
    }
  }
}

function _removeOwnedGateRefreshLease(
  leasePath: string,
  token: string,
): void {
  try {
    if (_readGateRefreshLease(leasePath)?.token === token) {
      fs.unlinkSync(leasePath)
    }
  } catch {}
}

function _claimGateRefreshLease(
  leasePath: string,
  lease: GateRefreshLease,
): boolean {
  const reaperPath = `${leasePath}.reaper`
  if (fs.existsSync(reaperPath)) return false
  if (_createGateRefreshLease(leasePath, lease)) return true

  const existing = _readGateRefreshLease(leasePath)
  if (existing && _gateRefreshPidAlive(existing.pid)) return false

  let reaperDescriptor: number | undefined
  try {
    reaperDescriptor = fs.openSync(reaperPath, "wx", 0o600)
  } catch {
    return false
  }
  try {
    const current = _readGateRefreshLease(leasePath)
    if (current && _gateRefreshPidAlive(current.pid)) return false
    try {
      fs.unlinkSync(leasePath)
    } catch {}
    return _createGateRefreshLease(leasePath, lease)
  } finally {
    try {
      if (reaperDescriptor !== undefined) fs.closeSync(reaperDescriptor)
    } catch {}
    try {
      fs.unlinkSync(reaperPath)
    } catch {}
  }
}

export function spawnGateRefreshIfStale(
  root: string,
  spawnProcess: GateRefreshSpawner,
): boolean {
  if (process.env.GLUDD_GATE_REFRESH_AUTOSPAWN === "0") return false
  try {
    const gatePath = path.join(root, ".gate-status")
    if (!fs.existsSync(gatePath)) return false
    if ((Date.now() - fs.statSync(gatePath).mtimeMs) <= GATE_REFRESH_STALE_MS) {
      return false
    }

    const leasePath = _gateRefreshLeasePath(root)
    const token = `${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`
    const lease: GateRefreshLease = {
      pid: process.pid,
      started_at: Date.now(),
      token,
    }
    if (!_claimGateRefreshLease(leasePath, lease)) return false

    try {
      const child = spawnProcess("make", ["gate-refresh"], {
        cwd: root,
        detached: true,
        stdio: "ignore",
      })
      if (Number.isSafeInteger(child.pid) && Number(child.pid) > 0) {
        fs.writeFileSync(
          leasePath,
          JSON.stringify({ ...lease, pid: Number(child.pid) }),
          "utf8",
        )
      }
      child.unref()
      return true
    } catch {
      _removeOwnedGateRefreshLease(leasePath, token)
      return false
    }
  } catch {
    return false
  }
}

// ── Subagent guard ────────────────────────────────────────────────────────
// Every plugin must skip enforcement inside a subagent context.

export function isSubagent(): boolean {
  if (process.env.OPENCODE_SUBAGENT === "1") return true
  // An explicit false value is authoritative. Runtime harnesses and parent
  // orchestrators use it to prevent a stale, PID-reused marker from making a
  // new process silently bypass every enforcement hook.
  if (process.env.OPENCODE_SUBAGENT === "0") return false
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
  maxMs?: number // maximum forward duration (default 300_000 = 5 minutes)
}

const _sessionUuid = `${process.pid}-${Math.floor(Date.now() / 1000)}`

export function isDisengaged(opts: DisengageOpts = {}): boolean {
  const maxMs = opts.maxMs ?? 300_000
  try {
    // BP.5: dedicated single-use disengage marker. Consume-once: delete the
    // file then return true. The next call finds no file and returns false.
    // Checked BEFORE the JSON path so the two mechanisms are independent.
    try {
      if (fs.existsSync(DISENGAGE_NEXT_PATH)) {
        fs.unlinkSync(DISENGAGE_NEXT_PATH)
        try {
          const audit = JSON.stringify({ ts: Date.now(), pid: process.pid, sessionUuid: _sessionUuid, single: true, source: "disengage-next" }) + "\n"
          fs.appendFileSync(DISENGAGE_AUDIT_PATH, audit, "utf8")
        } catch { /* fail-open */ }
        return true
      }
    } catch { /* fail-open: permission / race → ignore */ }

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
// Only the explicit environment override may select an unrelated directory;
// marker discovery is restricted to cwd and its ancestors.
// The cache is keyed on (GLUDD_PROJECT_ROOT, cwd) so a mid-session change to
// either invalidates the cached resolution.

let _cachedRoot: string | null = null
let _cachedRootKey: string | null = null

function _safeProjectCwd(): string {
  try { return path.resolve(process.cwd()) } catch { return "." }
}

function _projectRootCacheKey(cwd: string): string {
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

export const SESSION_START_STATE_FILE =
  process.env.GLUDD_SESSION_STATE || "/tmp/gludd-session-start.json"

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

// ═══════════════════════════════════════════════════════════════════════════
// DISPATCH-OUTCOMES STATE — shared pressure-release mechanism
//
// When subagents return empty/failed repeatedly, the agent deadlocks:
// can't dispatch usefully AND can't work inline (blocked by streaks).
// The pressure-release mode detects this and temporarily relaxes
// enforcement: floor drops to 2, streaks skip for N turns.
// ═══════════════════════════════════════════════════════════════════════════

export const DISPATCH_OUTCOMES_FILE =
  process.env.GLUDD_DISPATCH_OUTCOMES_FILE || "/tmp/gludd-dispatch-outcomes.json"

export interface DispatchOutcomesState {
  pid: number
  consecutiveEmptyDispatches: number
  consecutiveDispatchAttempts: number
  pressureReleaseActive: boolean
  pressureReleaseTurnsRemaining: number
  pressureReleaseFloor: number
  normalFloor: number
  inlineRecoveryTurnsRemaining: number
  lastDispatchTs: number
  lastStateChangeTs: number
  ts: number
}

export function freshDispatchOutcomes(): DispatchOutcomesState {
  return {
    pid: process.pid,
    consecutiveEmptyDispatches: 0,
    consecutiveDispatchAttempts: 0,
    pressureReleaseActive: false,
    pressureReleaseTurnsRemaining: 0,
    pressureReleaseFloor: 2,
    normalFloor: 10,
    inlineRecoveryTurnsRemaining: 0,
    lastDispatchTs: 0,
    lastStateChangeTs: Date.now(),
    ts: Date.now(),
  }
}

export function readDispatchOutcomes(): DispatchOutcomesState {
  try {
    if (isStateFileMtimeStale(DISPATCH_OUTCOMES_FILE)) {
      return freshDispatchOutcomes()
    }
    if (!fs.existsSync(DISPATCH_OUTCOMES_FILE)) return freshDispatchOutcomes()
    const raw = JSON.parse(fs.readFileSync(DISPATCH_OUTCOMES_FILE, "utf8"))
    const storedPid = typeof raw.pid === "number" ? raw.pid : 0
    if (storedPid > 0 && storedPid !== process.pid) {
      return freshDispatchOutcomes()
    }
    return {
      pid: process.pid,
      consecutiveEmptyDispatches: typeof raw.consecutiveEmptyDispatches === "number" ? raw.consecutiveEmptyDispatches : 0,
      consecutiveDispatchAttempts: typeof raw.consecutiveDispatchAttempts === "number" ? raw.consecutiveDispatchAttempts : 0,
      pressureReleaseActive: !!raw.pressureReleaseActive,
      pressureReleaseTurnsRemaining: typeof raw.pressureReleaseTurnsRemaining === "number" ? raw.pressureReleaseTurnsRemaining : 0,
      pressureReleaseFloor: typeof raw.pressureReleaseFloor === "number" ? raw.pressureReleaseFloor : 2,
      normalFloor: typeof raw.normalFloor === "number" ? raw.normalFloor : 10,
      inlineRecoveryTurnsRemaining: typeof raw.inlineRecoveryTurnsRemaining === "number" ? raw.inlineRecoveryTurnsRemaining : 0,
      lastDispatchTs: typeof raw.lastDispatchTs === "number" ? raw.lastDispatchTs : 0,
      lastStateChangeTs: typeof raw.lastStateChangeTs === "number" ? raw.lastStateChangeTs : Date.now(),
      ts: Date.now(),
    }
  } catch {
    return freshDispatchOutcomes()
  }
}

export function writeDispatchOutcomes(partial: Partial<DispatchOutcomesState>): void {
  try {
    const current = readDispatchOutcomes()
    const merged: DispatchOutcomesState = { ...current, ...partial, ts: Date.now() }
    const tmp = DISPATCH_OUTCOMES_FILE + ".tmp"
    fs.writeFileSync(tmp, JSON.stringify(merged), "utf8")
    fs.renameSync(tmp, DISPATCH_OUTCOMES_FILE)
  } catch { /* fail-open */ }
}

export function isInPressureRelease(): boolean {
  try {
    const s = readDispatchOutcomes()
    if (!s.pressureReleaseActive) return false
    if (s.pressureReleaseTurnsRemaining <= 0) return false
    return true
  } catch { return false }
}

export function isInInlineRecovery(): boolean {
  try {
    const s = readDispatchOutcomes()
    if (!s.pressureReleaseActive) return false
    if (s.inlineRecoveryTurnsRemaining <= 0) return false
    return true
  } catch { return false }
}

export function getPressureReleaseFloor(normalFloor: number): number {
  try {
    const s = readDispatchOutcomes()
    if (s.pressureReleaseActive && s.pressureReleaseTurnsRemaining > 0) {
      return Math.max(2, s.pressureReleaseFloor)
    }
  } catch {}
  return normalFloor
}

/**
 * Call at every message boundary (text.complete). Decrements pressure-release
 * and inline-recovery turn counters. When both reach 0, deactivates the mode.
 */
export function decrementPressureReleaseTurns(): void {
  try {
    const s = readDispatchOutcomes()
    if (!s.pressureReleaseActive) return
    s.pressureReleaseTurnsRemaining = Math.max(0, s.pressureReleaseTurnsRemaining - 1)
    s.inlineRecoveryTurnsRemaining = Math.max(0, s.inlineRecoveryTurnsRemaining - 1)
    if (s.pressureReleaseTurnsRemaining <= 0 && s.inlineRecoveryTurnsRemaining <= 0) {
      s.pressureReleaseActive = false
      s.consecutiveEmptyDispatches = 0
      s.consecutiveDispatchAttempts = 0
      s.lastStateChangeTs = Date.now()
      console.warn(
        `PRESSURE-RELEASE EXPIRED: returning to normal enforcement ` +
        `(floor=${s.normalFloor}). consecutiveEmptyDispatches reset.`
      )
    }
    writeDispatchOutcomes(s)
  } catch {}
}

/**
 * Record a dispatch attempt. If 3+ consecutive attempts all returned empty,
 * activate pressure-release mode: floor drops to 2, streaks skip for 3 turns,
 * inline recovery allowed for 5 turns.
 */
export function recordDispatchAttempt(): void {
  try {
    const s = readDispatchOutcomes()
    s.lastDispatchTs = Date.now()
    s.consecutiveDispatchAttempts++
    writeDispatchOutcomes(s)
  } catch {}
}

/**
 * Record that a dispatch returned empty/failed. Increments the empty counter.
 * When 3 consecutive empties are reached, activates pressure-release mode.
 */
export function recordEmptyDispatch(): void {
  try {
    const s = readDispatchOutcomes()
    s.consecutiveEmptyDispatches++
    if (s.consecutiveEmptyDispatches >= 3 && !s.pressureReleaseActive) {
      s.pressureReleaseActive = true
      s.pressureReleaseTurnsRemaining = 3
      s.inlineRecoveryTurnsRemaining = 5
      s.consecutiveEmptyDispatches = 0
      s.lastStateChangeTs = Date.now()
      console.warn(
        `PRESSURE-RELEASE ACTIVATED: ${s.consecutiveDispatchAttempts} dispatches, ` +
        `3+ consecutive empty results. Floor lowered to ${s.pressureReleaseFloor} for 3 turns, ` +
        `inline recovery allowed for 5 turns.`
      )
    }
    writeDispatchOutcomes(s)
  } catch {}
}

/**
 * Reset empty-dispatch tracking when a dispatch returns useful results.
 */
export function recordSuccessfulDispatch(): void {
  try {
    const s = readDispatchOutcomes()
    s.consecutiveEmptyDispatches = 0
    writeDispatchOutcomes(s)
  } catch {}
}

export function getProjectRoot(): string {
  const cwd = _safeProjectCwd()
  const key = _projectRootCacheKey(cwd)
  if (_cachedRoot !== null && _cachedRootKey === key) return _cachedRoot
  try {
    const envRoot = process.env.GLUDD_PROJECT_ROOT
    const explicitRoot = envRoot
      ? (path.isAbsolute(envRoot) ? path.normalize(envRoot) : path.resolve(cwd, envRoot))
      : ""
    if (explicitRoot && fs.statSync(explicitRoot).isDirectory()) {
      _cachedRoot = explicitRoot
      _cachedRootKey = key
      return _cachedRoot
    }
  } catch { /* ignore */ }
  try {
    let dir = cwd
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
  _cachedRoot = cwd
  _cachedRootKey = key
  return _cachedRoot
}

// ── TASKS.md pending-work detection ──────────────────────────────────────
// Checks both checkbox format (- [ ]) AND table-format entries
// (| NOT STARTED |, | IN PROGRESS |, | PENDING |). Prior to 2026-08-06,
// only checkbox format was detected, so table-format task entries silently
// bypassed the 10-agent floor enforcement.

export function hasTasksMdPendingWork(tasksMdPath: string): boolean {
  try {
    if (!fs.existsSync(tasksMdPath)) return false
    const content = fs.readFileSync(tasksMdPath, "utf8")
    if (/^\s*[-*]\s*\[\s*\]/m.test(content)) return true
    if (/\|\s*(NOT STARTED|IN PROGRESS|PENDING)\s*\|/im.test(content)) return true
    return false
  } catch {
    return false
  }
}
