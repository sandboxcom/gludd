/** Durable, content-addressed ownership for delegated work. */

import { createHash, randomUUID } from "node:crypto"
import * as fs from "node:fs"
import * as path from "node:path"

const DISPATCH_DEDUP_STATE = process.env.GLUDD_DISPATCH_DEDUP_STATE
  || path.join(process.cwd(), ".gludd", "dispatch-ledger.json")
const DISPATCH_DEDUP_ENABLED = (process.env.GLUDD_DISPATCH_DEDUP_ENFORCE || "1") !== "0"
const LOCK_STALE_MS = 30_000
const MAX_LEDGER_BYTES = 2 * 1024 * 1024

type DispatchStatus = "in_progress" | "completed" | "failed"

interface DispatchLedgerEntry {
  fingerprint: string
  normalized_spec: string
  prompt_head: string
  task_ids: string[]
  tool: string
  status: DispatchStatus
  attempts: number
  denied_duplicates: number
  first_dispatched_at: number
  updated_at: number
}

interface DispatchLedger {
  version: 2
  entries: Record<string, DispatchLedgerEntry>
}

interface LockRecord {
  pid: number
  created_at_ms: number
  token: string
}

const TRACKED_TASK_ID_RE = /\b[A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)+\b/g

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
  const normalizedPrompt = prompt.trim().replace(/\s+/g, " ").toLowerCase()
  return normalizedPrompt ? `${tool.trim().toLowerCase()}\n${normalizedPrompt}` : ""
}

function dispatchFingerprint(normalizedSpec: string): string {
  return createHash("sha256").update(normalizedSpec, "utf8").digest("hex")
}

function loadDispatchLedger(): DispatchLedger {
  if (!fs.existsSync(DISPATCH_DEDUP_STATE)) return { version: 2, entries: {} }
  const size = fs.statSync(DISPATCH_DEDUP_STATE).size
  if (size > MAX_LEDGER_BYTES) throw new Error("dispatch ledger exceeds 2 MiB")
  const parsed = JSON.parse(fs.readFileSync(DISPATCH_DEDUP_STATE, "utf8")) as Partial<DispatchLedger>
  if (parsed.version !== 2 || !parsed.entries || typeof parsed.entries !== "object") {
    throw new Error("unsupported or malformed dispatch ledger")
  }
  return { version: 2, entries: parsed.entries }
}

function fsyncDirectory(directory: string): void {
  let descriptor: number | undefined
  try {
    descriptor = fs.openSync(directory, "r")
    fs.fsyncSync(descriptor)
  } catch {
    // The atomic rename is still safe on filesystems that reject directory fsync.
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor)
  }
}

function saveDispatchLedger(ledger: DispatchLedger): void {
  const directory = path.dirname(DISPATCH_DEDUP_STATE)
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
  const encoded = JSON.stringify(ledger)
  if (Buffer.byteLength(encoded, "utf8") > MAX_LEDGER_BYTES) {
    throw new Error("dispatch ledger exceeds 2 MiB")
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
    const age = now - Number(decoded.created_at_ms)
    return age > LOCK_STALE_MS && !processIsAlive(Number(decoded.pid))
  } catch {
    return false
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
      throw new Error("dispatch ledger lock is busy")
    }
    const record: LockRecord = {
      pid: process.pid,
      created_at_ms: Date.now(),
      token,
    }
    fs.writeFileSync(descriptor, JSON.stringify(record), "utf8")
    fs.fsyncSync(descriptor)
    fs.closeSync(descriptor)
    return () => {
      try {
        const current = JSON.parse(fs.readFileSync(lockPath, "utf8")) as Partial<LockRecord>
        if (current.token === token) fs.unlinkSync(lockPath)
      } catch {
        // A missing/replaced lock is never removed by the former owner.
      }
    }
  }
  throw new Error("dispatch ledger lock is busy")
}

function withLedgerLock<T>(operation: () => T): T {
  const release = acquireLedgerLock()
  try {
    return operation()
  } finally {
    release()
  }
}

export function registerDispatch(
  tool: string,
  args: Record<string, unknown> | undefined,
): string | null {
  if (!DISPATCH_DEDUP_ENABLED) return null
  const prompt = extractDispatchPrompt(args)
  const normalizedSpec = normalizeDispatchSpec(tool, prompt)
  if (!normalizedSpec) return null
  try {
    return withLedgerLock(() => {
      const ledger = loadDispatchLedger()
      const fingerprint = dispatchFingerprint(normalizedSpec)
      const existing = ledger.entries[fingerprint]
      const taskIds = extractTrackedTaskIds(prompt)
      const now = Date.now()
      if (existing && (existing.status === "in_progress" || existing.status === "completed")) {
        existing.denied_duplicates += 1
        existing.updated_at = now
        saveDispatchLedger(ledger)
        return [
          "DUPLICATE DISPATCH DENIED:",
          `fingerprint=${fingerprint}`,
          `status=${existing.status}`,
          `attempts=${existing.attempts}`,
          "Use the existing result or submit a materially different task specification.",
        ].join(" ")
      }
      const taskIdCollision = Object.values(ledger.entries).find(entry =>
        (entry.status === "in_progress" || entry.status === "completed")
        && entry.task_ids.some(taskId => taskIds.includes(taskId))
      )
      if (taskIdCollision) {
        const taskId = taskIdCollision.task_ids.find(candidate => taskIds.includes(candidate)) || "unknown"
        taskIdCollision.denied_duplicates += 1
        taskIdCollision.updated_at = now
        saveDispatchLedger(ledger)
        return [
          "DUPLICATE DISPATCH DENIED:",
          `task_id=${taskId}`,
          `fingerprint=${taskIdCollision.fingerprint}`,
          `status=${taskIdCollision.status}`,
          "Use the existing owner/result instead of rewording the same tracked task.",
        ].join(" ")
      }
      ledger.entries[fingerprint] = {
        fingerprint,
        normalized_spec: normalizedSpec,
        prompt_head: prompt.trim().replace(/\s+/g, " ").slice(0, 160),
        task_ids: taskIds,
        tool,
        status: "in_progress",
        attempts: (existing?.attempts || 0) + 1,
        denied_duplicates: existing?.denied_duplicates || 0,
        first_dispatched_at: existing?.first_dispatched_at || now,
        updated_at: now,
      }
      saveDispatchLedger(ledger)
      return null
    })
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    return `DISPATCH DEDUP STATE ERROR: ${detail}. Dispatch denied because ownership cannot be proven.`
  }
}

export function finishDispatch(
  tool: string,
  args: Record<string, unknown> | undefined,
  output: Record<string, unknown> | undefined,
): void {
  if (!DISPATCH_DEDUP_ENABLED) return
  if (!["task", "agent", "workflow"].includes(tool)) return
  const normalizedSpec = normalizeDispatchSpec(tool, extractDispatchPrompt(args))
  if (!normalizedSpec) return
  try {
    withLedgerLock(() => {
      const ledger = loadDispatchLedger()
      const fingerprint = dispatchFingerprint(normalizedSpec)
      const existing = ledger.entries[fingerprint]
      if (!existing) return
      const failed = Boolean(output?.error || output?.isError || output?.is_error)
      existing.status = failed ? "failed" : "completed"
      existing.updated_at = Date.now()
      saveDispatchLedger(ledger)
    })
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    console.warn(`DISPATCH DEDUP COMPLETION ERROR: ${detail}`)
  }
}
