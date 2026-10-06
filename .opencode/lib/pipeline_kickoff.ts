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

export interface PipelineCandidateScope {
  kind: "repository" | "milestone" | "ambiguous"
  label: string
  range: string
  raw_count: number
  eligible_count: number
}

export interface ScopedPipelineCandidates {
  candidates: unknown[]
  scope: PipelineCandidateScope
}

type CandidateRecord = Record<string, unknown>

const RELEASE_MILESTONE_HINT_RE =
  /\bv\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\s+milestone[^\r\n]{0,160}\bexact\s+task\s+set\b/gi
const RELEASE_MILESTONE_RE =
  /\b(v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\s+milestone\s+is\s+the\s+exact\s+task\s+set\s+([A-Za-z]+\d+)\.(\d+)\s*[-\u2013]\s*([A-Za-z]+\d+)\.(\d+)/gi
const CANDIDATE_TASK_ID_RE = /\b([A-Z][A-Z0-9]*\d)\.(\d+)(?:\.\d+)*\b/g
const LEDGER_TASK_RE =
  /^\s*[-*]\s+\[([ xX])\]\s+([A-Z][A-Z0-9]*\d)\.(\d+)(?:\.\d+)*\b(.*)$/gm
const TASK_STATUS_RE = /\bstatus:\s*([^\s|,;]+)/i
const COMPLETED_TASK_STATUSES = new Set(["complete", "completed", "done"])
const CANDIDATE_SCOPE_FIELDS = Object.freeze([
  "id",
  "task_id",
  "objective",
  "task_item",
  "content",
  "title",
  "text",
  "command",
  "description",
])

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

function candidateScopeText(value: unknown): string {
  if (typeof value === "string") return value
  const record = asRecord(value)
  if (record === null) return ""
  return CANDIDATE_SCOPE_FIELDS
    .map(field => record[field])
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .join("\n")
}

function candidateIsInMilestone(
  candidate: unknown,
  prefix: string,
  start: number,
  end: number,
  activeRoots: ReadonlySet<string>,
): boolean {
  const references = Array.from(candidateScopeText(candidate).matchAll(CANDIDATE_TASK_ID_RE))
  if (references.length === 0) return false
  return references.every(reference =>
    reference[1].toLowerCase() === prefix.toLowerCase() &&
    Number(reference[2]) >= start &&
    Number(reference[2]) <= end &&
    activeRoots.has(`${reference[1].toLowerCase()}.${Number(reference[2])}`)
  )
}

function milestoneTaskRoots(
  tasksLedger: string,
  prefix: string,
  start: number,
  end: number,
): { defined: Set<string>; active: Set<string> } {
  const defined = new Set<string>()
  const active = new Set<string>()
  for (const task of tasksLedger.matchAll(LEDGER_TASK_RE)) {
    const taskPrefix = task[2]
    const taskNumber = Number(task[3])
    if (
      taskPrefix.toLowerCase() !== prefix.toLowerCase() ||
      !Number.isSafeInteger(taskNumber) ||
      taskNumber < start ||
      taskNumber > end
    ) {
      continue
    }
    const root = `${taskPrefix.toLowerCase()}.${taskNumber}`
    defined.add(root)
    const status = task[4].match(TASK_STATUS_RE)?.[1]
      ?.toLowerCase()
      .replace(/[|,;.]+$/, "")
    const checked = task[1].toLowerCase() === "x"
    if (!checked || (status !== undefined && !COMPLETED_TASK_STATUSES.has(status))) {
      active.add(root)
    }
  }
  return { defined, active }
}

/**
 * Bind orchestration candidates to the one explicitly active release milestone.
 *
 * A repository without versioned milestone metadata keeps its ordinary task
 * behavior. Once release metadata is present, exactly one valid declaration is
 * required and every candidate must carry only currently open task IDs inside
 * that range. Missing, conflicting, or malformed release metadata yields an
 * empty batch; a fully completed historical declaration releases repository
 * candidates again.
 */
export function scopePipelineCandidates(
  rawCandidates: readonly unknown[],
  tasksLedger: string | null | undefined,
): ScopedPipelineCandidates {
  const raw = [...rawCandidates]
  if (tasksLedger === undefined) {
    return {
      candidates: raw,
      scope: {
        kind: "repository",
        label: "",
        range: "",
        raw_count: raw.length,
        eligible_count: raw.length,
      },
    }
  }
  if (tasksLedger === null) {
    return {
      candidates: [],
      scope: {
        kind: "ambiguous",
        label: "",
        range: "",
        raw_count: raw.length,
        eligible_count: 0,
      },
    }
  }

  const hints = tasksLedger.match(RELEASE_MILESTONE_HINT_RE) ?? []
  const declarations = Array.from(tasksLedger.matchAll(RELEASE_MILESTONE_RE))
  if (hints.length === 0) {
    return {
      candidates: raw,
      scope: {
        kind: "repository",
        label: "",
        range: "",
        raw_count: raw.length,
        eligible_count: raw.length,
      },
    }
  }
  if (hints.length !== 1 || declarations.length !== 1) {
    return {
      candidates: [],
      scope: {
        kind: "ambiguous",
        label: "",
        range: "",
        raw_count: raw.length,
        eligible_count: 0,
      },
    }
  }

  const declaration = declarations[0]
  const label = declaration[1]
  const prefix = declaration[2]
  const start = Number(declaration[3])
  const endPrefix = declaration[4]
  const end = Number(declaration[5])
  if (
    prefix.toLowerCase() !== endPrefix.toLowerCase() ||
    !Number.isSafeInteger(start) ||
    !Number.isSafeInteger(end) ||
    end < start
  ) {
    return {
      candidates: [],
      scope: {
        kind: "ambiguous",
        label: "",
        range: "",
        raw_count: raw.length,
        eligible_count: 0,
      },
    }
  }

  const taskRoots = milestoneTaskRoots(tasksLedger, prefix, start, end)
  if (taskRoots.defined.size === 0) {
    return {
      candidates: [],
      scope: {
        kind: "ambiguous",
        label: "",
        range: "",
        raw_count: raw.length,
        eligible_count: 0,
      },
    }
  }
  if (taskRoots.active.size === 0) {
    return {
      candidates: raw,
      scope: {
        kind: "repository",
        label: "",
        range: "",
        raw_count: raw.length,
        eligible_count: raw.length,
      },
    }
  }

  const candidates = raw.filter(candidate =>
    candidateIsInMilestone(candidate, prefix, start, end, taskRoots.active)
  )
  return {
    candidates,
    scope: {
      kind: "milestone",
      label,
      range: `${prefix}.${start}-${prefix}.${end}`,
      raw_count: raw.length,
      eligible_count: candidates.length,
    },
  }
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
  if (
    /\b(?:do\s+not|don't|never)\s+(?:create(?:\s+and\s+use)?|use)\b[^.\n]{0,80}\bisolated\s+(?:git\s+)?worktree\b/i
      .test(prompt)
  ) {
    return false
  }
  return /\bcreate\s+and\s+use\s+an?\s+isolated\s+(?:git\s+)?worktree\b/i.test(prompt) &&
    /\b(?:never|do\s+not|don't)\s+edit\b[^.\n]*(?:frozen|tested|main)\s+checkout\b/i.test(prompt)
}

export function dispatchKey(prompt: string): string {
  const canonical = normalize(prompt)
    .replace(/\[pipeline-task:[^\]]+\]\s*/gi, "")
    .replace(/\/tmp\/[a-z0-9_./-]+/gi, "<worktree>")
    .replace(/\b[0-9a-f]{40}\b/gi, "<ref>")
  return `task:${createHash("sha256").update(canonical).digest("hex")}`
}

/** Bind the frozen candidate batch to its checkout, ref, prompts, and hashes. */
export function candidateBatchDigest(
  testedRef: string,
  testedWorktree: string,
  candidates: readonly PipelineCandidate[],
): string {
  const payload = {
    tested_ref: testedRef,
    tested_worktree: testedWorktree,
    candidates: candidates.map(candidate => ({
      id: candidate.id,
      objective: candidate.objective,
      files: candidate.files,
      key: candidate.key,
      dispatch_prompt: candidate.dispatch_prompt ?? "",
    })),
  }
  return createHash("sha256").update(JSON.stringify(payload)).digest("hex")
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
  scope?: PipelineCandidateScope,
): string {
  const lines = [
    "PIPELINE PARALLEL KICKOFF",
    `Frozen tested checkout: ${testedWorktree} @ ${testedRef}`,
  ]
  if (scope?.kind === "milestone") {
    lines.push(
      `Candidate scope: ${scope.label} ${scope.range} ` +
      `(${scope.eligible_count}/${scope.raw_count} source records eligible).`,
    )
  } else if (scope?.kind === "ambiguous") {
    lines.push("Candidate scope: ambiguous release metadata (fail-closed).")
  }
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
