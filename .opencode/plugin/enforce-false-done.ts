import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"

const FALSE_DONE_ENFORCE = process.env.GLUDD_FALSE_DONE_ENFORCE !== "0"
const MAX_CONSECUTIVE_BLOCKS = 25
const STATE_FILE = process.env.GLUDD_FALSE_DONE_STATE_FILE || "/tmp/gludd-false-done-blocks.json"

const turnState: { accumulatedText: string; blocked: boolean } = { accumulatedText: "", blocked: false }

// Minimal CLAIM_PATTERNS — only truly terminal claims (shipped, released, deployed)
const CLAIM_PATTERNS: RegExp[] = [
  /\blanded\b/i, /\bshipped\b/i, /\bdeployed\b/i, /\breleased\b/i,
  /(?:\bis|'s|\bit'?s|\bwe'?re|\bthey'?re|\bare)\s+(?:now\s+)?live\b/i,
  /\bgoes? live\b/i,
]

const EVIDENCE_PATTERNS: RegExp[] = [
  /ci-verdict/i, /conclusion:\s*success/i, /\brun[ _]?id\b/i, /\brun \d{6,}/i,
  /gh release view/i, /verify-release/i, /verify-remote/i, /\.gate-status/i,
  /gate(?:-status)?:?\s*pass/i, /\bgate green\b/i,
  /\b[1-9]\d*\s+passed\b/i, /\b[1-9]\d*\s+passing\b/i,
  /\bverified\b[^.\n]{0,40}(?:[1-9]\d*\s+passed|conclusion:\s*success|run \d{6,})/i,
  /\bcommit\s+(?!0{7}|deadbeef|c0ffee)[0-9a-f]{7,40}\b/i,
  /\bsha[:= ]\s*[0-9a-f]{7,40}\b/i,
  /\bat\s+[0-9a-f]{7,40}\b/i, /`[0-9a-f]{7,40}`/i,
  /VERIFIED\s+\S+@[0-9a-f]{7,40}/i,
  /```(?=[^`]*?(?:[1-9]\d*\s+passed|passed in|conclusion|success))[^`]*```/i,
]

const RELEASE_CLAIM_PATTERNS: RegExp[] = [/\bshipped\b/i, /\breleased\b/i, /\bdeployed\b/i]
const RELEASE_EVIDENCE_PATTERNS: RegExp[] = [
  /VERIFIED\s+\S+@[0-9a-f]{7,40}/i,
  /verify-release-artifact[^\n]{0,80}PASS/i,
  /ARTIFACT\s+CHECK:\s*PASS/i,
  /gh release view/i,
]

const HEDGE_PATTERNS: RegExp[] = [
  /\bnot (?:yet |fully )?(?:done|live|complete|completed|committed|pushed|built|working|applied|landed|shipped|wired|verified)\b/i,
  /\bnot yet\b/i, /\bin progress\b/i, /\bin-flight\b/i,
  /(?<!no )(?<!not )(?<!zero )\buncommitted\b/i,
  /(?<!no )(?<!not )(?<!zero )\bunpushed\b/i,
  /(?<!no )(?<!not )(?<!zero )\bpending\b/i,
  /\bunverified\b/i, /\bisn'?t\b/i, /\bhaven'?t\b/i, /\bhasn'?t\b/i,
  /\bnot committed\b/i, /\bnot pushed\b/i, /\bnot applied\b/i, /\bnot built\b/i,
  /\bnext steps?\b/i, /\bstill needs?\b/i, /\bblocked\b/i,
  /GLUDD_FALSE_DONE_ENFORCE=0/,
]

interface MatchResult { isFalseDone: boolean; matchedPhrase: string | null }

function classify(text: string): MatchResult {
  const hasClaim = CLAIM_PATTERNS.some(p => p.test(text))
  if (!hasClaim) return { isFalseDone: false, matchedPhrase: null }
  const hasHedge = HEDGE_PATTERNS.some(p => p.test(text))
  if (hasHedge) return { isFalseDone: false, matchedPhrase: null }
  const hasReleaseClaim = RELEASE_CLAIM_PATTERNS.some(p => p.test(text))
  if (hasReleaseClaim) {
    const hasReleaseEvidence = RELEASE_EVIDENCE_PATTERNS.some(p => p.test(text))
    if (!hasReleaseEvidence) {
      const m = text.match(/\b(?:shipped|released|deployed)\b/i)
      return { isFalseDone: true, matchedPhrase: m ? m[0] : "shipped/released/deployed" }
    }
  }
  const hasEvidence = EVIDENCE_PATTERNS.some(p => p.test(text))
  if (hasEvidence) return { isFalseDone: false, matchedPhrase: null }
  let matched: string | null = null
  for (const p of CLAIM_PATTERNS) { const m = text.match(p); if (m) { matched = m[0]; break } }
  return { isFalseDone: true, matchedPhrase: matched }
}

function readCount(): number {
  try {
    if (!fs.existsSync(STATE_FILE)) return 0
    const raw = fs.readFileSync(STATE_FILE, "utf8")
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === "object" && typeof parsed.count === "number" ? parsed.count : 0
  } catch { return 0 }
}
function writeCount(n: number): void { try { fs.writeFileSync(STATE_FILE, JSON.stringify({ count: n }), "utf8") } catch {} }

function blockDirective(matchedPhrase: string | null, blockNum: number): string {
  const phrase = matchedPhrase ? `"${matchedPhrase}"` : "a done/shipped/landed claim"
  const isReleasePhrase = matchedPhrase && /\b(?:shipped|released|deployed)\b/i.test(matchedPhrase)
  const releaseNote = isReleasePhrase
    ? `⛔ RELEASE CLAIM WITHOUT ARTIFACT EVIDENCE — include VERIFIED line, verify-release-artifact PASS, or gh release view output.\n\n`
    : ""
  return `${releaseNote}⛔ FALSE-COMPLETION BLOCKED: your response contains ${phrase} but cites no evidence token (commit SHA, run URL, gate output, pass count, VERIFIED line). Per AGENTS.md evidence required. This is block ${blockNum} of ${MAX_CONSECUTIVE_BLOCKS}; after the cap you will pass through (anti-wedge).`
}

export default (async () => {
  return {
    event: async ({ event }: { event: { type: string } }) => {
      if (event.type === "session.idle") { turnState.accumulatedText = ""; turnState.blocked = false }
    },
    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      try {
        if (!FALSE_DONE_ENFORCE) return
        turnState.accumulatedText += output.text
        if (turnState.blocked) { output.text = ""; return }
        const result = classify(turnState.accumulatedText)
        if (!result.isFalseDone) { if (readCount() !== 0) writeCount(0); return }
        const current = readCount()
        if (current >= MAX_CONSECUTIVE_BLOCKS) { writeCount(0); return }
        const next = current + 1; writeCount(next)
        output.text = blockDirective(result.matchedPhrase, next)
        turnState.blocked = true
      } catch { return }
    },
  }
}) satisfies Plugin
