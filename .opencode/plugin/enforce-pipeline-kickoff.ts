import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { execFileSync } from "node:child_process"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import {
  getProjectRoot,
  isDispatchTool,
  isSubagent,
  readJsonFile,
  reportAlive,
  writeJsonFile,
} from "../lib/shared.ts"
import {
  PIPELINE_AGENT_CAP,
  buildIsolatedDispatchPrompt,
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
  version: 1
  status: PipelineStatus
  pipeline_target: string
  command: string
  tested_ref: string
  tested_worktree: string
  created_at: number
  updated_at: number
  existing_in_flight?: number
  candidates: PipelineCandidate[]
  dispatched_keys: string[]
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

function extractBashCommand(...sources: unknown[]): string {
  for (const source of sources) {
    if (!source || typeof source !== "object") continue
    const envelope = source as {
      command?: unknown
      args?: { command?: unknown }
      tool_input?: { command?: unknown }
      input?: { command?: unknown; args?: { command?: unknown } }
    }
    for (const value of [
      envelope.args?.command,
      envelope.command,
      envelope.tool_input?.command,
      envelope.input?.args?.command,
      envelope.input?.command,
    ]) {
      if (typeof value === "string") return value
    }
  }
  return ""
}

function replaceBashCommand(input: unknown, output: unknown, command: string): void {
  const candidates = [output, input]
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== "object") continue
    const envelope = candidate as { args?: Record<string, unknown>; command?: unknown }
    if (envelope.args && typeof envelope.args === "object") {
      envelope.args.command = command
      return
    }
    if (typeof envelope.command === "string") {
      envelope.command = command
      return
    }
  }
}

function extractDispatchText(input: unknown, output: unknown): string {
  const parts: string[] = []
  for (const source of [input, output]) {
    if (!source || typeof source !== "object") continue
    const envelope = source as {
      prompt?: unknown
      description?: unknown
      message?: unknown
      args?: { prompt?: unknown; description?: unknown; message?: unknown }
      input?: { prompt?: unknown; description?: unknown; message?: unknown }
    }
    for (const value of [
      envelope.prompt,
      envelope.description,
      envelope.message,
      envelope.args?.prompt,
      envelope.args?.description,
      envelope.args?.message,
      envelope.input?.prompt,
      envelope.input?.description,
      envelope.input?.message,
    ]) {
      if (typeof value === "string" && value.trim()) parts.push(value.trim())
    }
  }
  return Array.from(new Set(parts)).join("\n")
}

function extractFilePath(input: unknown, output: unknown): string {
  for (const source of [input, output]) {
    if (!source || typeof source !== "object") continue
    const envelope = source as {
      path?: unknown
      filePath?: unknown
      args?: { path?: unknown; filePath?: unknown }
      tool_input?: { path?: unknown; filePath?: unknown }
    }
    for (const value of [
      envelope.args?.filePath,
      envelope.args?.path,
      envelope.filePath,
      envelope.path,
      envelope.tool_input?.filePath,
      envelope.tool_input?.path,
    ]) {
      if (typeof value === "string" && value.trim()) return value.trim()
    }
  }
  return ""
}

function isInsideTestedCheckout(filePath: string, testedWorktree: string): boolean {
  if (!filePath) return true
  const absolute = path.isAbsolute(filePath)
    ? path.resolve(filePath)
    : path.resolve(testedWorktree, filePath)
  const relative = path.relative(path.resolve(testedWorktree), absolute)
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative))
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
    .map(candidate => ({
      ...candidate,
      dispatch_prompt: buildIsolatedDispatchPrompt(candidate, testedRef, root),
    }))
  const frozenCommand = withFrozenRef(command, testedRef)
  const now = Date.now()
  return {
    version: 1,
    status: "launching",
    pipeline_target: extractMakeTarget(frozenCommand) || "unknown",
    command: frozenCommand,
    tested_ref: testedRef,
    tested_worktree: root,
    created_at: now,
    updated_at: now,
    existing_in_flight: inFlight,
    candidates: batch,
    dispatched_keys: [],
  }
}

function statusPathFor(state: PipelineKickoffState): string | null {
  if (process.env.GLUDD_PIPELINE_STATUS_PATH) return process.env.GLUDD_PIPELINE_STATUS_PATH
  if (state.pipeline_target === "ship-async") return path.join(state.tested_worktree, ".ship-status")
  if (state.pipeline_target.includes("gate")) return path.join(state.tested_worktree, ".gate-status")
  return null
}

function refreshTerminalState(state: PipelineKickoffState | null): PipelineKickoffState | null {
  if (!state || !["launching", "running"].includes(state.status)) return state
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
    if (/^(?:PASS\b|SHIP PASS\b)/m.test(content)) {
      state.status = "complete"
      writeState(state)
    } else if (/^(?:FAIL\b|SHIP FAIL\b)/m.test(content)) {
      state.status = "failed"
      writeState(state)
    }
  } catch {
    // A launcher writes status asynchronously; absence during startup is normal.
  }
  return state
}

function active(state: PipelineKickoffState | null): state is PipelineKickoffState {
  return state !== null && ["launching", "running"].includes(state.status)
}

function deny(message: string): { permissionDecision: "deny"; message: string } {
  return { permissionDecision: "deny", message }
}

function extractExitCode(output: unknown): number | null {
  if (!output || typeof output !== "object") return null
  const value = output as {
    exitCode?: unknown
    exit_code?: unknown
    metadata?: { exitCode?: unknown; exit_code?: unknown }
    result?: { exitCode?: unknown; exit_code?: unknown }
  }
  for (const candidate of [
    value.metadata?.exitCode,
    value.metadata?.exit_code,
    value.result?.exitCode,
    value.result?.exit_code,
    value.exitCode,
    value.exit_code,
  ]) {
    if (typeof candidate === "number") return candidate
  }
  return null
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
        const key = dispatchKey(prompt)
        if (state.dispatched_keys.includes(key)) {
          return deny(`PIPELINE KICKOFF: duplicate task dispatch rejected (${key}).`)
        }
        const occupied = Math.max(0, Math.floor(Number(state.existing_in_flight) || 0)) +
          state.dispatched_keys.length
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
