import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive, writeHeartbeat } from "../lib/shared.ts"

// enforce-deliverable.ts — warns when subagent dispatch prompts use check-only
// patterns instead of concrete deliverables.
//
// AGENTS.md "Subagent Task Design — Fix, Don't Check": every subagent MUST
// produce a concrete fix/deliverable — never just a status report, audit
// finding, or problem list.
//
// WHAT IT DOES:
//   * tool.execute.before (task/agent/workflow) — extracts dispatch prompt,
//     scans for forbidden check-only patterns, injects console.warn if matched.
//   * WARNING ONLY — never blocks; the deny is handled by other plugins.
//
// DISABLE: GLUDD_DELIVERABLE_ENFORCE=0
// SUBAGENT SKIP: OPENCODE_SUBAGENT=1
// FAIL-OPEN: every code path wrapped; internal errors never wedge the session.

const ENABLED = (process.env.GLUDD_DELIVERABLE_ENFORCE || "1") !== "0"

const MAX_PROMPT_LINES = 20

const CHECK_ONLY_PATTERNS = /\b(check|audit|scan|review|survey|report|summarize|run|poll|wait|watch|find|read|list)\b.*?\b(report|summarize|CI|lint|typecheck|type\s*errors|dead\s*code|dirty\s*tree|coverage|secrets|vulnerabilities|status|git\s*status|unused|files|completion|imports)\b/i

function extractPrompt(args: any): string {
  if (!args) return ""
  if (typeof args.prompt === "string") return args.prompt
  if (typeof args.description === "string") return args.description
  if (typeof args.message === "string") return args.message
  if (typeof args.content === "string") return args.content
  if (typeof args.text === "string") return args.text
  try { return JSON.stringify(args).substring(0, 500) } catch { return "" }
}

function isDispatchTool(tool: string): boolean {
  return tool === "task" || tool === "agent" || tool === "workflow"
}

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, _output: any) => {
    if (isSubagent()) return
    reportAlive("enforce-deliverable")
    writeHeartbeat("enforce-deliverable")
    if (!ENABLED) return
    const tool = input.tool
    if (!isDispatchTool(tool)) return
    try {
      const prompt = extractPrompt(input.args)
      if (!prompt) return
      if (CHECK_ONLY_PATTERNS.test(prompt)) {
        console.warn(
          `DELIVERABLE WARNING: dispatch prompt matches check-only pattern. ` +
          `Subagents MUST produce a concrete fix/deliverable, not just a status report. ` +
          `See AGENTS.md "Subagent Task Design — Fix, Don't Check".`
        )
      }
      const promptLines = prompt.split("\n").length
      if (promptLines > MAX_PROMPT_LINES) {
        console.warn(
          `TERSE PROMPT RULE: dispatch prompt is ${promptLines} lines. ` +
          `Max: ${MAX_PROMPT_LINES} lines. Condense to essential requirements only.`
        )
      }
    } catch { /* fail open */ }
  },
}

export default (({ }) => {
  return {
    "tool.execute.before": async (input: any, _output: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("deliverable", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, _output) : undefined
    },
  }
}) satisfies Plugin
