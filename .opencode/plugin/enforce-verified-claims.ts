/**
 * enforce-verified-claims.ts — structurally block false-done claims.
 *
 * Per AGENTS.md "Evidence-Based Response Policy" and "Done Claims Require
 * Observable Verification Evidence": the orchestrator repeatedly claims work
 * is "done", "landed", "pushed", "fixed", "passing" without pasting the
 * verification output (make git-log, make ci-verdict, make verify-remote,
 * make test-iso). This plugin blocks outgoing text containing done-words
 * UNLESS it also carries machine-produced evidence.
 *
 * Hook surface: `experimental.text.complete` (fires before text reaches the
 * user). On block, the outgoing text is replaced with BLOCK_MESSAGE — a
 * clean deny that names the remediation commands.
 *
 * Default ON. Set GLUDD_VERIFIED_CLAIMS_ENFORCE=0 to disable.
 * Fail-open: any throw/exception → allow (never wedge the editor).
 *
 * The matcher is exported as named constants + a `shouldBlock` function so
 * tests/unit/test_verified_claims_plugin.py can pin the behavior without a
 * JS runtime.
 */
import type { Plugin } from "@opencode-ai/plugin"

/**
 * Words that signal a completion / success claim. When ANY of these appear,
 * the response MUST also carry evidence (a commit hash, VERIFIED token, CI
 * verdict, pass count, or gate marker) or it is blocked.
 *
 * Kept lower-case; the matcher compares case-insensitively.
 */
export const DONE_WORDS = [
  "landed",
  "committed",
  "pushed",
  "fixed",
  "passing",
  "shipped",
  "done",
  "complete",
  "green",
  "resolved",
  "deployed",
  "verified",
  "passed",
  "working",
] as const

/**
 * Machine-produced evidence tokens that back a done-claim. ANY one of these
 * alongside a done-word cancels the block. These correspond to the canonical
 * verification outputs named in AGENTS.md:
 *   - commit hash (7-40 hex) — make git-log
 *   - VERIFIED <branch>@<sha> — make verify-remote
 *   - CI GREEN|RED|PENDING — make ci-verdict
 *   - N passed — make test / make test-iso
 *   - === GATE[: PASSED|FAILED] === — make gate
 *   - Collection OK — make collect-check
 *   - All checks passed — ruff
 *   - Success: no issues found — mypy
 */
export const EVIDENCE_PATTERNS = [
  /\b[0-9a-f]{7,40}\b/,
  /VERIFIED\s+\w+@/,
  /CI\s+(GREEN|RED|PENDING)/,
  /\d+\s+passed/,
  /===\s*(?:GATE|GATE-LITE):\s*(?:PASSED|FAILED)/,
  /Collection OK/,
  /All checks passed/,
  /Success: no issues found/,
] as const

/**
 * In-progress phrases that look like done-words but are NOT claims.
 * "working on X" = activity, not a completion state. These are scrubbed
 * from the text before the done-word check so "working on the fix" does
 * not trip the "working" done-word.
 */
export const NOT_DONE_PHRASES = [
  /\bworking\s+on\b/,
] as const

/**
 * The replacement text shown when a done-claim is blocked. Names the
 * remediation commands so the agent knows exactly what to run to produce
 * real evidence.
 */
export const BLOCK_MESSAGE = [
  "BLOCKED: response contains done-claims without verification evidence.",
  "Run make git-status, make git-log, make ci-verdict-safe, or make test-iso",
  "and paste the output before claiming work is done.",
  "See AGENTS.md 'Evidence-Based Response Policy' and 'Done Claims Require Observable Verification Evidence'.",
].join("\n")

function _reportAlive(): void {
  try {
    const alivePath = "/tmp/gludd-plugin-alive.json"
    const fs = require("node:fs")
    let alive: Record<string, unknown> = {}
    if (fs.existsSync(alivePath)) {
      try { alive = JSON.parse(fs.readFileSync(alivePath, "utf8")) } catch {}
    }
    alive["enforce-verified-claims"] = { last_seen: Date.now() }
    fs.writeFileSync(alivePath, JSON.stringify(alive), "utf8")
  } catch {}
}

/**
 * Does `text` contain a done-word? Case-insensitive, word-boundary aware.
 * In-progress phrases (NOT_DONE_PHRASES) are scrubbed first so "working on"
 * does not count as the done-word "working".
 */
export const shouldBlock = (text: string): boolean => {
  if (!text || text.trim().length === 0) return false
  let lower = text.toLowerCase()
  for (const phrase of NOT_DONE_PHRASES) {
    lower = lower.replace(phrase, " ")
  }
  let found = false
  for (const w of DONE_WORDS) {
    const re = new RegExp(`\\b${w}\\b`)
    if (re.test(lower)) {
      found = true
      break
    }
  }
  if (!found) return false
  const hasEvidence = EVIDENCE_PATTERNS.some((p) => p.test(text))
  return !hasEvidence
}

export default (async () => {
  return {
    "experimental.text.complete": async (
      _input: unknown,
      output: { text: string },
    ) => {
      if (process.env.OPENCODE_SUBAGENT === "1") return output
      _reportAlive()
      try {
        if (process.env.GLUDD_VERIFIED_CLAIMS_ENFORCE === "0") return
        if (/^(⛔|HARD STOP|MUST DISPATCH|ENHANCEMENT RATIO|████|BLOCKED:|MULTITASK|INSUFFICIENT DISPATCHES|ZERO-DISPATCH|DISPATCH SUBAGENTS|EARLY ENHANCEMENT|DELEGATE-FIRST|REFILL NEEDED|AFTER-RESULTS|CONSECUTIVE TEXT-ONLY|FALSE-DONE|QA RESPONSE)/.test((output?.text ?? "").trim())) return output
        const text = output?.text ?? ""
        if (shouldBlock(text)) {
          output.text = BLOCK_MESSAGE
        }
      } catch {
        // Fail-open: a broken hook must never wedge the editor or swallow
        // the agent's real output. Leave the text untouched.
      }
    },
  }
}) satisfies Plugin
