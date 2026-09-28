import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { execFileSync } from "node:child_process"
import { createHash } from "node:crypto"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import {
  deny,
  extractBashCommand,
  extractDispatchText,
  extractExitCode,
  extractFilePath,
  getProjectRoot,
  isDispatchTool,
  isSubagent,
  readJsonFile,
  replaceBashCommand,
  reportAlive,
  writeJsonFile,
} from "../lib/shared.ts"
import {
  PIPELINE_AGENT_CAP,
  buildIsolatedDispatchPrompt,
  candidateBatchDigest,
  consolidateCandidateBatch,
  dispatchKey,
  extractMakeTarget,
  formatKickoffNotice,
  isIsolatedDispatchPrompt,
  isLongPipelineLaunch,
  isUsefulObjective,
  projectKickoffStatePath,
  withFrozenRef,
  type PipelineCandidate,
} from "../lib/pipeline_kickoff.ts"

const MAX_STATE_AGE_MS = Number.parseInt(
  process.env.GLUDD_PIPELINE_KICKOFF_MAX_AGE_MS || "14400000",
  10,
)

const FROZEN_BASH_ALLOWLIST = new Set([
  "active-work-status",
  "agent-worktree-base",
  "cat-file",
  "gate-status",
  "git-status-safe",
  "help",
  "pipeline-status",
  "ps",
  "ps-gludd",
  "search",
  "show-lines",
  "test-ci-shards-parallel-status",
  "worktree-state",
])

type PipelineStatus = "launching" | "running" | "complete" | "failed"

interface PipelineKickoffState {
  version: 1 | 2
  status: PipelineStatus
  pipeline_target: string
  command: string
  tested_ref: string
  tested_worktree: string
  created_at: number
  updated_at: number
  existing_in_flight?: number
  candidates: PipelineCandidate[]
  candidate_batch_digest: string
  dispatched_keys: string[]
  launch_digest?: string
  status_path?: string | null
  status_baseline_digest?: string
}

function statePath(): string {
  return process.env.GLUDD_PIPELINE_KICKOFF_STATE || projectKickoffStatePath(getProjectRoot())
}

function readState(): PipelineKickoffState | null {
  return readJsonFile<PipelineKickoffState | null>(statePath(), null)
}

function writeState(state: PipelineKickoffState): void {
  state.updated_at = Date.now()
  writeJsonFile(statePath(), state)
}

function isInsideTestedCheckout(filePath: string, testedWorktree: string): boolean {
  if (!filePath) return true
  const absolute = path.isAbsolute(filePath)
    ? path.resolve(filePath)
    : path.resolve(testedWorktree, filePath)
  const relative = path.relative(path.resolve(testedWorktree), absolute)
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative))
}

function physicalPath(filePath: string): string {
  let resolvedPath: string | undefined
  try {
    resolvedPath = fs.realpathSync(filePath)
  } catch {}
  if (resolvedPath !== undefined) return resolvedPath

  let symlinkTarget: string | undefined
  try {
    if (fs.lstatSync(filePath).isSymbolicLink()) {
      symlinkTarget = path.resolve(path.dirname(filePath), fs.readlinkSync(filePath))
    }
  } catch {}
  if (symlinkTarget !== undefined) {
    try {
      return fs.realpathSync(symlinkTarget)
    } catch {
      return path.join(fs.realpathSync(path.dirname(symlinkTarget)), path.basename(symlinkTarget))
    }
  }
  return path.join(fs.realpathSync(path.dirname(filePath)), path.basename(filePath))
}

function isPhysicalPathInsideCheckout(filePath: string, testedWorktree: string): boolean {
  try {
    const root = fs.realpathSync(testedWorktree)
    const candidate = physicalPath(filePath)
    const relative = path.relative(root, candidate)
    return relative === "" || (
      !relative.startsWith(`..${path.sep}`) &&
      relative !== ".." &&
      !path.isAbsolute(relative)
    )
  } catch {
    return false
  }
}

function belongsToCurrentCheckout(state: PipelineKickoffState): boolean {
  return typeof state.tested_worktree === "string" &&
    path.resolve(state.tested_worktree) === path.resolve(getProjectRoot())
}

function fileDigest(filePath: string): string | undefined {
  try {
    return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex")
  } catch {
    return undefined
  }
}

function launchBindingDigest(state: PipelineKickoffState): string {
  const payload = {
    version: state.version,
    pipeline_target: state.pipeline_target,
    command: state.command,
    tested_ref: state.tested_ref,
    tested_worktree: state.tested_worktree,
    created_at: state.created_at,
    existing_in_flight: state.existing_in_flight ?? 0,
    candidate_batch_digest: state.candidate_batch_digest,
    status_path: state.status_path ?? null,
    status_baseline_digest: state.status_baseline_digest ?? null,
  }
  return createHash("sha256").update(JSON.stringify(payload)).digest("hex")
}

function launchBindingIsValid(state: PipelineKickoffState): boolean {
  return state.version === 1 || (
    typeof state.launch_digest === "string" &&
    state.launch_digest === launchBindingDigest(state)
  )
}

function captureTestedRef(root: string): string {
  const override = process.env.GLUDD_PIPELINE_TESTED_REF
  if (override && override.trim()) return override.trim()
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: root,
      encoding: "utf8",
      timeout: 3000,
      stdio: ["ignore", "pipe", "ignore"],
    }).trim()
  } catch {
    return "unknown"
  }
}

function rawCandidatesFromExistingState(): unknown[] {
  const candidates: unknown[] = []
  const todoPath = process.env.GLUDD_TODOWRITE_STATE_PATH ||
    process.env.GLUDD_TODOWRITE_STATE || "/tmp/gludd-todowrite-state.json"
  const todo = readJsonFile<unknown>(todoPath, [])
  if (Array.isArray(todo)) candidates.push(...todo)
  else if (todo && typeof todo === "object" && Array.isArray((todo as { items?: unknown[] }).items)) {
    candidates.push(...((todo as { items: unknown[] }).items))
  }

  const forcePath = process.env.GLUDD_FORCE_DISPATCH_PATH || "/tmp/gludd-force-dispatch.json"
  const force = readJsonFile<unknown>(forcePath, null)
  if (force && typeof force === "object" && Array.isArray((force as { dispatch_commands?: unknown[] }).dispatch_commands)) {
    candidates.push(...((force as { dispatch_commands: unknown[] }).dispatch_commands))
  }

  const preflightPath = process.env.GLUDD_DISPATCH_PREFLIGHT_PATH || "/tmp/gludd-dispatch-preflight.json"
  const preflight = readJsonFile<unknown>(preflightPath, null)
  if (preflight && typeof preflight === "object") {
    const items = (preflight as { planned_items?: unknown[] }).planned_items
    if (Array.isArray(items)) candidates.push(...items)
  }
  return candidates
}

function estimatedInFlight(): number {
  const multitaskPath = process.env.GLUDD_MULTITASK_STATE_FILE || "/tmp/gludd-multitask-state.json"
  const state = readJsonFile<Record<string, unknown>>(multitaskPath, {})
  const value = Number(state.estimatedInFlight)
  return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0
}

function prepareState(command: string): PipelineKickoffState {
  const root = path.resolve(getProjectRoot())
  const testedRef = captureTestedRef(root)
  const inFlight = estimatedInFlight()
  const batch = consolidateCandidateBatch(rawCandidatesFromExistingState(), inFlight)
    .map(candidate => {
      const dispatchPrompt = buildIsolatedDispatchPrompt(candidate, testedRef, root)
      return {
        ...candidate,
        key: dispatchKey(dispatchPrompt),
        dispatch_prompt: dispatchPrompt,
      }
    })
  const frozenCommand = withFrozenRef(command, testedRef)
  const now = Date.now()
  const state: PipelineKickoffState = {
    version: 2,
    status: "launching",
    pipeline_target: extractMakeTarget(frozenCommand) || "unknown",
    command: frozenCommand,
    tested_ref: testedRef,
    tested_worktree: root,
    created_at: now,
    updated_at: now,
    existing_in_flight: inFlight,
    candidates: batch,
    candidate_batch_digest: candidateBatchDigest(testedRef, root, batch),
    dispatched_keys: [],
    status_path: null,
  }
  const receiptPath = deriveStatusPath(state)
  state.status_path = receiptPath
  if (receiptPath) {
    state.status_baseline_digest = fileDigest(receiptPath)
  }
  state.launch_digest = launchBindingDigest(state)
  return state
}

function deriveStatusPath(state: PipelineKickoffState): string | null {
  let configured = process.env.GLUDD_PIPELINE_STATUS_PATH || ""
  if (!configured && state.pipeline_target === "ship-async") configured = ".ship-status"
  if (!configured && state.pipeline_target.includes("gate")) configured = ".gate-status"
  if (!configured) return null
  const resolved = path.isAbsolute(configured)
    ? path.resolve(configured)
    : path.resolve(state.tested_worktree, configured)
  return isPhysicalPathInsideCheckout(resolved, state.tested_worktree) ? resolved : null
}

function statusPathFor(state: PipelineKickoffState): string | null {
  if (state.version === 2) {
    if (!state.status_path) return null
    const resolved = path.resolve(state.status_path)
    return isPhysicalPathInsideCheckout(resolved, state.tested_worktree) ? resolved : null
  }
  return deriveStatusPath(state)
}

function terminalReceiptStatus(
  state: PipelineKickoffState,
  content: string,
): "complete" | "failed" | null {
  const lines = content.split(/\r?\n/).map(line => line.trim()).filter(Boolean)
  const receipt = lines.at(-1) || ""
  if (state.pipeline_target === "ship-async") {
    const passed = receipt.match(/^SHIP PASS\s+(\S+)$/)
    if (passed) return passed[1] === state.tested_ref ? "complete" : null
    return /^SHIP FAIL(?:\s|$)/.test(receipt) ? "failed" : null
  }
  const gate = receipt.match(/^(PASS|FAIL)\s+(\d+)\b/)
  if (gate) {
    const receiptTime = Number(gate[2]) * 1000
    if (
      !Number.isFinite(receiptTime) ||
      receiptTime + 1000 < state.created_at ||
      receiptTime > Date.now() + 60_000
    ) return null
    return gate[1] === "PASS" ? "complete" : "failed"
  }
  if (/^test PASS\s+0$/.test(receipt)) return "complete"
  if (/^test FAIL(?:\s|$)/.test(receipt)) return "failed"
  return null
}

function refreshTerminalState(state: PipelineKickoffState | null): PipelineKickoffState | null {
  if (!state || !["launching", "running"].includes(state.status)) return state
  if (!launchBindingIsValid(state)) return state
  const age = Date.now() - state.created_at
  if (Number.isFinite(MAX_STATE_AGE_MS) && MAX_STATE_AGE_MS > 0 && age > MAX_STATE_AGE_MS) {
    state.status = "failed"
    writeState(state)
    return state
  }
  const statusPath = statusPathFor(state)
  if (!statusPath) return state
  try {
    const stat = fs.statSync(statusPath)
    if (stat.mtimeMs < state.created_at) return state
    const content = fs.readFileSync(statusPath, "utf8")
    const digest = createHash("sha256").update(content).digest("hex")
    if (state.status_baseline_digest && digest === state.status_baseline_digest) return state
    const terminalStatus = terminalReceiptStatus(state, content)
    if (terminalStatus) {
      state.status = terminalStatus
      writeState(state)
    }
  } catch {
    // A launcher writes status asynchronously; absence during startup is normal.
  }
  return state
}

function active(state: PipelineKickoffState | null): state is PipelineKickoffState {
  return state !== null && belongsToCurrentCheckout(state) &&
    ["launching", "running"].includes(state.status)
}

function exposeKickoff(output: unknown, state: PipelineKickoffState): void {
  const notice = formatKickoffNotice(state.tested_ref, state.tested_worktree, state.candidates)
  let exposed = false
  try {
    if (output && typeof output === "object") {
      const envelope = output as {
        metadata?: Record<string, unknown>
        output?: unknown
        result?: unknown
      }
      envelope.metadata = { ...(envelope.metadata || {}), pipelineKickoff: state }
      if (typeof envelope.output === "string") {
        envelope.output = `${envelope.output}\n\n${notice}`
        exposed = true
      } else if (typeof envelope.result === "string") {
        envelope.result = `${envelope.result}\n\n${notice}`
        exposed = true
      } else if (envelope.result && typeof envelope.result === "object") {
        const result = envelope.result as { stdout?: unknown }
        if (typeof result.stdout === "string") {
          result.stdout = `${result.stdout}\n\n${notice}`
          exposed = true
        }
      }
      exposed = true // metadata always exposes the machine-readable batch.
    }
  } catch {
    exposed = false
  }
  if (!exposed) console.warn(notice)
}

function candidateBatchIsValid(state: PipelineKickoffState): boolean {
  try {
    if (
      !launchBindingIsValid(state) ||
      !Array.isArray(state.candidates) ||
      typeof state.candidate_batch_digest !== "string" ||
      state.candidate_batch_digest !== candidateBatchDigest(
        state.tested_ref,
        state.tested_worktree,
        state.candidates,
      )
    ) {
      return false
    }
    const keys = new Set<string>()
    for (const candidate of state.candidates) {
      if (
        !candidate.dispatch_prompt ||
        candidate.key !== dispatchKey(candidate.dispatch_prompt) ||
        keys.has(candidate.key)
      ) {
        return false
      }
      keys.add(candidate.key)
    }
    return true
  } catch {
    return false
  }
}

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, output: any) => {
    if (isSubagent()) return
    reportAlive("enforce-pipeline-kickoff")
    try {
      if (process.env.GLUDD_PIPELINE_KICKOFF_ENFORCE === "0") return
      const tool = String(input?.tool || "")
      const command = tool === "bash" ? extractBashCommand(input, output) : ""

      if (tool === "bash" && isLongPipelineLaunch(command)) {
        const current = refreshTerminalState(readState())
        if (active(current)) {
          return deny(
            `PIPELINE KICKOFF: ${current.pipeline_target} already freezes ` +
            `${current.tested_worktree} @ ${current.tested_ref}. Do not launch a duplicate pipeline.`,
          )
        }
        const state = prepareState(command)
        replaceBashCommand(input, output, state.command)
        writeState(state)
        return
      }

      const state = refreshTerminalState(readState())
      if (!active(state)) return

      if (isDispatchTool(tool)) {
        const prompt = extractDispatchText(input, output)
        if (!isUsefulObjective(prompt)) {
          return deny("PIPELINE KICKOFF: filler, wait, poll, and status-only dispatches are forbidden.")
        }
        if (!isIsolatedDispatchPrompt(prompt)) {
          return deny(
            "PIPELINE KICKOFF: dispatch must create/use an isolated git worktree and explicitly " +
            `promise never to edit the frozen tested checkout ${state.tested_worktree}.`,
          )
        }
        if (!candidateBatchIsValid(state)) {
          return deny(
            "PIPELINE KICKOFF: frozen candidate batch integrity check failed; " +
            "do not dispatch mutated receipt state.",
          )
        }
        const key = dispatchKey(prompt)
        if (state.dispatched_keys.includes(key)) {
          return deny(`PIPELINE KICKOFF: duplicate task dispatch rejected (${key}).`)
        }
        const candidate = state.candidates.find(item =>
          item.key === key && item.dispatch_prompt === prompt
        )
        if (!candidate) {
          return deny(
            "PIPELINE KICKOFF: prompt is not an exact member of the frozen candidate batch; " +
            "zero candidates is valid and must not be filled with invented work.",
          )
        }
        const existing = Math.max(0, Math.floor(Number(state.existing_in_flight) || 0))
        // enforce-multitask is registered before this plugin and has already
        // counted the current dispatch attempt. Subtract that attempt while
        // retaining any workers that appeared after pipeline launch.
        const liveBeforeAttempt = Math.max(0, estimatedInFlight() - 1)
        const occupied = Math.max(
          existing + state.dispatched_keys.length,
          liveBeforeAttempt,
        )
        if (occupied >= PIPELINE_AGENT_CAP) {
          return deny(
            `PIPELINE KICKOFF: ${PIPELINE_AGENT_CAP}-agent cap reached; wait for useful work to finish.`,
          )
        }
        state.dispatched_keys.push(key)
        writeState(state)
        return
      }

      if (tool === "edit" || tool === "write") {
        const filePath = extractFilePath(input, output)
        if (isInsideTestedCheckout(filePath, state.tested_worktree)) {
          return deny(
            `PIPELINE CHECKOUT FROZEN: ${state.tested_worktree} @ ${state.tested_ref} is under test. ` +
            "Move implementation work to an isolated worktree.",
          )
        }
        return
      }

      if (tool === "bash") {
        const target = extractMakeTarget(command)
        if (!target || !FROZEN_BASH_ALLOWLIST.has(target)) {
          return deny(
            `PIPELINE CHECKOUT FROZEN: make target ${target || "unknown"} is not read-only. ` +
            "Use a dispatched isolated worktree while the long pipeline runs.",
          )
        }
      }
    } catch {
      // Fail open: orchestration support must never wedge the editor.
      return
    }
  },
  "tool.execute.after": async (input: any, output: any) => {
    if (isSubagent()) return
    try {
      if (process.env.GLUDD_PIPELINE_KICKOFF_ENFORCE === "0") return
      const command = extractBashCommand(input, output)
      if (String(input?.tool || "") !== "bash" || !isLongPipelineLaunch(command)) return
      const state = readState()
      if (!state || state.status !== "launching") return
      const exitCode = extractExitCode(output)
      state.status = exitCode !== null && exitCode !== 0 ? "failed" : "running"
      writeState(state)
      if (state.status === "running") exposeKickoff(output, state)
    } catch {
      // Fail open.
    }
  },
}

export default (({ }) => {
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-pipeline-kickoff ` +
      `tool.execute.before+tool.execute.after pid=${process.pid}\n`,
      "utf8",
    )
  } catch {}
  return {
    "tool.execute.before": async (input: any, output: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("pipeline-kickoff", defaultImpl)
      const hook = impl["tool.execute.before"]
      return hook ? await hook(input, output) : undefined
    },
    "tool.execute.after": async (input: any, output: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("pipeline-kickoff", defaultImpl)
      const hook = impl["tool.execute.after"]
      return hook ? await hook(input, output) : undefined
    },
  }
}) satisfies Plugin
