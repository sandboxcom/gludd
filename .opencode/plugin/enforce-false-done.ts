import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"

// enforce-false-done.ts — opencode-native port of
// `.claude/hooks/no_false_completion_stop.sh`.
//
// BLOCKS an outgoing assistant message that CLAIMS work is done / shipped /
// landed / fixed / ✅ WITHOUT a cited, machine-produced MEASUREMENT and WITHOUT
// an honest hedge. This is the rule from AGENTS.md "'Done' Claims Require
// Observable Verification Evidence" made machine-enforceable in opencode
// (the Claude layer already had it; the opencode stack was missing the port).
//
// CONTRACT: `experimental.chat.response.transform` REPLACES (not appends) the
// outgoing response with a loud block directive when a false-completion claim
// is detected. Matches the replacement-not-append pattern of enforce-todos.ts
// for terminal blocks and the enforce-stop.ts pattern for stop blocks.
//
// FAIL-OPEN: every hook returns the original output on any internal error
// (parse/IO/regex). A guardrail bug must NEVER wedge the session.
//
// ANTI-WEDGE: a bounded consecutive-block counter persists to
// `/tmp/gludd-false-done-blocks.json`. A claim-without-evidence is blocked
// every time, but after MAX_CONSECUTIVE_BLOCKS in a row it fails open so a
// false-positive on a genuinely-finished turn can never wedge permanently.
// The counter resets to 0 the moment a claim WITH evidence (or no claim at
// all) is observed — i.e. honest behavior clears the wedge counter.
//
// OFF-SWITCH: GLUDD_FALSE_DONE_ENFORCE=0 makes this plugin advisory (never
// blocks, returns output unchanged). Default is ENFORCE (block).
// GLUDD_FALSE_DONE_MAX_BLOCKS overrides the consecutive-block cap (default 25).

// ============================================================================
// CONFIG
// ============================================================================

// DEFAULT ON via the canonical `!== "0"` pattern (matches
// GLUDD_FLOOR_ENFORCE / GLUDD_NO_WAIT_ENFORCE / GLUDD_TODO_GUARD_ENFORCE).
const FALSE_DONE_ENFORCE = process.env.GLUDD_FALSE_DONE_ENFORCE !== "0"

// Consecutive-block cap before fail-open. Bash hook default is 25.
const MAX_CONSECUTIVE_BLOCKS = (() => {
  const raw = process.env.GLUDD_FALSE_DONE_MAX_BLOCKS
  if (!raw) return 25
  const n = Number.parseInt(raw, 10)
  return Number.isFinite(n) && n > 0 ? n : 25
})()

const STATE_FILE =
  process.env.GLUDD_FALSE_DONE_STATE_FILE ||
  "/tmp/gludd-false-done-blocks.json"

// ============================================================================
// (1) CLAIM patterns — strong completion / success claim about shippable /
//     outward work. Ported VERBATIM from no_false_completion_stop.sh
//     `claim_patterns` (lowercased matching; we use the `i` flag here).
// ============================================================================

const CLAIM_PATTERNS: RegExp[] = [
  /✅/,
  /[✔✓☑🟢🆗👍]/,
  /\blanded\b/i,
  /\bshipped\b/i,
  /\bship it\b/i,
  /\bdeployed\b/i,
  /\breleased\b/i,
  /\bmerged\b/i,
  // "is live" + contractions
  /(?:\bis|'s|\bit'?s|\bwe'?re|\bthey'?re|\bare)\s+(?:now\s+)?live\b/i,
  /\bgoes? live\b/i,
  /\bnow works\b/i,
  /\bup and running\b/i,
  /\boperational\b/i,
  /\bdone\b/i,
  /\ball set\b/i,
  /\bcomplete(?:s|d)?\b/i,
  /\bresolved\b/i,
  /\bfixed\b/i,
  /\bworking\b/i,
  /\bfunctional\b/i,
  /\bsuccessful(?:ly)?\b/i,
  /\bwired (?:up|in)\b/i,
  /\bfully wired\b/i,
  /\bproduction[- ]ready\b/i,
  /\bready to (?:go|ship|merge|land|release)\b/i,
  /\bgood to go\b/i,
  /\ball green\b/i,
  /\bgreen\b.*\bpipeline\b/i,
]

// ============================================================================
// (2) EVIDENCE tokens — a cited, machine-produced measurement. Presence
//     anywhere means the claim is BACKED; allow the response. HARDENED for
//     the adversarial patterns the bash hook explicitly defends against:
//       - "0 passed" is NOT evidence (failure/none).
//       - bare ``` fence without a measurement body is NOT evidence.
//       - placeholder/fake SHAs (deadbeef / c0ffee / all-zero) are NOT evidence.
//       - lone word "verified" without an adjacent measurement is NOT evidence.
//     Ported VERBATIM from no_false_completion_stop.sh `evidence_patterns`.
// ============================================================================

const EVIDENCE_PATTERNS: RegExp[] = [
  /ci-verdict/i,
  /conclusion:\s*success/i,
  /\brun[ _]?id\b/i,
  /\brun \d{6,}/i,
  /gh release view/i,
  /verify-release/i,
  /verify-remote/i,
  /\.gate-status/i,
  /gate(?:-status)?:?\s*pass/i,
  /\bgate green\b/i,
  // Nonzero test counts only ("0 passed" is failure/none, not evidence).
  /\b[1-9]\d*\s+passed\b/i,
  /\b[1-9]\d*\s+passing\b/i,
  // "verified" only counts when adjacent to an actual measurement.
  /\bverified\b[^.\n]{0,40}(?:[1-9]\d*\s+passed|conclusion:\s*success|run \d{6,})/i,
  // Commit SHA, excluding low-entropy placeholders (deadbeef / c0ffee / all-zero).
  /\bcommit\s+(?!0{7}|deadbeef|c0ffee)[0-9a-f]{7,40}\b/i,
  /\bsha[:= ]\s*[0-9a-f]{7,40}\b/i,
  // A code fence counts ONLY if its body contains a measurement token. Uses a
  // lookahead + single greedy sweep (NOT two lazy unbounded [^`]*? spans, which
  // are O(N^2) on an adversarial unclosed fence and could hang this hook).
  /```(?=[^`]*?(?:[1-9]\d*\s+passed|passed in|conclusion|success))[^`]*```/i,
]

// ============================================================================
// (3) HEDGE patterns — honest hedge / negation / in-progress markers. The
//     claim is qualified, not asserted. Presence anywhere means allow the
//     response. Ported VERBATIM from no_false_completion_stop.sh
//     `hedge_patterns` (the bash hook uses lookbehind assertions; JS supports
//     them too, ported as-is).
// ============================================================================

const HEDGE_PATTERNS: RegExp[] = [
  /\bnot (?:yet |fully )?(?:done|live|complete|completed|committed|pushed|built|working|applied|landed|shipped|wired|verified)\b/i,
  /\bnot yet\b/i,
  /\bin progress\b/i,
  /\bin-flight\b/i,
  // uncommitted/unpushed/pending only count when NOT preceded by a negator.
  /(?<!no )(?<!not )(?<!zero )\buncommitted\b/i,
  /(?<!no )(?<!not )(?<!zero )\bunpushed\b/i,
  /(?<!no )(?<!not )(?<!zero )\bpending\b/i,
  /\bunverified\b/i,
  /\b(?:this is|it'?s|still) a draft\b/i,
  // future-WORK senses of will/would only (not "users will love it").
  /\bwill (?:not|need|require|fail|follow|be (?:done|built|pushed|added))\b/i,
  /\bwould (?:need|have to|require)\b/i,
  /\bnot applied\b/i,
  /\bnot built\b/i,
  /\bisn'?t\b/i,
  /\bhaven'?t\b/i,
  /\bhasn'?t\b/i,
  /\bnothing is (?:live|on)\b/i,
  /\bnot real\b/i,
  /\bcan'?t claim\b/i,
  /\bnot committed\b/i,
  /\bnot pushed\b/i,
  /\bover-?claim\b/i,
  /\bi was wrong\b/i,
  /\bfalse claim\b/i,
  /\b(?:need|have|going) to prove\b/i,
  /\bonce .{0,30}?(?:lands?|returns?|finish)/i,
  /\b(?:is|are|was|were|'s|'re)\s+not\s+(?:yet\s+)?\w*\s?(?:done|live|complete)\b/i,
  // Standing off-switch reference (agent citing the env-var bypass).
  /GLUDD_FALSE_DONE_ENFORCE=0/,
  // Generic "next steps" / "still needs" honest forward-look markers.
  /\bnext steps?\b/i,
  /\bstill needs?\b/i,
  /\bblocked\b/i,
]

// ============================================================================
// MATCHING
// ============================================================================

interface MatchResult {
  isFalseDone: boolean
  matchedPhrase: string | null
}

function classify(text: string): MatchResult {
  const hasClaim = CLAIM_PATTERNS.some(p => p.test(text))
  if (!hasClaim) {
    return { isFalseDone: false, matchedPhrase: null }
  }
  const hasEvidence = EVIDENCE_PATTERNS.some(p => p.test(text))
  if (hasEvidence) {
    return { isFalseDone: false, matchedPhrase: null }
  }
  const hasHedge = HEDGE_PATTERNS.some(p => p.test(text))
  if (hasHedge) {
    return { isFalseDone: false, matchedPhrase: null }
  }
  // Find the specific claim phrase that fired (for the directive message).
  let matched: string | null = null
  for (const p of CLAIM_PATTERNS) {
    const m = text.match(p)
    if (m) {
      matched = m[0]
      break
    }
  }
  return { isFalseDone: true, matchedPhrase: matched }
}

// ============================================================================
// ANTI-WEDGE COUNTER
// Persisted consecutive-block count. Resets to 0 on any non-blocked response.
// After MAX_CONSECUTIVE_BLOCKS in a row, the next claim is allowed through
// (fail-open) and the counter resets.
// ============================================================================

function readCount(): number {
  try {
    if (!fs.existsSync(STATE_FILE)) return 0
    const raw = fs.readFileSync(STATE_FILE, "utf8")
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === "object" && typeof parsed.count === "number") {
      return parsed.count
    }
    return 0
  } catch {
    return 0
  }
}

function writeCount(n: number): void {
  try {
    fs.writeFileSync(STATE_FILE, JSON.stringify({ count: n }), "utf8")
  } catch {
    // best-effort
  }
}

// ============================================================================
// BLOCK DIRECTIVE
// ============================================================================

function blockDirective(matchedPhrase: string | null, blockNum: number): string {
  const phrase = matchedPhrase ? `"${matchedPhrase}"` : "a done/shipped/landed claim"
  return [
    `⛔ FALSE-COMPLETION BLOCKED: your response contains ${phrase} but cites`,
    "no evidence token (commit SHA, run URL, gate output, pass count, VERIFIED line).",
    "",
    'Per AGENTS.md "\'Done\' Claims Require Observable Verification Evidence":',
    "- Unit fix → named passing test + make-test pass count",
    "- Local gate → make gate (lint 0, typecheck ≤ baseline, collect 0, tests pass) + .gate-status PASS",
    "- Committed → commit hash from make git-log + gate evidence",
    "- Pushed → make verify-remote BRANCH=b SHA=sha → VERIFIED line",
    "- CI-green → make ci-verdict BRANCH=b → conclusion: success + headSha == tip",
    "- Shipped → make verify-release-artifact TAG=t PASS + gh release view assets",
    "",
    "Either PASTE the measurement or REPHRASE without the done-claim. " +
      `This is block ${blockNum} of ${MAX_CONSECUTIVE_BLOCKS}; after the cap ` +
      "you'll be allowed through (anti-wedge).",
  ].join("\n")
}

// ============================================================================
// PLUGIN
// ============================================================================
export default (async () => {
  return {
    "experimental.chat.response.transform": async (_input: unknown, output: unknown) => {
      try {
        if (!FALSE_DONE_ENFORCE) return output
        if (typeof output !== "string") return output

        const result = classify(output)

        if (!result.isFalseDone) {
          // Honest response (no claim, claim+evidence, or claim+hedge).
          // Reset the consecutive-block counter.
          if (readCount() !== 0) writeCount(0)
          return output
        }

        // False-completion claim detected. Apply the bounded anti-wedge counter.
        const current = readCount()
        if (current >= MAX_CONSECUTIVE_BLOCKS) {
          // Anti-wedge: cap reached, allow this one through and reset.
          writeCount(0)
          return output
        }

        const next = current + 1
        writeCount(next)

        // REPLACE (not append) the response with the block directive so the
        // unverified claim never reaches the user.
        return blockDirective(result.matchedPhrase, next)
      } catch {
        return output // fail open
      }
    },
  }
}) satisfies Plugin
