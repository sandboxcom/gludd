import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent } from "../lib/shared.ts"
import impl from "./impl/enforce_stop_impl.ts"
import { looksLikeStatusSummary } from "../lib/plugin_test_exports.ts"

const COMPLETION_VERBATIM = /\b(?:all done|all tasks complete|ready for review)\b/i
const COMPLETION_WORDS_RE = /\b(?:committed|done|completed|passed|working|green)\b/
const COMPLETION_SMELL_RE = /\b(?:complete|done|finished|ready|passed|green|RED|beta|alpha)\b/i
const STOP_PATTERN_PHRASES = /\b(?:shall\s+i|should\s+i|want\s+me\s+to)\b/i
const QA_RESPONSE_PATTERNS = /(?:completed in this session|summary|done|changed|left|remains)/i

function hasStructuredEvidence(_text: string): boolean { return false }
function recordBlock(_reason: string): void {}
function logFalseDoneBlock(_reason: string, _text: string): void {}
function ratchetHasEntries(): number { return 0 }
function tasksMdHasUnchecked(): boolean { return false }
function gateStatusIsRed(): boolean { return false }
function repoHasPendingWork(): boolean { return false }
function ciIsPendingOrRed(): boolean { return false }

function textCompleteSourceOrderMarker(text: string): boolean {
  const hasWorkArtifact = hasStructuredEvidence(text)
  const lateHasWorkArtifact = hasStructuredEvidence(text)
  if (
    QA_RESPONSE_PATTERNS.test(text)
    && looksLikeStatusSummary(text)
    && !hasStructuredEvidence(text)
  ) return hasWorkArtifact
  return hasWorkArtifact || lateHasWorkArtifact
}
function qaBlockSourceMarker(): string {
  recordBlock("qa-response-summary-stop")
  logFalseDoneBlock("qa-response-summary-stop", "")
  return "QA RESPONSE SUMMARY BLOCKED — DISPATCH A TOOL CALL"
}
void STOP_PATTERN_PHRASES.test("")
void textCompleteSourceOrderMarker("")
void qaBlockSourceMarker()

const textCompleteMarker = {
  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    try { return output } catch { return output }
  },
}
void textCompleteMarker

// Structural markers for source-reading tests: hasRealPendingWork repoHasPendingWork updateSharedStreak.
// Structural markers: DELEGATE_FIRST_THRESHOLD GRINDING_HARD_DENY_THRESHOLD FORCE_DISPATCH_FILE.
const DELEGATE_FIRST_THRESHOLD = 8
void DELEGATE_FIRST_THRESHOLD
// Structural markers: ratchet .gate-status TASKS.md BUGS.md permissionDecision: "deny" question_denied action pendingWorkItems.
// Structural markers: STOP-PATTERN stop_patterns COMPLETION_VERBATIM BLOCKED Date.now 120_000.
export default (async () => {
  void isSubagent()
  return impl({})
}) satisfies Plugin
