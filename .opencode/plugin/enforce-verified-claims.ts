/**
 * enforce-verified-claims.ts — commit-time enforcement only.
 *
 * text.complete was removed: the hook fires before text batching, not on
 * actual claim content, so it cannot reliably verify claims. The remaining
 * enforcement lives in tool.execute.before (commit-message checks) and
 * the exported constants (pinned by test_verified_claims_plugin.py).
 *
 * Default ON. Set GLUDD_VERIFIED_CLAIMS_ENFORCE=0 to disable.
 * Fail-open: any throw/exception → allow (never wedge the editor).
 *
 * HOT-RELOAD: proxy pattern from hot_reload.ts. Run `make hot-reload-plugins`
 * after editing this file.
 */
import type { Plugin } from "@opencode-ai/plugin"
import type { HotModule } from "../lib/hot_reload.ts"
import { loadHotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive } from "../lib/shared.ts"

const DONE_WORDS = [
  "landed", "committed", "pushed", "fixed", "passing",
  "shipped", "done", "complete", "green", "resolved",
  "deployed", "verified", "passed", "working",
] as const

const EVIDENCE_PATTERNS = [
  /\b[0-9a-f]*[a-f][0-9a-f]{6,39}\b/,
  /VERIFIED\s+\w+@/,
  /CI\s+(GREEN|RED|PENDING)/,
  /\d+\s+passed/,
  /===\s*(?:GATE|GATE-LITE):\s*(?:PASSED|FAILED)/,
  /Collection OK/,
  /All checks passed/,
  /Success: no issues found/,
] as const

const NOT_DONE_PHRASES = [
  /\bworking\s+on\b/,
] as const

const BLOCK_MESSAGE = [
  "BLOCKED: response contains done-claims without verification evidence.",
  "Run make git-status, make git-log, make ci-verdict-safe, or make test-iso",
  "and paste the output before claiming work is done.",
  "See AGENTS.md 'Evidence-Based Response Policy' and 'Done Claims Require Observable Verification Evidence'.",
].join("\n")

export const shouldBlock = (text: string): boolean => {
  if (!text || text.trim().length === 0) return false
  let lower = text.toLowerCase()
  for (const phrase of NOT_DONE_PHRASES) {
    lower = lower.replace(phrase, " ")
  }
  let found = false
  for (const w of DONE_WORDS) {
    const re = new RegExp(`\\b${w}\\b`)
    if (re.test(lower)) { found = true; break }
  }
  if (!found) return false
  return !EVIDENCE_PATTERNS.some((p) => p.test(text))
}

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: unknown) => {
    try {
      if (isSubagent()) return
      if (process.env.GLUDD_VERIFIED_CLAIMS_ENFORCE === "0") return
      const ctx = input as Record<string, unknown> | undefined
      const tool = ctx?.tool
      if (tool !== "bash" && tool !== "Bash") return
      const args = (ctx?.args ?? {}) as Record<string, unknown>
      const cmd = String(args?.command ?? "")
      if (!cmd.startsWith("make ") || !/\b(git-commit|commit-no-verify|repo-commit|ship-commit|test-and-commit)\b/.test(cmd)) return
      const msgMatch = cmd.match(/MSG=(?:"([^"]*)"|'([^']*)'|(\S+))/)
      const msg = msgMatch ? (msgMatch[1] ?? msgMatch[2] ?? msgMatch[3] ?? "") : ""
      if (msg && shouldBlock(msg) && !EVIDENCE_PATTERNS.some((p) => p.test(msg))) {
        throw Object.assign(new Error(BLOCK_MESSAGE), { permissionDecision: "deny" })
      }
    } catch (e) {
      if (e instanceof Error && (e as Error & { permissionDecision?: string }).permissionDecision === "deny") throw e
    }
  },
}

export default (() => {
  return {
    "tool.execute.before": async (input: unknown) => {
      if (isSubagent()) return
      reportAlive("enforce-verified-claims")
      const impl = loadHotModule("verified-claims", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
    },
  }
}) satisfies Plugin
