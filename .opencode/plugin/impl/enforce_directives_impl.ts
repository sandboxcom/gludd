// Registered directive-enforcement implementation. The thin top-level wrapper
// imports this module while keeping OpenCode's discovered export surface small.
//
// THE FAILURE PATTERN (AGENTS.md session gap):
//   1. User: "E2E coverage must be >85% before beta.3" → agent stops at 68%
//   2. User supplies a numeric constraint → enforcement retains the constraint
// This plugin makes those violations structurally impossible.
//
// WHAT IT DOES:
//   * experimental.chat.messages.transform — ingests the latest user message so
//     standing directives do not depend on the assistant repeating them in a
//     tool prompt.
//   * tool.execute.before — tracks the active delegated-work estimate, blocks
//     mutation while a user-mandated pool is deficient, and honors explicit
//     user pauses before allowing another dispatch.
//   * experimental.text.complete — checks outgoing text against directives:
//     blocks completion claims ("final", "complete", "done") paired with a
//     directive subject when the numeric target is unmet; blocks "ALL" directive
//     violations.
//   * Hardcoded directives list bootstraps on first call from AGENTS.md session
//     rules (floor, TDD, gate-green, etc.) + user-provided directives from messages.
//   * FAIL-OPEN: any exception → allow (never wedge the editor).
//   * SUBAGENT SKIP: OPENCODE_SUBAGENT=1 → no enforcement.
//   * DISABLE: GLUDD_DIRECTIVE_ENFORCE=0
//   * HOT-RELOAD: proxy pattern from hot_reload.ts.
//
// STATE FILE: /tmp/gludd-active-directives.json
// ============================================================================

import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { loadHotModule, type HotModule } from "../../lib/hot_reload.ts"
import { clampDispatchCount } from "../../lib/multitask_config.ts"
import { isSubagent, reportAlive, writeHeartbeat, readJsonFile, writeJsonFile } from "../../lib/shared.ts"

const STATE_FILE = process.env.GLUDD_DIRECTIVE_STATE || "/tmp/gludd-active-directives.json"
const ENABLED = process.env.GLUDD_DIRECTIVE_ENFORCE !== "0"
const PROJECT_ROOT = process.env.GLUDD_PROJECT_ROOT || process.cwd()

// ── Hardcoded directives from AGENTS.md session rules ───────────────────────
// These are ALWAYS active. User messages can add more via pattern matching.
const HARDCODED_DIRECTIVES: Directive[] = [
  {
    id: "tdd-test-first",
    kind: "prohibition",
    subject: "write code without test",
    source: "AGENTS.md: TDD Policy",
    pattern: /\bnever write (?:code|implementation) without (?:a )?test\b/i,
  },
  {
    id: "gate-green-commit",
    kind: "prohibition",
    subject: "commit without green gate",
    source: "AGENTS.md: Commit-After-Green",
    pattern: /\bnever commit (?:without|before) (?:a )?green (?:gate|test)\b/i,
  },
  {
    id: "no-force-push",
    kind: "prohibition",
    subject: "force push",
    source: "AGENTS.md: Working Conventions",
    pattern: /\bnever force[- ]push\b/i,
  },
  {
    id: "disjoint-files-only",
    kind: "rule",
    subject: "concurrent edits",
    source: "AGENTS.md: Pipeline Orchestration",
    pattern: /\bconcurrent subagents .* disjoint files\b/i,
  },
]

interface Directive {
  id: string
  kind: "numeric" | "floor" | "completeness" | "prohibition" | "rule"
  subject: string
  target?: number
  source: string
  pattern: RegExp
  active: boolean
  created_ts: number
  updated_ts: number
}

interface DirectivePause {
  active: boolean
  reason: string
  created_ts: number
  updated_ts: number
}

interface DirectiveState {
  directives: Directive[]
  last_dispatch_count: number
  active_dispatch_count: number
  processed_result_ids: string[]
  last_dispatch_ts: number
  pause: DirectivePause | null
  pid: number
}

function freshState(): DirectiveState {
  return {
    directives: HARDCODED_DIRECTIVES.map(d => ({
      ...d,
      active: true,
      pattern: d.pattern,
      created_ts: 0,
      updated_ts: 0,
    })),
    last_dispatch_count: 0,
    active_dispatch_count: 0,
    processed_result_ids: [],
    last_dispatch_ts: 0,
    pause: null,
    pid: process.pid,
  }
}

function deserializeDirectives(raw: Record<string, unknown>): Directive[] {
  const stored = Array.isArray(raw.directives)
    ? raw.directives.filter((d: any) => d.id !== "floor-10").map((d: any) => ({
        id: String(d.id ?? ""),
        kind: String(d.kind ?? "rule") as Directive["kind"],
        subject: String(d.subject ?? ""),
        target: typeof d.target === "number"
          ? (String(d.kind ?? "") === "floor" ? clampDispatchCount(d.target) : d.target)
          : undefined,
        source: String(d.source ?? ""),
        pattern: d.pattern instanceof RegExp
          ? d.pattern
          : (typeof d.pattern === "string" ? new RegExp(d.pattern) : /(?:)/),
        active: Boolean(d.active ?? true),
        created_ts: typeof d.created_ts === "number" ? d.created_ts : 0,
        updated_ts: typeof d.updated_ts === "number" ? d.updated_ts : 0,
      }))
    : []
  const byId = new Map<string, Directive>()
  for (const directive of freshState().directives) byId.set(directive.id, directive)
  for (const directive of stored) byId.set(directive.id, directive)
  return [...byId.values()]
}

function loadState(): DirectiveState {
  try {
    if (fs.existsSync(STATE_FILE)) {
      const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
      const processChanged = typeof raw.pid === "number" && raw.pid !== process.pid
      const state: DirectiveState = {
        directives: deserializeDirectives(raw as Record<string, unknown>),
        last_dispatch_count: processChanged
          ? 0
          : (typeof raw.last_dispatch_count === "number" ? raw.last_dispatch_count : 0),
        active_dispatch_count: processChanged
          ? 0
          : (typeof raw.active_dispatch_count === "number"
              ? clampDispatchCount(raw.active_dispatch_count)
              : clampDispatchCount(typeof raw.last_dispatch_count === "number" ? raw.last_dispatch_count : 0)),
        processed_result_ids: Array.isArray(raw.processed_result_ids)
          ? raw.processed_result_ids.filter((value: unknown) => typeof value === "string").slice(-512)
          : [],
        last_dispatch_ts: typeof raw.last_dispatch_ts === "number" ? raw.last_dispatch_ts : 0,
        pause: raw.pause && typeof raw.pause === "object"
          ? {
              active: Boolean(raw.pause.active),
              reason: String(raw.pause.reason ?? "user-requested release completion"),
              created_ts: typeof raw.pause.created_ts === "number" ? raw.pause.created_ts : 0,
              updated_ts: typeof raw.pause.updated_ts === "number" ? raw.pause.updated_ts : 0,
            }
          : null,
        pid: typeof raw.pid === "number" ? raw.pid : process.pid,
      }
      if (processChanged) saveState(state)
      return state
    }
  } catch {}
  const s = freshState()
  saveState(s)
  return s
}

function saveState(s: DirectiveState): void {
  try {
    s.pid = process.pid
    const ser = {
      directives: s.directives.map(d => ({
        id: d.id, kind: d.kind, subject: d.subject, target: d.target,
        source: d.source, pattern: d.pattern.source,
        active: d.active, created_ts: d.created_ts, updated_ts: d.updated_ts,
      })),
      last_dispatch_count: s.last_dispatch_count,
      active_dispatch_count: s.active_dispatch_count,
      processed_result_ids: s.processed_result_ids.slice(-512),
      last_dispatch_ts: s.last_dispatch_ts,
      pause: s.pause,
      pid: s.pid,
    }
    fs.writeFileSync(STATE_FILE, JSON.stringify(ser), "utf8")
  } catch {}
}

// ── Directive extraction from user messages ─────────────────────────────────

const MUST_GT_NUMERIC_RE = /\b(must|should|need|have to|ensure|required)\s+(?:be|have|reach|hit|get\s+to)\s*[><=]?\s*(\d+)(?:%| percent)?\b/i
const MAINTAIN_FLOOR_RE = /\bmaintain\s+(?:a\s+)?(\d+)[- ]agent\s+floor\b/i
const SUBAGENT_POOL_COUNT_RE = /\b(\d+)\s*x?\s+(?:(?:currently|still)\s+)?(?:subagents?\s+(?:running|active)|(?:running|active)\s+subagents?|subagents?|agent\s+slots?|slots?)\b/i
const SUBAGENT_POOL_INTENT_RE = /\b(?:keep|maintain|ensure|use\s+spare\s+slots?|fill(?:ed)?\s+(?:the\s+)?slots?|if\s+.{0,50}\b(?:less|fewer)\b)\b/i
const PAUSE_SUBAGENTS_RE = /\bwait\s+until\s+(.{1,100}?)\s+(?:is\s+)?(?:finished|complete|completed)\s+to\s+resume\s+(?:the\s+)?subagent\s+work\b/i
const RESUME_SUBAGENTS_RE = /\b(?:finished|complete|completed)\b.{0,80}\bresume\s+(?:the\s+)?subagent\s+work\b/i
const ALL_COMPLETENESS_RE = /\b(?:ensure|make\s+sure|verify)\s+(?:that\s+)?(?:all|every)\s+(.+?)\s+(?:is|are|must\s+be)\s+(.+?)(?:\.|$)/i
const PROHIBITION_RE = /\b(?:do\s+not|never|don't|NEVER|DO\s+NOT)\s+(.+?)(?:\.|!|$)/i

function extractDirectivesFromText(text: string): Partial<Directive>[] {
  const found: Partial<Directive>[] = []

  const numericMatch = text.match(MUST_GT_NUMERIC_RE)
  if (numericMatch) {
    found.push({
      kind: "numeric",
      subject: text.substring(0, 100).replace(/\n/g, " "),
      target: parseInt(numericMatch[2], 10),
      source: "user-directive",
      active: true,
    })
  }

  const floorMatch = text.match(MAINTAIN_FLOOR_RE)
  const poolMatch = text.match(SUBAGENT_POOL_COUNT_RE)
  const explicitFloorTarget = floorMatch
    ? clampDispatchCount(parseInt(floorMatch[1], 10))
    : (poolMatch && SUBAGENT_POOL_INTENT_RE.test(text)
        ? clampDispatchCount(parseInt(poolMatch[1], 10))
        : undefined)
  if (explicitFloorTarget !== undefined) {
    found.push({
      kind: "floor",
      subject: "subagent floor",
      target: explicitFloorTarget,
      source: "user-directive",
      active: true,
    })
    return found // floor overrides less-specific numeric
  }

  const allMatch = text.match(ALL_COMPLETENESS_RE)
  if (allMatch) {
    found.push({
      kind: "completeness",
      subject: allMatch[1].trim(),
      target: undefined,
      source: "user-directive",
      active: true,
    })
  }

  const prohMatch = text.match(PROHIBITION_RE)
  if (prohMatch) {
    found.push({
      kind: "prohibition",
      subject: prohMatch[1].trim(),
      source: "user-directive",
      active: true,
    })
  }

  return found
}

function latestUserText(output: unknown): string {
  const messages = (output as any)?.messages
  if (!Array.isArray(messages)) return ""
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index]
    const role = String(message?.info?.role ?? message?.role ?? "")
    if (role !== "user") continue
    const parts = Array.isArray(message?.parts) ? message.parts : []
    return parts
      .filter((part: any) => part?.type === "text" && typeof part?.text === "string")
      .map((part: any) => part.text)
      .join("\n")
  }
  return ""
}

function completedDispatchResultIds(output: unknown): string[] {
  const messages = (output as any)?.messages
  if (!Array.isArray(messages)) return []
  const resultIds: string[] = []
  for (const message of messages) {
    const parts = Array.isArray(message?.parts) ? message.parts : []
    for (const part of parts) {
      if (part?.type !== "tool" || !isDispatchTool(String(part?.tool ?? ""))) continue
      const status = String(part?.state?.status ?? "")
      if (status !== "completed" && status !== "error") continue
      const identity = String(part?.callID ?? part?.id ?? "")
      if (identity) resultIds.push(identity)
    }
  }
  return [...new Set(resultIds)]
}

function ingestUserDirective(s: DirectiveState, text: string): boolean {
  let changed = false
  const now = Date.now()
  const pauseMatch = text.match(PAUSE_SUBAGENTS_RE)
  if (pauseMatch) {
    const reason = pauseMatch[1].trim().replace(/[.;:,]+$/, "")
    s.pause = {
      active: true,
      reason,
      created_ts: s.pause?.created_ts || now,
      updated_ts: now,
    }
    changed = true
  } else if (RESUME_SUBAGENTS_RE.test(text) && s.pause?.active) {
    s.pause = null
    changed = true
  }

  for (const fd of extractDirectivesFromText(text)) {
    const dedupId = `${fd.kind}-${fd.subject}`.replace(/\s+/g, "-").toLowerCase().substring(0, 60)
    const existing = s.directives.find(d => d.id === dedupId)
    if (existing) {
      existing.kind = fd.kind as Directive["kind"]
      existing.subject = fd.subject ?? existing.subject
      existing.target = fd.target
      existing.source = fd.source ?? existing.source
      existing.active = true
      existing.updated_ts = now
    } else {
      s.directives.push({
        id: dedupId,
        kind: fd.kind as Directive["kind"],
        subject: fd.subject ?? "",
        target: fd.target,
        source: fd.source ?? "user-directive",
        pattern: /(?:)/,
        active: true,
        created_ts: now,
        updated_ts: now,
      })
    }
    changed = true
  }
  return changed
}

function releasePauseSatisfied(pause: DirectivePause): boolean {
  const release = pause.reason.match(/\bv?\d+\.\d+\.\d+(?:[-.][0-9a-z]+)*\b/i)?.[0]
  if (!release) return false
  try {
    const taskText = fs.readFileSync(path.join(PROJECT_ROOT, "TASKS.md"), "utf8")
    const relevant = taskText.split(/\r?\n/).filter(line => {
      const lower = line.toLowerCase()
      return lower.includes(release.toLowerCase()) && /\b(?:release|deploy|promot)/i.test(line)
    })
    const hasCompletedTerminalEvidence = relevant.some(line => /^\s*-\s*\[[xX]\]/.test(line))
    const hasOpenTerminalWork = relevant.some(line => /^\s*-\s*\[\s\]/.test(line))
    return hasCompletedTerminalEvidence && !hasOpenTerminalWork
  } catch {
    return false
  }
}

function resultMarkerCount(text: string): number {
  const structured = text.match(/<task_result\b/gi)
  if (structured) return structured.length
  return text.match(/\b(?:task|subagent|workflow)[ _-]+result\b/gi)?.length ?? 0
}

// ── Response text checks ────────────────────────────────────────────────────

const COMPLETION_CLAIM_RE = /\b(?:final|complete\w*|done|finished|all\s+done|ready)\b/i
const COVERAGE_SUBJECT_RE = /\b(?:e2e|coverage|test\s+cover\w*|end[- ]to[- ]end)\b/i
const PERCENT_RE = /(\d+)\s*%/g

function checkNumericDirective(text: string, d: Directive): string | null {
  if (!COMPLETION_CLAIM_RE.test(text)) return null
  if (!COVERAGE_SUBJECT_RE.test(text)) return null

  const percents = [...text.matchAll(PERCENT_RE)]
  if (percents.length === 0) {
    if (d.target !== undefined) {
      return `DIRECTIVE VIOLATION: "${d.subject}" target is >${d.target}%, but response claims completion without citing a coverage number.`
    }
    return null
  }

  const claimedMax = Math.max(...percents.map(m => parseInt(m[1], 10)))
  if (d.target !== undefined && claimedMax < d.target) {
    return `DIRECTIVE VIOLATION: "${d.subject}" requires >${d.target}%, but response claims ${claimedMax}%. Target not met.`
  }
  return null
}

function checkCompletenessDirective(_text: string, d: Directive): string | null {
  if (!COMPLETION_CLAIM_RE.test(_text)) return null
  const subjLC = d.subject.toLowerCase()
  const textLC = _text.toLowerCase()
  if (!textLC.includes(subjLC.substring(0, 10))) return null
  const hasEvidence = /\b\d+\s*(?:\/\s*\d+|passed|green|verified)\b/i.test(_text)
  if (!hasEvidence) {
    return `DIRECTIVE VIOLATION: "ensure ALL ${d.subject}" directive active, but response lacks completeness evidence.`
  }
  return null
}

function checkProhibitionDirective(_text: string, d: Directive): string | null {
  const subjWords = d.subject.toLowerCase().split(/\s+/).filter(w => w.length > 2)
  if (subjWords.length === 0) return null
  const textLC = _text.toLowerCase()
  const matchCount = subjWords.filter(w => textLC.includes(w)).length
  if (matchCount >= subjWords.length * 0.7) {
    return `DIRECTIVE VIOLATION: "${d.subject}" is forbidden, but response mentions it.`
  }
  return null
}

// ── Extract user message from system prompt / input for directive mining ───

function extractUserText(input: any): string {
  try {
    if (typeof input === "string") return input
    if (typeof input?.messages === "string") return input.messages
    if (Array.isArray(input?.messages)) {
      return input.messages
        .filter((m: any) => m?.role === "user")
        .map((m: any) => typeof m.content === "string" ? m.content : "")
        .join("\n")
    }
    const args = input?.tool_input ?? input?.args ?? {}
    if (typeof args?.prompt === "string") return args.prompt
    if (typeof args?.text === "string") return args.text
    if (typeof args?.content === "string") return args.content
    if (typeof args?.message === "string") return args.message
    return ""
  } catch {
    return ""
  }
}

function isDispatchTool(tool: string): boolean {
  return ["task", "agent", "workflow", "spawn_agent", "followup_task"].includes(tool)
}

// ── DEFAULT IMPLEMENTATION ──────────────────────────────────────────────────

const defaultImpl: HotModule = {
  "experimental.chat.messages.transform": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    if (!ENABLED) return output
    try {
      const userText = latestUserText(output)
      const state = loadState()
      let changed = userText ? ingestUserDirective(state, userText) : false
      const alreadyProcessed = new Set(state.processed_result_ids)
      const newResults = completedDispatchResultIds(output).filter(
        identity => !alreadyProcessed.has(identity)
      )
      if (newResults.length > 0) {
        state.active_dispatch_count = Math.max(
          0,
          state.active_dispatch_count - newResults.length,
        )
        state.last_dispatch_count = state.active_dispatch_count
        state.processed_result_ids = [
          ...state.processed_result_ids,
          ...newResults,
        ].slice(-512)
        changed = true
      }
      if (changed) saveState(state)
      return output
    } catch {
      return output
    }
  },

  "tool.execute.before": async (input: any, output: any) => {
    if (isSubagent()) return
    if (!ENABLED) return
    try {
      const tool = input?.tool ?? input?.tool_name ?? ""

      // Compatibility ingestion for callers that still include messages in a
      // tool hook. The canonical source is messages.transform above.
      const userText = extractUserText(input)
      if (userText && userText.length > 10) {
        const state = loadState()
        if (ingestUserDirective(state, userText)) saveState(state)
      }

      // Dispatches are denied while the user has explicitly paused delegated
      // work. A later explicit resume message clears this durable pause.
      if (isDispatchTool(tool)) {
        const s = loadState()
        if (s.pause?.active && releasePauseSatisfied(s.pause)) {
          s.pause = null
          saveState(s)
        }
        if (s.pause?.active) {
          return {
            permissionDecision: "deny",
            message: `DIRECTIVE PAUSE: subagent dispatch is paused until ${s.pause.reason} is finished.`,
          }
        }
        s.active_dispatch_count = clampDispatchCount(s.active_dispatch_count + 1)
        s.last_dispatch_count = s.active_dispatch_count
        s.last_dispatch_ts = Date.now()
        saveState(s)
        return
      }

      const s = loadState()
      const floorDirective = s.directives.find(d => d.kind === "floor" && d.active)
      if (!s.pause?.active && floorDirective && floorDirective.target !== undefined) {
        const floorTarget = clampDispatchCount(floorDirective.target)
        const active = clampDispatchCount(s.active_dispatch_count)
        const deficit = Math.max(0, floorTarget - active)
        if (deficit > 0) {
          const normalizedTool = String(tool).toLowerCase()
          const cmd = String(
            output?.args?.command ?? input?.args?.command ?? input?.tool_input?.command ?? ""
          )
          const readOnlyTool = ["read", "grep", "glob", "list_agents"].includes(normalizedTool)
          const readOnlyCommand = /\b(git-status|git-log|git-diff|ci-verdict|gate-status|verify-state|disk|git-staged|git-show|active-work-status|ps)\b/.test(cmd)
          if (!readOnlyTool && !(normalizedTool === "bash" && readOnlyCommand)) {
            return {
              permissionDecision: "deny",
              message: `DIRECTIVE VIOLATION: active subagent pool ${active}/${floorTarget}; dispatch ${deficit} replacement agent(s) before continuing mutation work.`,
            }
          }
        }
      }
    } catch {
      // fail-open: never wedge
    }
  },

  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    if (!ENABLED) return output
    if (isSubagent()) return output
    try {
      const out = output as { text?: string }
      const text = out?.text ?? ""
      if (!text) return output

      const s = loadState()
      const completedCount = resultMarkerCount(text)
      if (completedCount > 0) {
        s.active_dispatch_count = Math.max(0, s.active_dispatch_count - completedCount)
        s.last_dispatch_count = s.active_dispatch_count
        saveState(s)
      }
      const activeDirectives = s.directives.filter(d => d.active)

      for (const d of activeDirectives) {
        let blockMsg: string | null = null

        switch (d.kind) {
          case "numeric":
            blockMsg = checkNumericDirective(text, d)
            break
          case "completeness":
            blockMsg = checkCompletenessDirective(text, d)
            break
          case "prohibition":
            blockMsg = checkProhibitionDirective(text, d)
            break
          case "floor":
            // Block completion claims paired with "floor" subject when dispatch count is zero
            if (COMPLETION_CLAIM_RE.test(text) && /\bagent\b.*\bfloor\b|\bfloor\b.*\bagent\b/i.test(text)) {
              const floorTarget = clampDispatchCount(d.target ?? 0)
              if (!s.pause?.active && floorTarget > 0 && s.active_dispatch_count < floorTarget) {
                blockMsg = `DIRECTIVE VIOLATION: floor=${floorTarget} agents required, but only ${s.active_dispatch_count} active. Resuming work in progress is not completion.`
              }
            }
            break
        }

        if (blockMsg) {
          return { ...(output as Record<string, unknown>), text: blockMsg + "\n\n" + text }
        }
      }

      return output
    } catch {
      return output
    }
  },
}

// ── PROXY PLUGIN (hot-reload aware) ─────────────────────────────────────────

export default (({}) => {
  return {
    "experimental.chat.messages.transform": async (_input: unknown, output: unknown) => {
      if (!ENABLED) return output
      if (isSubagent()) return output
      reportAlive("enforce-directives")
      writeHeartbeat("enforce-directives")
      const impl = loadHotModule("directives", defaultImpl)
      const fn = impl["experimental.chat.messages.transform"]
      return fn ? await fn(_input, output) : output
    },
    "tool.execute.before": async (input: unknown, _output: unknown) => {
      if (isSubagent()) return
      if (!ENABLED) return
      reportAlive("enforce-directives")
      writeHeartbeat("enforce-directives")
      const impl = loadHotModule("directives", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, _output) : undefined
    },
    "experimental.text.complete": async (_input: unknown, output: unknown) => {
      if (!ENABLED) return output
      if (isSubagent()) return output
      const impl = loadHotModule("directives", defaultImpl)
      const fn = impl["text.complete"] || impl["experimental.text.complete"]
      return fn ? await fn(_input, output) : output
    },
  }
}) satisfies Plugin
