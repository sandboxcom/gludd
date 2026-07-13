import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive } from "../lib/shared.ts"

// enforce-enhancement-ratio.ts — per-wave enhancement/fix dispatch ratio enforcement.
//
// AGENTS.md COST-EFFICIENCY DIRECTIVE §5 (2026-07-12): at least 50% of every
// dispatch wave must be project enhancements, not just bug fixes. Multiple
// sessions of fix-only dispatches were observed — the agent was only dispatching
// repair work and never advancing the project.
//
// WHAT IT DOES:
//   * tool.execute.before (task/agent/workflow) — classifies dispatch as
//     "enhancement" or "fix" based on prompt keywords, appends to wave array
//   * text.complete — finalizes the wave: when ≥2 dispatches accumulated,
//     computes ratio. DEFAULT: console.warn if fix% > 50%. HARD_DENY=1:
//     prepends a HARD STOP directive to outgoing text (blocks the wave).
//   * Early directive: when wave has ≥2 fixes + 0 enhancements, text.complete
//     injects a pre-ratio directive so the agent knows before the wave ends.
//   * Conservative default: unknown prompts count as "fix" (err on the side
//     of flagging)
//
// STATE FILE: /tmp/gludd-enhancement-ratio.json
// DISABLE: GLUDD_ENHANCEMENT_RATIO_ENFORCE=0
// HARD DENY: GLUDD_ENHANCEMENT_RATIO_HARD_DENY=1 (blocks via text injection)
// SUBAGENT SKIP: OPENCODE_SUBAGENT=1 — subagents don't enforce this
//
// FAIL-OPEN: every code path wrapped; internal errors never wedge the session.
//
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
// check /tmp/gludd-hot-enhancement-ratio.js on every invocation.  Run
// `make hot-reload-plugins` after editing this file to generate the hot module.

const STATE_FILE = process.env.GLUDD_ENHANCEMENT_RATIO_STATE || "/tmp/gludd-enhancement-ratio.json"
const ENABLED = (process.env.GLUDD_ENHANCEMENT_RATIO_ENFORCE || "1") !== "0"
const HARD_DENY = process.env.GLUDD_ENHANCEMENT_RATIO_HARD_DENY === "1"
const BLOCK = (process.env.GLUDD_ENHANCEMENT_RATIO_BLOCK || "1") !== "0"

const ENHANCEMENT_KEYWORDS = [
  "enhancement", "feature", "docs", "documentation",
  "test", "tooling", "script", "make target",
  "presentation", "skill", "guardrail", "refactor",
  "observability", "new feature", "new test", "add test",
  "add feature", "codify", "self-test",
]

const FIX_KEYWORDS = [
  "fix", "bug", "repair", "regression",
  "broken", "repair", "incident", "hotfix",
]

interface WaveEntry {
  type: "enhancement" | "fix"
  prompt_head: string
  ts: number
}

interface RatioState {
  wave: WaveEntry[]
  session_enhancements: number
  session_fixes: number
  session_unknown: number
  wave_count_since_last_warn: number
  early_warned: boolean
  lastPid: number
  lastTs: number
}

function _freshState(): RatioState {
  return { wave: [], session_enhancements: 0, session_fixes: 0, session_unknown: 0, wave_count_since_last_warn: 0, early_warned: false, lastPid: process.pid, lastTs: 0 }
}

function _isStale(raw: any): boolean {
  if (typeof raw.lastPid === "number" && raw.lastPid !== process.pid) return true
  if (typeof raw.lastTs === "number" && (Date.now() - raw.lastTs) > 3_600_000) return true
  return false
}

function loadState(): RatioState {
  try {
    if (fs.existsSync(STATE_FILE)) {
      const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
      if (_isStale(raw)) return _freshState()
      return {
        wave: Array.isArray(raw.wave) ? raw.wave : [],
        session_enhancements: typeof raw.session_enhancements === "number" ? raw.session_enhancements : 0,
        session_fixes: typeof raw.session_fixes === "number" ? raw.session_fixes : 0,
        session_unknown: typeof raw.session_unknown === "number" ? raw.session_unknown : 0,
        wave_count_since_last_warn: typeof raw.wave_count_since_last_warn === "number" ? raw.wave_count_since_last_warn : 0,
        early_warned: typeof raw.early_warned === "boolean" ? raw.early_warned : false,
        lastPid: typeof raw.lastPid === "number" ? raw.lastPid : process.pid,
        lastTs: typeof raw.lastTs === "number" ? raw.lastTs : Date.now(),
      }
    }
  } catch {}
  return _freshState()
}

function saveState(s: RatioState): void {
  try {
    s.lastPid = process.pid
    s.lastTs = Date.now()
    fs.writeFileSync(STATE_FILE, JSON.stringify(s), "utf8")
  } catch {}
}

function extractPrompt(args: any): string {
  if (!args) return ""
  if (typeof args.prompt === "string") return args.prompt
  if (typeof args.description === "string") return args.description
  if (typeof args.message === "string") return args.message
  if (typeof args.content === "string") return args.content
  if (typeof args.text === "string") return args.text
  try { return JSON.stringify(args).substring(0, 500) } catch { return "" }
}

function classify(prompt: string): "enhancement" | "fix" {
  const lower = prompt.toLowerCase()
  for (const kw of ENHANCEMENT_KEYWORDS) {
    if (lower.includes(kw)) return "enhancement"
  }
  for (const kw of FIX_KEYWORDS) {
    if (lower.includes(kw)) return "fix"
  }
  return "fix"
}

function isDispatchTool(tool: string): boolean {
  return tool === "task" || tool === "agent" || tool === "workflow"
}

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
// Dedup guard: prevent the same response from being progressively modified by
// multiple text.complete hook calls.  The lastHookTime + lastHookOutputHash
// pair tracks the most recent hook invocation; if the same output is seen
// within 50ms, the hook is a duplicate and returns early.
let _lastHookTime = 0
let _lastHookOutputHash = ""

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, _output: any) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return
    reportAlive("enforce-enhancement-ratio")
    if (!ENABLED) return

    const tool = input.tool
    if (!isDispatchTool(tool)) return

    try {
      const prompt = extractPrompt(input.args)
      const category = classify(prompt)
      const s = loadState()

      s.wave.push({
        type: category,
        prompt_head: prompt.substring(0, 120),
        ts: Date.now(),
      })

      if (category === "enhancement") s.session_enhancements++
      else if (category === "fix") s.session_fixes++
      else s.session_unknown++

      saveState(s)

      if (BLOCK && s.wave.length >= 2) {
        const fixCount = s.wave.filter(e => e.type === "fix").length
        const fixRatio = fixCount / s.wave.length
        if (fixRatio > 0.5) {
          const fixPct = (fixRatio * 100).toFixed(0)
          return {
            permissionDecision: "deny",
            message: `ENHANCEMENT RATIO VIOLATION: ${fixPct}% fixes (${fixCount}/${s.wave.length}) in this wave. Must be ≤50%. Replace fix dispatches with enhancement work.`
          }
        }
      }
    } catch { /* fail open */ }
  },

    "experimental.text.complete": async (output: any) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return output
    if (!ENABLED) return output
    const outText = typeof output === 'string' ? output : (output?.text ?? "")
    if (/^(⛔|HARD STOP|MUST DISPATCH|ENHANCEMENT RATIO|████|BLOCKED:|MULTITASK|INSUFFICIENT DISPATCHES|ZERO-DISPATCH|DISPATCH SUBAGENTS|EARLY ENHANCEMENT|DELEGATE-FIRST|REFILL NEEDED|AFTER-RESULTS|CONSECUTIVE TEXT-ONLY|FALSE-DONE|QA RESPONSE)/.test(outText)) return output

    try {
      const s = loadState()
      if (s.wave.length < 2) return output

      const fixCount = s.wave.filter(e => e.type === "fix").length
      const enhancementCount = s.wave.filter(e => e.type === "enhancement").length
      const total = s.wave.length
      const fixRatio = fixCount / total
      const enhancementRatio = 1 - fixRatio

      let modified = ""

      if (!s.early_warned && fixCount >= 2 && enhancementCount === 0) {
        s.early_warned = true
        const directive = [
          "EARLY ENHANCEMENT RATIO WARNING: all dispatches in this wave are fixes.",
          `(${fixCount} fixes, ${enhancementCount} enhancements so far)`,
          "Add at least one enhancement dispatch before the wave ends.",
        ].join(" ")
        if (HARD_DENY) {
          modified += `\n\n${directive}\n`
        } else {
          console.warn(directive)
        }
      }

      if (fixRatio > 0.5) {
        const fixPct = (fixRatio * 100).toFixed(0)
        const enhancementPct = (enhancementRatio * 100).toFixed(0)
        const violMsg =
          `ENHANCEMENT RATIO VIOLATION: ${fixPct}% fixes (${fixCount}/${total}) in this wave. ` +
          `Only ${enhancementPct}% enhancements. ` +
          `At least 50% of dispatches must be enhancements per AGENTS.md COST-EFFICIENCY DIRECTIVE §5.` +
          `\n\nRe-split the wave: replace fix dispatches with enhancement work.\n`

        s.wave = []
        s.early_warned = false
        s.wave_count_since_last_warn = 0
        saveState(s)

        if (BLOCK || HARD_DENY) {
          return violMsg
        } else {
          console.warn(violMsg)
          if (modified) return output + modified
          return output
        }
      }

      s.wave = []
      s.early_warned = false
      saveState(s)

      if (modified) {
        return output + modified
      }
      return output
    } catch { /* fail open */ }

    return output
  },
}

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  return {
    "tool.execute.before": async (input: any, _output: any) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return;
      const impl = loadHotModule("enhancement-ratio", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, _output) : undefined
    },

    "experimental.text.complete": async (output: any) => {
      const impl = loadHotModule("enhancement-ratio", defaultImpl)
      const fn = impl["experimental.text.complete"]
      return fn ? await fn(output) : output
    },
  }
}) satisfies Plugin
