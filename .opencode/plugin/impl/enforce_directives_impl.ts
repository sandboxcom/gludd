// Dormant directive-enforcement implementation. Keep this below plugin/impl so
// OpenCode does not auto-discover an unregistered top-level plugin.
//
// THE FAILURE PATTERN (AGENTS.md session gap):
//   1. User: "E2E coverage must be >85% before beta.3" → agent stops at 68%
//   2. User: "maintain 10-agent floor at all times" → agent dispatches 3
// This plugin makes those violations structurally impossible.
//
// WHAT IT DOES:
//   * tool.execute.before — classifies incoming DISPATCH prompts against known
//     directives: blocks non-dispatch tools when active subagent floor is below
//     the directive-mandated minimum. Blocks commit/push with pending directives.
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
import { loadHotModule, type HotModule } from "../../lib/hot_reload.ts"
import { isSubagent, reportAlive, writeHeartbeat, readJsonFile, writeJsonFile } from "../../lib/shared.ts"

const STATE_FILE = process.env.GLUDD_DIRECTIVE_STATE || "/tmp/gludd-active-directives.json"
const ENABLED = process.env.GLUDD_DIRECTIVE_ENFORCE !== "0"

// ── Hardcoded directives from AGENTS.md session rules ───────────────────────
// These are ALWAYS active. User messages can add more via pattern matching.
const HARDCODED_DIRECTIVES: Directive[] = [
  {
    id: "floor-10",
    kind: "floor",
    subject: "subagent floor",
    target: 10,
    source: "AGENTS.md: 10-Agent Dispatch Floor",
    pattern: /\b(?:maintain|keep|floor)\b.*?\b(\d+)[- ]agent\b/i,
  },
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

interface DirectiveState {
  directives: Directive[]
  last_dispatch_count: number
  last_dispatch_ts: number
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
    last_dispatch_ts: 0,
    pid: process.pid,
  }
}

function isStale(raw: Record<string, unknown>): boolean {
  if (typeof raw.pid === "number" && raw.pid !== process.pid) return true
  return false
}

function loadState(): DirectiveState {
  try {
    if (fs.existsSync(STATE_FILE)) {
      const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
      if (isStale(raw as Record<string, unknown>)) {
        const s = freshState()
        saveState(s)
        return s
      }
      return {
        directives: Array.isArray(raw.directives)
          ? raw.directives.map((d: any) => ({
              id: String(d.id ?? ""),
              kind: String(d.kind ?? "rule"),
              subject: String(d.subject ?? ""),
              target: typeof d.target === "number" ? d.target : undefined,
              source: String(d.source ?? ""),
              pattern: d.pattern instanceof RegExp ? d.pattern : (typeof d.pattern === "string" ? new RegExp(d.pattern) : /(?:)/),
              active: Boolean(d.active ?? true),
              created_ts: typeof d.created_ts === "number" ? d.created_ts : 0,
              updated_ts: typeof d.updated_ts === "number" ? d.updated_ts : 0,
            }))
          : freshState().directives,
        last_dispatch_count: typeof raw.last_dispatch_count === "number" ? raw.last_dispatch_count : 0,
        last_dispatch_ts: typeof raw.last_dispatch_ts === "number" ? raw.last_dispatch_ts : 0,
        pid: typeof raw.pid === "number" ? raw.pid : process.pid,
      }
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
      last_dispatch_ts: s.last_dispatch_ts,
      pid: s.pid,
    }
    fs.writeFileSync(STATE_FILE, JSON.stringify(ser), "utf8")
  } catch {}
}

// ── Directive extraction from user messages ─────────────────────────────────

const MUST_GT_NUMERIC_RE = /\b(must|should|need|have to|ensure|required)\s+(?:be|have|reach|hit|get\s+to)\s*[><=]?\s*(\d+)(?:%| percent)?\b/i
const MAINTAIN_FLOOR_RE = /\bmaintain\s+(?:a\s+)?(\d+)[- ]agent\s+floor\b/i
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
  if (floorMatch) {
    found.push({
      kind: "floor",
      subject: "subagent floor",
      target: parseInt(floorMatch[1], 10),
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
  return tool === "task" || tool === "agent" || tool === "workflow"
}

// ── DEFAULT IMPLEMENTATION ──────────────────────────────────────────────────

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, _output: any) => {
    if (isSubagent()) return
    if (!ENABLED) return
    try {
      const tool = input?.tool ?? input?.tool_name ?? ""

      // Step 1: mine directives from user messages in the prompt context
      const userText = extractUserText(input)
      if (userText && userText.length > 10) {
        const found = extractDirectivesFromText(userText)
        if (found.length > 0) {
          const s = loadState()
          let changed = false
          for (const fd of found) {
            const dedupId = `${fd.kind}-${fd.subject}`.replace(/\s+/g, "-").toLowerCase().substring(0, 60)
            const existing = s.directives.find(d => d.id === dedupId)
            if (!existing) {
              s.directives.push({
                id: dedupId,
                kind: fd.kind as Directive["kind"],
                subject: fd.subject ?? "",
                target: fd.target,
                source: fd.source ?? "user-directive",
                pattern: /(?:)/,
                active: true,
                created_ts: Date.now(),
                updated_ts: Date.now(),
              })
              changed = true
            }
          }
          if (changed) saveState(s)
        }
      }

      // Step 2: dispatch counting for floor directive
      if (isDispatchTool(tool)) {
        const s = loadState()
        s.last_dispatch_count++
        s.last_dispatch_ts = Date.now()
        saveState(s)
        return
      }

      // Step 3: block non-dispatch tools when floor directive requires active subagents
      const s = loadState()
      const floorDirective = s.directives.find(d => d.kind === "floor" && d.active)
      if (floorDirective && floorDirective.target !== undefined) {
        const now = Date.now()
        const sinceLastDispatch = now - s.last_dispatch_ts
        // Allow reads, edits, and writes through — only block bash ops that
        // would replace dispatching work
        if (tool === "bash" || tool === "Bash") {
          const cmd = String(input?.args?.command ?? input?.tool_input?.command ?? "")
          const isGitTarget = /\b(git-commit|git-push|batch-push|ship-commit|release-cut|git-tag-push)\b/.test(cmd)
          const isReadOnly = /\b(git-status|git-log|git-diff|ci-verdict|gate-status|verify-state|disk|git-staged|git-show)\b/.test(cmd)
          // Block shipping/commit targets when no dispatches recently
          if (isGitTarget && s.last_dispatch_count === 0 && sinceLastDispatch > 60000) {
            return {
              permissionDecision: "deny",
              message: `DIRECTIVE VIOLATION: floor=${floorDirective.target} agents required, but 0 dispatches made. Commit/push blocked until ≥${floorDirective.target} agents dispatched.`,
            }
          }
          // Allow read-only targets
          if (isReadOnly) return
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
              if (s.last_dispatch_count < (d.target ?? 10)) {
                blockMsg = `DIRECTIVE VIOLATION: floor=${d.target ?? 10} agents required, but only ${s.last_dispatch_count} dispatched. Resuming work in progress is not completion.`
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
