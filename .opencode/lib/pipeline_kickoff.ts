import { createHash } from "node:crypto"

/** A pipeline kickoff may use fewer workers, but never more than three. */
export const PIPELINE_AGENT_CAP = 3

export function projectKickoffStatePath(projectRoot: string): string {
  const identity = createHash("sha256")
    .update(projectRoot.trim().replace(/\/+$/, ""))
    .digest("hex")
    .slice(0, 16)
  return `/tmp/gludd-pipeline-kickoff-${identity}.json`
}

const LONG_PIPELINE_TARGETS = new Set([
  "gate-async",
  "gate-background",
  "gate-all-background",
  "ship-async",
  "test-ci-shards-parallel-bg",
])

const NON_WORK_PATTERNS: readonly RegExp[] = Object.freeze([
  /\b(?:wait|poll|watch)\b.*\b(?:ci|gate|pipeline|build|deploy)/i,
  /\bcheck\s+(?:ci|gate|pipeline|build|deploy)\s+status\b/i,
  /\b(?:ci|gate|pipeline)\s+status\s+check\b/i,
  /\b(?:filler|placeholder|busywork)\b/i,
  /\bkeep\s+(?:an?\s+)?(?:agent|slot|worker)\s+busy\b/i,
  /^\s*(?:research tasks?|status check)\s*$/i,
])

export interface PipelineCandidate {
  id: string
  objective: string
  files: string[]
  key: string
  dispatch_prompt?: string
}

type CandidateRecord = Record<string, unknown>

function asRecord(value: unknown): CandidateRecord | null {
  if (typeof value === "string") return { content: value }
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  return value as CandidateRecord
}

function firstString(record: CandidateRecord, names: readonly string[]): string {
  for (const name of names) {
    const value = record[name]
    if (typeof value === "string" && value.trim()) return value.trim()
  }
  return ""
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .map(item => item.trim())
}

function normalize(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, " ")
}

function safeId(value: string): string {
  const cleaned = value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")
  return cleaned.slice(0, 48) || "independent-work"
}

export function isUsefulObjective(objective: string): boolean {
  const value = objective.trim()
  if (value.length < 8) return false
  return !NON_WORK_PATTERNS.some(pattern => pattern.test(value))
}

/**
 * Select the first dependency-ready, pairwise file-disjoint candidates.
 *
 * This deliberately follows the repository OrchestrationPlanner contract
 * (files are exclusive resources; dependencies must already be complete)
 * without inventing tasks when the existing enforcement state has none.
 */
export function consolidateCandidateBatch(
  rawCandidates: readonly unknown[],
  estimatedInFlight = 0,
): PipelineCandidate[] {
  const available = Math.max(
    0,
    PIPELINE_AGENT_CAP - Math.max(0, Math.floor(Number(estimatedInFlight) || 0)),
  )
  if (available === 0) return []

  const records = rawCandidates.map(asRecord).filter((item): item is CandidateRecord => item !== null)
  const completedIds = new Set<string>()
  for (const record of records) {
    const status = normalize(firstString(record, ["status"]))
    if (["done", "complete", "completed", "closed"].includes(status)) {
      const id = firstString(record, ["id", "task_id", "key"])
      if (id) completedIds.add(id)
    }
  }

  const selected: PipelineCandidate[] = []
  const selectedFiles = new Set<string>()
  const seen = new Set<string>()

  for (const record of records) {
    if (selected.length >= available) break
    const status = normalize(firstString(record, ["status"]))
    if (status && !["pending", "in_progress", "in-progress", "open", "todo"].includes(status)) {
      continue
    }
    if (record.independent === false) continue

    const objective = firstString(record, [
      "objective",
      "task_item",
      "content",
      "title",
      "text",
      "command",
      "description",
    ])
    if (!isUsefulObjective(objective)) continue

    const dependencies = stringList(record.depends_on ?? record.dependencies)
    if (dependencies.some(dependency => !completedIds.has(dependency))) continue

    const objectiveKey = normalize(objective)
    if (seen.has(objectiveKey)) continue

    const files = Array.from(new Set([
      ...stringList(record.files),
      ...stringList(record.resources),
      ...stringList(record.paths),
    ])).sort()
    if (files.some(file => selectedFiles.has(file))) continue

    const rawId = firstString(record, ["id", "task_id", "key"]) || objective
    const id = safeId(rawId)
    const key = createHash("sha256").update(objectiveKey).digest("hex").slice(0, 20)
    selected.push({ id, objective, files, key })
    seen.add(objectiveKey)
    for (const file of files) selectedFiles.add(file)
  }

  return selected
}

export function buildIsolatedDispatchPrompt(
  candidate: PipelineCandidate,
  testedRef: string,
  testedWorktree: string,
): string {
  const scope = candidate.files.length > 0
    ? ` Scope is limited to: ${candidate.files.join(", ")}.`
    : ""
  return [
    `[pipeline-task:${candidate.id}] ${candidate.objective}.${scope}`,
    `Create and use an isolated git worktree and feature branch from frozen ref ${testedRef}.`,
    `Never edit the frozen tested checkout: ${testedWorktree}.`,
    "Return a concrete tested deliverable committed on that feature branch.",
  ].join(" ")
}

export function isIsolatedDispatchPrompt(prompt: string): boolean {
  return /\bisolated\s+(?:git\s+)?worktree\b/i.test(prompt) &&
    /\b(?:never|do\s+not|don't)\s+edit\b[^.\n]*(?:frozen|tested|main)\s+checkout\b/i.test(prompt)
}

export function dispatchKey(prompt: string): string {
  const marker = prompt.match(/\[pipeline-task:([^\]]+)\]/i)
  if (marker) return `task:${safeId(marker[1])}`
  const canonical = normalize(prompt)
    .replace(/\/tmp\/[a-z0-9_./-]+/gi, "<worktree>")
    .replace(/\b[0-9a-f]{40}\b/gi, "<ref>")
  return `prompt:${createHash("sha256").update(canonical).digest("hex").slice(0, 20)}`
}

export function extractMakeTarget(command: string): string | null {
  const tokens = command.trim().split(/\s+/)
  if (tokens[0] !== "make") return null
  for (const token of tokens.slice(1)) {
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(token)) continue
    return token
  }
  return null
}

export function isLongPipelineLaunch(command: string): boolean {
  const target = extractMakeTarget(command)
  return target !== null && LONG_PIPELINE_TARGETS.has(target)
}

export function withFrozenRef(command: string, testedRef: string): string {
  const target = extractMakeTarget(command)
  if (!target || !["gate-async", "ship-async"].includes(target)) return command
  if (/(?:^|\s)REF=\S+/.test(command)) return command
  return `${command.trim()} REF=${testedRef}`
}

export function formatKickoffNotice(
  testedRef: string,
  testedWorktree: string,
  candidates: readonly PipelineCandidate[],
): string {
  const lines = [
    "PIPELINE PARALLEL KICKOFF",
    `Frozen tested checkout: ${testedWorktree} @ ${testedRef}`,
  ]
  if (candidates.length === 0) {
    lines.push("Independent candidate batch: 0 (valid; do not create filler work).")
  } else {
    lines.push(`Independent candidate batch: ${candidates.length}/${PIPELINE_AGENT_CAP}`)
    for (const candidate of candidates) {
      lines.push(`- ${candidate.dispatch_prompt ?? candidate.objective}`)
    }
  }
  return lines.join("\n")
}
