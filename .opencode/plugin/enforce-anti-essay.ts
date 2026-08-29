// Fail-open. Subagent guard. Hot-reload capable.
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import {
  isSubagent,
  reportAlive,
  readJsonFile,
  getProjectRoot,
  hasTasksMdPendingWork,
} from "../lib/shared.ts"
const ESSAY_WORD_THRESHOLD = parseInt(
  process.env.GLUDD_ESSAY_WORD_THRESHOLD || "50",
  10,
)
const ESSAY_PARAGRAPH_THRESHOLD = parseInt(
  process.env.GLUDD_ESSAY_PARAGRAPH_THRESHOLD || "3",
  10,
)
function hasPendingWork(): boolean {
  const root = getProjectRoot()
  try {
    const tasksPath = path.join(root, "TASKS.md")
    if (hasTasksMdPendingWork(tasksPath)) return true
    const ratchetPath = path.join(root, "config", "ratchet.yml")
    if (fs.existsSync(ratchetPath)) {
      const entries = fs.readFileSync(ratchetPath, "utf8")
        .split("\n")
        .filter(l => l.trim() && !l.trim().startsWith("#") && l.includes(":"))
      if (entries.length > 0) return true
    }
  } catch {
    // fail-open
  }
  return false
}
function wordCount(text: string): number {
  return text.split(/\s+/).filter(w => w.length > 0).length
}
function paragraphCount(text: string): number {
  return text.split(/\n\s*\n/).filter(p => p.trim().length > 0).length
}
function hasCommitHash(text: string): boolean {
  return /[0-9a-f]{7,40}/.test(text)
}
function hasTestCount(text: string): boolean {
  return /\d+\s+(passed|tests?|pass(?:ing)?)/i.test(text)
}
function hasCiVerdict(text: string): boolean {
  return /conclusion:\s*(?:success|failure)/i.test(text) ||
    /CI\s+(?:GREEN|RED)/.test(text)
}
function hasBoldedHeaders(text: string): boolean {
  const headerPattern = /\*\*(?:What (?:changed|worked|was done|happened|is left|next)|Why|How|Status|Summary|Remaining|Next steps?)\*\*/gi
  return headerPattern.test(text)
}
function hasStatusSummary(text: string): boolean {
  const summaryPatterns = [
    /here'?s\s+(?:what|a)\s+(?:was\s+)?(?:done|changed|completed|the\s+status)/i,
    /summary\s+of\s+(?:what\s+was\s+done|changes|this\s+session)/i,
    /status\s+report\s*(?::|$)/i,
    /session\s+\d+\s+summary/i,
    /what\s+we\s+did\s+so\s+far/i,
    /let\s+me\s+explain/i,
    /completed\s+in\s+this\s+session/i,
    /everything\s+committed\s+and\s+merged/i,
  ]
  return summaryPatterns.some(r => r.test(text))
}
function hasEvidence(text: string): boolean {
  return hasCommitHash(text) || hasTestCount(text) || hasCiVerdict(text)
}
const NAG_TEXT = (
  "\n███  ANTI-ESSAY GUARD: pending work exists.  ███\n" +
  "This response looks like an essay/status report.  If work remains, " +
  "replace it with a tool call (dispatch subagents, read/edit files, run tests).\n" +
  "Explanations and summaries when work is pending are a policy violation.\n" +
  "Set GLUDD_ANTI_ESSAY_ENFORCE=0 to disable this guard.\n"
)
const defaultImpl: HotModule = {
  "experimental.text.complete": async (output) => {
    if (isSubagent()) return
    reportAlive("enforce-anti-essay")
    try {
      if (process.env.GLUDD_ANTI_ESSAY_ENFORCE === "0") return
      if (!hasPendingWork()) return
      const text = typeof output === "object" && output !== null && "text" in output
        ? String((output as Record<string, unknown>).text)
        : typeof output === "string" ? output : ""
      if (!text) return
      const words = wordCount(text)
      const paragraphs = paragraphCount(text)
      const hasBold = hasBoldedHeaders(text)
      const isSummary = hasStatusSummary(text)
      const evidence = hasEvidence(text)
      const isEssay = (words > ESSAY_WORD_THRESHOLD || paragraphs > ESSAY_PARAGRAPH_THRESHOLD)
      const isBlockedPattern = hasBold || isSummary
      if (isBlockedPattern && !evidence) {
        return {
          ...(output as Record<string, unknown>),
          text: NAG_TEXT,
        }
      }
      if (isEssay && !evidence && !isBlockedPattern) {
        return {
          ...(output as Record<string, unknown>),
          text: NAG_TEXT + "\n" + text,
        }
      }
    } catch {
      // fail-open
    }
  },
  "tool.execute.before": async (input, _output) => {
    if (isSubagent()) return
    reportAlive("enforce-anti-essay")
    try {
      if (process.env.GLUDD_ANTI_ESSAY_ENFORCE === "0") return
      // tool.execute.before only watches — doesn't block tool calls.
      // The experimental.text.complete hook is the primary enforcement surface.
    } catch {
      // fail-open
    }
  },
}
export default (({ }) => {
  return {
    // opencode 1.17.9 only registers "experimental.text.complete" — bare
    // "text.complete" is rejected by Plugin.add and crashes opencode at boot.
    "experimental.text.complete": async (_input, output) => {
      if (isSubagent()) return output
      const impl = loadHotModule("anti-essay", defaultImpl)
      const fn = impl["text.complete"] || impl["experimental.text.complete"]
      return fn ? await fn(output) : output
    },
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return
      const impl = loadHotModule("anti-essay", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
  }
}) satisfies Plugin
