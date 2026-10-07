/** Durable, project-scoped ownership for delegated work. */

import { createHash, randomUUID } from "node:crypto"
import * as fs from "node:fs"
import * as path from "node:path"

const DISPATCH_DEDUP_STATE = process.env.GLUDD_DISPATCH_DEDUP_STATE
  || path.join(process.cwd(), ".gludd", "dispatch-ledger.json")
const DISPATCH_DEDUP_ENABLED = (process.env.GLUDD_DISPATCH_DEDUP_ENFORCE || "1") !== "0"
const LOCK_STALE_MS = 30_000
const DEFAULT_DISPATCH_STALE_MS = 6 * 60 * 60 * 1000
const MAX_DISPATCH_STALE_MS = 30 * 24 * 60 * 60 * 1000
const MAX_LEDGER_BYTES = 2 * 1024 * 1024
const PROCESS_STARTED_AT_MS = Math.max(0, Math.trunc(Date.now() - process.uptime() * 1000))

type DispatchStatus = "in_progress" | "completed" | "failed" | "cancelled"
type TerminalReason = "success" | "error" | "cancelled" | "stale_owner"

interface DispatchOwner {
  owner_id: string
  pid: number
  process_started_at_ms: number
  claimed_at: number
}

interface DispatchLedgerEntry {
  fingerprint: string
  project_digest: string
  scope_digest: string
  spec_digest: string
  task_ids: string[]
  tool: string
  status: DispatchStatus
  attempts: number
  denied_duplicates: number
  stale_recoveries: number
  first_dispatched_at: number
  updated_at: number
  owner: DispatchOwner
  terminal_reason?: TerminalReason
}

interface DispatchLedger {
  version: 3
  entries: Record<string, DispatchLedgerEntry>
}

interface DispatchIdentity {
  fingerprint: string
  projectDigest: string
  scopeDigest: string
  specDigest: string
  tool: string
}

interface LoadedLedger {
  ledger: DispatchLedger
  migrated: boolean
}

interface LockRecord {
  pid: number
  created_at_ms: number
  token: string
}

const TRACKED_TASK_ID_RE = /\b[A-Z]{1,3}\d*\.\d+(?:\.\d+)*\b/g
const TRACKED_TASK_ID_FULL_RE = /^[A-Z]{1,3}\d*\.\d+(?:\.\d+)*$/
const LEGACY_TRACKED_TASK_ID_FULL_RE = /^[A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)+$/
const RETRY_TERMINAL_DIRECTIVE_RE = /(?:^|\n)\s*retry-terminal:\s*true\s*(?=\n|$)/giu
const DIGEST_RE = /^[0-9a-f]{64}$/
const VALID_TOOLS = new Set(["task", "agent", "workflow"])
const VALID_STATUSES = new Set<DispatchStatus>([
  "in_progress", "completed", "failed", "cancelled",
])
const VALID_TERMINAL_REASONS = new Set<TerminalReason>([
  "success", "error", "cancelled", "stale_owner",
])

function digest(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex")
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 0
}

function extractTrackedTaskIds(prompt: string): string[] {
  return [...new Set(prompt.toUpperCase().match(TRACKED_TASK_ID_RE) || [])].sort()
}

function extractDispatchPrompt(args: Record<string, unknown> | undefined): string {
  if (!args) return ""
  for (const key of ["prompt", "description", "message", "content", "text"]) {
    const value = args[key]
    if (typeof value === "string" && value.trim()) return value
  }
  return ""
}

function normalizeDispatchSpec(tool: string, prompt: string): string {
  const normalizedPrompt = prompt
    .replace(RETRY_TERMINAL_DIRECTIVE_RE, "\n")
    .normalize("NFKC")
    .trim()
    .replace(/\s+/gu, " ")
    .toLowerCase()
  return normalizedPrompt ? `${tool.trim().toLowerCase()}\n${normalizedPrompt}` : ""
}

function normalizeScope(raw: string): string {
  const normalized = raw.normalize("NFKC").trim().replace(/\s+/gu, " ").toLowerCase()
  if (!normalized || normalized.length > 256) throw new Error("dispatch_scope_invalid")
  return normalized
}

function canonicalProjectRoot(): string {
  const configured = process.env.GLUDD_PROJECT_ROOT || process.cwd()
  const resolved = path.resolve(configured)
  try {
    return fs.realpathSync.native(resolved)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return resolved
    throw new Error("dispatch_project_unavailable")
  }
}

function buildIdentity(tool: string, prompt: string): DispatchIdentity {
  const normalizedTool = tool.trim().toLowerCase()
  if (!VALID_TOOLS.has(normalizedTool)) throw new Error("dispatch_tool_invalid")
  const normalizedSpec = normalizeDispatchSpec(normalizedTool, prompt)
  if (!normalizedSpec) throw new Error("dispatch_spec_empty")
  const projectDigest = digest(canonicalProjectRoot())
  const scopeDigest = digest(normalizeScope(process.env.GLUDD_DISPATCH_SCOPE || "project"))
  const specDigest = digest(normalizedSpec)
  const canonicalIdentity = [
    "dispatch-ledger-v3",
    normalizedTool,
    projectDigest,
    scopeDigest,
    specDigest,
  ].join("\n")
  return {
    fingerprint: digest(canonicalIdentity),
    projectDigest,
    scopeDigest,
    specDigest,
    tool: normalizedTool,
  }
}

function dispatchStaleMs(): number {
  const raw = process.env.GLUDD_DISPATCH_STALE_MS
  if (raw === undefined || raw === "") return DEFAULT_DISPATCH_STALE_MS
  if (!/^\d+$/.test(raw)) throw new Error("dispatch_stale_window_invalid")
  const value = Number(raw)
  if (!Number.isSafeInteger(value) || value < 1 || value > MAX_DISPATCH_STALE_MS) {
    throw new Error("dispatch_stale_window_invalid")
  }
  return value
}

function explicitRetryRequested(
  args: Record<string, unknown> | undefined,
  prompt: string,
): boolean {
  RETRY_TERMINAL_DIRECTIVE_RE.lastIndex = 0
  return args?.retry_terminal === true || RETRY_TERMINAL_DIRECTIVE_RE.test(prompt)
}

function ownerIdentity(now: number): DispatchOwner {
  const sessionSeed = process.env.GLUDD_DISPATCH_OWNER_ID
    || process.env.OPENCODE_SESSION_ID
    || `${process.pid}:${PROCESS_STARTED_AT_MS}:${randomUUID()}`
  return {
    owner_id: digest(sessionSeed),
    pid: process.pid,
    process_started_at_ms: PROCESS_STARTED_AT_MS,
    claimed_at: now,
  }
}

function terminalReasonFor(status: DispatchStatus): TerminalReason | undefined {
  if (status === "completed") return "success"
  if (status === "failed") return "error"
  if (status === "cancelled") return "cancelled"
  return undefined
}

function validateTaskIds(
  value: unknown,
  pattern = TRACKED_TASK_ID_FULL_RE,
): value is string[] {
  if (!Array.isArray(value) || value.some(item => typeof item !== "string")) return false
  const taskIds = value as string[]
  return taskIds.every(taskId => pattern.test(taskId))
    && new Set(taskIds).size === taskIds.length
    && taskIds.join("\n") === [...taskIds].sort().join("\n")
}

function validOwner(value: unknown): value is DispatchOwner {
  if (!isRecord(value)) return false
  return typeof value.owner_id === "string"
    && DIGEST_RE.test(value.owner_id)
    && isNonNegativeInteger(value.pid)
    && isNonNegativeInteger(value.process_started_at_ms)
    && isNonNegativeInteger(value.claimed_at)
}

function expectedFingerprint(entry: DispatchLedgerEntry): string {
  return digest([
    "dispatch-ledger-v3",
    entry.tool,
    entry.project_digest,
    entry.scope_digest,
    entry.spec_digest,
  ].join("\n"))
}

function terminalReasonMatches(
  status: DispatchStatus,
  reason: TerminalReason | undefined,
): boolean {
  if (status === "in_progress") return reason === undefined
  if (status === "completed") return reason === "success"
  if (status === "failed") return reason === "error"
  return reason === "cancelled" || reason === "stale_owner"
}

function validateV3Entry(key: string, raw: unknown): asserts raw is DispatchLedgerEntry {
  if (!isRecord(raw)) throw new Error("dispatch_ledger_invalid")
  const status = raw.status as DispatchStatus
  const entry = raw as unknown as DispatchLedgerEntry
  const digestsValid = [raw.project_digest, raw.scope_digest, raw.spec_digest]
    .every(value => typeof value === "string" && DIGEST_RE.test(value))
  const countersValid = Number.isInteger(raw.attempts) && Number(raw.attempts) >= 1
    && isNonNegativeInteger(raw.denied_duplicates)
    && isNonNegativeInteger(raw.stale_recoveries)
  const timestampsValid = isNonNegativeInteger(raw.first_dispatched_at)
    && isNonNegativeInteger(raw.updated_at)
  const reasonValid = raw.terminal_reason === undefined
    || (typeof raw.terminal_reason === "string"
      && VALID_TERMINAL_REASONS.has(raw.terminal_reason as TerminalReason))
  if (
    !DIGEST_RE.test(key)
    || raw.fingerprint !== key
    || !digestsValid
    || typeof raw.tool !== "string"
    || !VALID_TOOLS.has(raw.tool)
    || !VALID_STATUSES.has(status)
    || !countersValid
    || !timestampsValid
    || !validateTaskIds(raw.task_ids)
    || !validOwner(raw.owner)
    || !reasonValid
    || expectedFingerprint(entry) !== key
    || !terminalReasonMatches(status, raw.terminal_reason as TerminalReason | undefined)
    || Number(raw.first_dispatched_at) > Number(raw.updated_at)
    || Number((raw.owner as DispatchOwner).claimed_at) > Number(raw.updated_at)
    || Number(raw.stale_recoveries) >= Number(raw.attempts)
  ) {
    throw new Error("dispatch_ledger_invalid")
  }
}

function migrateV2Entry(
  key: string,
  raw: unknown,
  projectDigest: string,
  scopeDigest: string,
): DispatchLedgerEntry {
  if (!isRecord(raw)) throw new Error("dispatch_ledger_invalid")
  const normalizedSpec = raw.normalized_spec
  const tool = raw.tool
  const status = raw.status as DispatchStatus
  if (
    typeof normalizedSpec !== "string"
    || digest(normalizedSpec) !== key
    || typeof tool !== "string"
    || !VALID_TOOLS.has(tool)
    || !new Set<DispatchStatus>(["in_progress", "completed", "failed"]).has(status)
    || !validateTaskIds(raw.task_ids, LEGACY_TRACKED_TASK_ID_FULL_RE)
    || !Number.isInteger(raw.attempts)
    || Number(raw.attempts) < 1
    || !isNonNegativeInteger(raw.denied_duplicates)
    || !isNonNegativeInteger(raw.first_dispatched_at)
    || !isNonNegativeInteger(raw.updated_at)
  ) {
    throw new Error("dispatch_ledger_invalid")
  }
  const specDigest = digest(normalizedSpec.normalize("NFKC"))
  const fingerprint = digest([
    "dispatch-ledger-v3", tool, projectDigest, scopeDigest, specDigest,
  ].join("\n"))
  const migrated: DispatchLedgerEntry = {
    fingerprint,
    project_digest: projectDigest,
    scope_digest: scopeDigest,
    spec_digest: specDigest,
    task_ids: (raw.task_ids as string[]).filter(
      taskId => TRACKED_TASK_ID_FULL_RE.test(taskId),
    ),
    tool,
    status,
    attempts: Number(raw.attempts),
    denied_duplicates: Number(raw.denied_duplicates),
    stale_recoveries: 0,
    first_dispatched_at: Number(raw.first_dispatched_at),
    updated_at: Number(raw.updated_at),
    owner: {
      owner_id: digest("legacy-v2-owner"),
      pid: 0,
      process_started_at_ms: 0,
      claimed_at: Number(raw.first_dispatched_at),
    },
  }
  const reason = terminalReasonFor(status)
  if (reason) migrated.terminal_reason = reason
  return migrated
}

function loadDispatchLedger(identity: DispatchIdentity): LoadedLedger {
  if (!fs.existsSync(DISPATCH_DEDUP_STATE)) {
    return { ledger: { version: 3, entries: {} }, migrated: false }
  }
  if (fs.statSync(DISPATCH_DEDUP_STATE).size > MAX_LEDGER_BYTES) {
    throw new Error("dispatch_ledger_too_large")
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(fs.readFileSync(DISPATCH_DEDUP_STATE, "utf8"))
  } catch {
    throw new Error("dispatch_ledger_invalid")
  }
  if (!isRecord(parsed) || !isRecord(parsed.entries)) {
    throw new Error("dispatch_ledger_invalid")
  }
  if (parsed.version === 3) {
    for (const [key, entry] of Object.entries(parsed.entries)) validateV3Entry(key, entry)
    return { ledger: parsed as unknown as DispatchLedger, migrated: false }
  }
  if (parsed.version !== 2) throw new Error("dispatch_ledger_version_unsupported")
  const entries: Record<string, DispatchLedgerEntry> = {}
  for (const [legacyKey, raw] of Object.entries(parsed.entries)) {
    const migrated = migrateV2Entry(
      legacyKey, raw, identity.projectDigest, identity.scopeDigest,
    )
    if (entries[migrated.fingerprint]) throw new Error("dispatch_ledger_invalid")
    entries[migrated.fingerprint] = migrated
  }
  return { ledger: { version: 3, entries }, migrated: true }
}

function fsyncDirectory(directory: string): void {
  let descriptor: number | undefined
  try {
    descriptor = fs.openSync(directory, "r")
    fs.fsyncSync(descriptor)
  } catch {
    // Atomic rename remains the portable boundary where directory fsync is unavailable.
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor)
  }
}

function saveDispatchLedger(ledger: DispatchLedger): void {
  const directory = path.dirname(DISPATCH_DEDUP_STATE)
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
  const encoded = JSON.stringify(ledger)
  if (Buffer.byteLength(encoded, "utf8") > MAX_LEDGER_BYTES) {
    throw new Error("dispatch_ledger_too_large")
  }
  const temporary = `${DISPATCH_DEDUP_STATE}.${process.pid}.${randomUUID()}.tmp`
  let descriptor: number | undefined
  try {
    descriptor = fs.openSync(temporary, "wx", 0o600)
    fs.writeFileSync(descriptor, encoded, "utf8")
    fs.fsyncSync(descriptor)
    fs.closeSync(descriptor)
    descriptor = undefined
    fs.renameSync(temporary, DISPATCH_DEDUP_STATE)
    fs.chmodSync(DISPATCH_DEDUP_STATE, 0o600)
    fsyncDirectory(directory)
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor)
    try { fs.unlinkSync(temporary) } catch { /* renamed or absent */ }
  }
}

function processIsAlive(pid: number): boolean {
  if (!Number.isInteger(pid) || pid < 1) return false
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM"
  }
}

function staleLockCanBeReclaimed(lockPath: string, now: number): boolean {
  try {
    const decoded = JSON.parse(fs.readFileSync(lockPath, "utf8")) as Partial<LockRecord>
    if (!Number.isInteger(decoded.pid) || !Number.isFinite(decoded.created_at_ms)) return false
    return now - Number(decoded.created_at_ms) > LOCK_STALE_MS
      && !processIsAlive(Number(decoded.pid))
  } catch {
    return false
  }
}

function closeDescriptorQuietly(descriptor: number): void {
  try {
    fs.closeSync(descriptor)
  } catch {
    // The descriptor may already have been closed before a later write failed.
  }
}

function unlinkQuietly(filePath: string): void {
  try {
    fs.unlinkSync(filePath)
  } catch {
    // Best-effort cleanup: an absent path needs no further action.
  }
}

function acquireLedgerLock(): () => void {
  const lockPath = `${DISPATCH_DEDUP_STATE}.lock`
  fs.mkdirSync(path.dirname(lockPath), { recursive: true, mode: 0o700 })
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const token = randomUUID()
    let descriptor: number
    try {
      descriptor = fs.openSync(lockPath, "wx", 0o600)
    } catch (error) {
      if (
        (error as NodeJS.ErrnoException).code === "EEXIST"
        && attempt === 0
        && staleLockCanBeReclaimed(lockPath, Date.now())
      ) {
        fs.unlinkSync(lockPath)
        continue
      }
      throw new Error("dispatch_ledger_lock_busy")
    }
    const record: LockRecord = { pid: process.pid, created_at_ms: Date.now(), token }
    try {
      fs.writeFileSync(descriptor, JSON.stringify(record), "utf8")
      fs.fsyncSync(descriptor)
      fs.closeSync(descriptor)
    } catch {
      closeDescriptorQuietly(descriptor)
      unlinkQuietly(lockPath)
      throw new Error("dispatch_ledger_lock_unavailable")
    }
    return () => {
      try {
        const current = JSON.parse(fs.readFileSync(lockPath, "utf8")) as Partial<LockRecord>
        if (current.token === token) fs.unlinkSync(lockPath)
      } catch {
        // A missing or replaced lock is never removed by the former owner.
      }
    }
  }
  throw new Error("dispatch_ledger_lock_busy")
}

function withLedgerLock<T>(operation: () => T): T {
  const release = acquireLedgerLock()
  try {
    return operation()
  } finally {
    release()
  }
}

function isSameScope(entry: DispatchLedgerEntry, identity: DispatchIdentity): boolean {
  return entry.project_digest === identity.projectDigest
    && entry.scope_digest === identity.scopeDigest
}

function canRecoverStaleOwner(entry: DispatchLedgerEntry, now: number): boolean {
  if (entry.status !== "in_progress" || entry.owner.pid < 1) return false
  return now - entry.owner.claimed_at >= dispatchStaleMs()
    && !processIsAlive(entry.owner.pid)
}

function claimEntry(
  identity: DispatchIdentity,
  tool: string,
  taskIds: string[],
  now: number,
  predecessor?: DispatchLedgerEntry,
  staleRecovery = false,
): DispatchLedgerEntry {
  return {
    fingerprint: identity.fingerprint,
    project_digest: identity.projectDigest,
    scope_digest: identity.scopeDigest,
    spec_digest: identity.specDigest,
    task_ids: taskIds,
    tool,
    status: "in_progress",
    attempts: (predecessor?.attempts ?? 0) + 1,
    denied_duplicates: predecessor?.denied_duplicates ?? 0,
    stale_recoveries: (predecessor?.stale_recoveries ?? 0) + (staleRecovery ? 1 : 0),
    first_dispatched_at: predecessor?.first_dispatched_at ?? now,
    updated_at: now,
    owner: ownerIdentity(now),
  }
}

function denyDuplicate(
  ledger: DispatchLedger,
  entry: DispatchLedgerEntry,
  reason: string,
  now: number,
): string {
  entry.denied_duplicates += 1
  entry.updated_at = now
  saveDispatchLedger(ledger)
  return [
    "DUPLICATE DISPATCH DENIED:",
    `reason=${reason}`,
    `fingerprint=${entry.fingerprint}`,
    `status=${entry.status}`,
    `attempts=${entry.attempts}`,
    "content_omitted=true",
  ].join(" ")
}

function retryOrDenyExact(
  ledger: DispatchLedger,
  existing: DispatchLedgerEntry,
  identity: DispatchIdentity,
  tool: string,
  taskIds: string[],
  retry: boolean,
  now: number,
  claimAllowed: boolean,
): string | null {
  if (existing.status === "completed") {
    return denyDuplicate(ledger, existing, "completed_is_terminal", now)
  }
  if (existing.status === "failed" || existing.status === "cancelled") {
    if (!retry) return denyDuplicate(ledger, existing, "explicit_retry_required", now)
    if (!claimAllowed) return null
    ledger.entries[identity.fingerprint] = claimEntry(
      identity, tool, taskIds, now, existing,
    )
    saveDispatchLedger(ledger)
    return null
  }
  if (retry && canRecoverStaleOwner(existing, now)) {
    if (!claimAllowed) return null
    ledger.entries[identity.fingerprint] = claimEntry(
      identity, tool, taskIds, now, existing, true,
    )
    saveDispatchLedger(ledger)
    return null
  }
  const reason = retry ? "owner_live_or_claim_fresh" : "existing_owner_active"
  return denyDuplicate(ledger, existing, reason, now)
}

function selectTaskCollision(
  ledger: DispatchLedger,
  identity: DispatchIdentity,
  taskIds: string[],
): DispatchLedgerEntry | undefined {
  const collisions = Object.values(ledger.entries).filter(entry =>
    isSameScope(entry, identity)
    && entry.task_ids.some(taskId => taskIds.includes(taskId))
  )
  return collisions.find(entry => entry.status === "completed")
    || collisions.find(entry => entry.status === "in_progress")
    || collisions.sort((left, right) => right.updated_at - left.updated_at)[0]
}

function evaluateDispatchAdmission(
  tool: string,
  args: Record<string, unknown> | undefined,
  claimAllowed: boolean,
): string | null {
  if (!DISPATCH_DEDUP_ENABLED) return null
  const prompt = extractDispatchPrompt(args)
  if (!prompt.trim()) return null
  try {
    dispatchStaleMs()
    const identity = buildIdentity(tool, prompt)
    return withLedgerLock(() => {
      const loaded = loadDispatchLedger(identity)
      const ledger = loaded.ledger
      if (loaded.migrated) saveDispatchLedger(ledger)
      const existing = ledger.entries[identity.fingerprint]
      const taskIds = extractTrackedTaskIds(prompt)
      const retry = explicitRetryRequested(args, prompt)
      const now = Date.now()
      if (existing) {
        return retryOrDenyExact(
          ledger, existing, identity, identity.tool, taskIds, retry, now,
          claimAllowed,
        )
      }
      const taskCollision = selectTaskCollision(ledger, identity, taskIds)
      if (taskCollision) {
        if (taskCollision.status === "completed") {
          return denyDuplicate(ledger, taskCollision, "completed_task_id", now)
        }
        if (taskCollision.status === "in_progress") {
          if (!retry || !canRecoverStaleOwner(taskCollision, now)) {
            const reason = retry ? "owner_live_or_claim_fresh" : "existing_task_owner_active"
            return denyDuplicate(ledger, taskCollision, reason, now)
          }
          if (!claimAllowed) return null
          taskCollision.status = "cancelled"
          taskCollision.terminal_reason = "stale_owner"
          taskCollision.updated_at = now
          ledger.entries[identity.fingerprint] = claimEntry(
            identity, identity.tool, taskIds, now, taskCollision, true,
          )
          saveDispatchLedger(ledger)
          return null
        }
        if (!retry) {
          return denyDuplicate(ledger, taskCollision, "explicit_retry_required", now)
        }
        if (!claimAllowed) return null
        ledger.entries[identity.fingerprint] = claimEntry(
          identity, identity.tool, taskIds, now, taskCollision,
        )
        saveDispatchLedger(ledger)
        return null
      }
      if (!claimAllowed) return null
      ledger.entries[identity.fingerprint] = claimEntry(identity, identity.tool, taskIds, now)
      saveDispatchLedger(ledger)
      return null
    })
  } catch (error) {
    const code = error instanceof Error && /^dispatch_[a-z_]+$/.test(error.message)
      ? error.message
      : "dispatch_state_unavailable"
    const detail = code === "dispatch_ledger_lock_busy" ? " dispatch ledger lock is busy." : ""
    return `DISPATCH DEDUP STATE ERROR: reason=${code}.${detail} Dispatch denied because ownership cannot be proven.`
  }
}

export function preflightDispatch(
  tool: string,
  args: Record<string, unknown> | undefined,
): string | null {
  return evaluateDispatchAdmission(tool, args, false)
}

export function registerDispatch(
  tool: string,
  args: Record<string, unknown> | undefined,
): string | null {
  return evaluateDispatchAdmission(tool, args, true)
}

function outputWasCancelled(output: Record<string, unknown> | undefined): boolean {
  const status = typeof output?.status === "string" ? output.status.toLowerCase() : ""
  return output?.cancelled === true
    || output?.canceled === true
    || status === "cancelled"
    || status === "canceled"
}

export function finishDispatch(
  tool: string,
  args: Record<string, unknown> | undefined,
  output: Record<string, unknown> | undefined,
): void {
  if (!DISPATCH_DEDUP_ENABLED || !VALID_TOOLS.has(tool)) return
  const prompt = extractDispatchPrompt(args)
  if (!prompt.trim()) return
  try {
    const identity = buildIdentity(tool, prompt)
    withLedgerLock(() => {
      const loaded = loadDispatchLedger(identity)
      const existing = loaded.ledger.entries[identity.fingerprint]
      if (!existing || existing.status !== "in_progress") {
        if (loaded.migrated) saveDispatchLedger(loaded.ledger)
        return
      }
      if (outputWasCancelled(output)) {
        existing.status = "cancelled"
        existing.terminal_reason = "cancelled"
      } else if (Boolean(output?.error || output?.isError || output?.is_error)) {
        existing.status = "failed"
        existing.terminal_reason = "error"
      } else {
        existing.status = "completed"
        existing.terminal_reason = "success"
      }
      existing.updated_at = Date.now()
      saveDispatchLedger(loaded.ledger)
    })
  } catch {
    console.warn("DISPATCH DEDUP COMPLETION ERROR: state_unavailable_or_corrupt")
  }
}
