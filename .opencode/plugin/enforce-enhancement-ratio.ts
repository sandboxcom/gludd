import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive, writeHeartbeat } from "../lib/shared.ts"

// enforce-enhancement-ratio.ts — per-wave enhancement/fix dispatch ratio enforcement.
//
// AGENTS.md COST-EFFICIENCY DIRECTIVE §5 (2026-07-12): at least 50% of every
// dispatch wave must be project enhancements, not just bug fixes.
//
// WHAT IT DOES:
//   * tool.execute.before (task/agent/workflow) — classifies dispatch as
//     "enhancement" or "fix" based on prompt keywords, appends to wave.
//     When wave reaches ≥2 entries, checks ratio: if >50% fixes → DENY.
//     If ≤50% fixes → ALLOW + reset wave.  Self-contained — no text.complete.
//   * Conservative default: unknown prompts count as "fix".
//
// STATE FILE: /tmp/gludd-enhancement-ratio.json
// DISABLE: GLUDD_ENHANCEMENT_RATIO_ENFORCE=0
// SOFT MODE: GLUDD_ENHANCEMENT_RATIO_BLOCK=0 (console.warn only, no deny)
// SUBAGENT SKIP: OPENCODE_SUBAGENT=1
//
// FAIL-OPEN: every code path wrapped; internal errors never wedge the session.
//
// HOT-RELOAD: proxy pattern from hot_reload.ts.  Hook functions check
// /tmp/gludd-hot-enhancement-ratio.js on every invocation.  Run
// `make hot-reload-plugins` after editing this file.

const STATE_FILE = process.env.GLUDD_ENHANCEMENT_RATIO_STATE || "/tmp/gludd-enhancement-ratio.json"
const ENABLED = (process.env.GLUDD_ENHANCEMENT_RATIO_ENFORCE || "1") !== "0"
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
  "broken", "incident", "hotfix",
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
  lastPid: number
  lastTs: number
}

function _freshState(): RatioState {
  return { wave: [], session_enhancements: 0, session_fixes: 0, session_unknown: 0, lastPid: process.pid, lastTs: 0 }
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

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, _output: any) => {
    if (isSubagent()) return
    reportAlive("enforce-enhancement-ratio")
    writeHeartbeat("enforce-enhancement-ratio")
    if (!ENABLED) return

    const tool = input.tool
    if (!isDispatchTool(tool)) return

    try {
      const prompt = extractPrompt(input.args)
      const category = classify(prompt)
      const s = loadState()

      s.wave.push({
        type: `${category}`,
        prompt_head: `${prompt.substring(0, 120)}`,
        ts: Date.now(),
      })

      if (category === "enhancement") s.session_enhancements++
      else if (category === "fix") s.session_fixes++
      else s.session_unknown++

      if (s.wave.length >= 2) {
        const fixCount = s.wave.filter(e => e.type === "fix").length
        const fixRatio = fixCount / s.wave.length

        if (fixRatio > 0.5) {
          const fixPct = (fixRatio * 100).toFixed(0)
          const enhCount = s.wave.length - fixCount
          s.wave = []
          saveState(s)

          if (BLOCK) {
            return {
              permissionDecision: "deny",
              message: `ENHANCEMENT RATIO VIOLATION: ${fixPct}% fixes (${fixCount}/${fixCount + enhCount}) in this wave. Must be ≤50%. Replace fix dispatches with enhancement work.`
            }
          } else {
            console.warn(`ENHANCEMENT RATIO WARNING: ${fixPct}% fixes (${fixCount}/${fixCount + enhCount}) in this wave.`)
            return
          }
        }

        s.wave = []
      }

      saveState(s)
    } catch { /* fail open */ }
  },
}

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  return {
    "tool.execute.before": async (input: any, _output: any) => {
      if (isSubagent()) return;
      const impl = loadHotModule("enhancement-ratio", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, _output) : undefined
    },
  }
}) satisfies Plugin
