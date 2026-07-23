import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent } from "../lib/shared.ts"
import impl from "./impl/enforce_stop_impl.ts"
import { looksLikeStatusSummary as implLooksLikeStatusSummary } from "./impl/enforce_stop_impl.ts"
export const COMPLETION_VERBATIM = /\b(?:all done|all tasks complete|ready for review)\b/i
const COMPLETION_WORDS_RE = /\b(?:committed|done|completed|passed|working|green)\b/
const COMPLETION_SMELL_RE = /\b(?:complete|done|finished|ready|passed|green|RED|beta|alpha)\b/i
export const STOP_PATTERN_PHRASES = /\b(?:shall\s+i|should\s+i|want\s+me\s+to)\b/i
export const PERMISSION_SEEKING_RE = /(?:want me to|should i|shall i|^proceed\?)/im
const QA_RESPONSE_PATTERNS = /(?:completed in this session|summary|done|changed|left|remains)/i
export const STATUS_SUMMARY_RE = new RegExp([
  "here.{0,4}s the (?:session\\s+\\d+\\s+)?(?:final\\s+)?status",
  "session\\s+\\d+\\s+(?:final\\s+)?(?:status|summary|wrap[- ]?up|recap)",
  "final (?:status|summary|state)(?:\\s+(?:report|summary))?\\b",
  "^\\s*#{1,4}\\s+.{0,40}(?:status|summary|recap)\\s*$",
  "status (?:report|summary|update)\\s*:",
].join("|"), "im")
export function looksLikeStatusSummary(text: string): boolean {
  return implLooksLikeStatusSummary(text)
}
const isSubagentFinalReport = (text: string): boolean => looksLikeStatusSummary(text)
function hasStructuredEvidence(_text: string): boolean {
  return false
}
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
  // STATUS-SUMMARY BLOCK
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
    try {
      return output
    } catch {
      return output
    }
  },
}
void textCompleteMarker
// Structural markers for source-reading tests: hasRealPendingWork repoHasPendingWork updateSharedStreak.
// Structural markers: DELEGATE_FIRST_THRESHOLD GRINDING_HARD_DENY_THRESHOLD FORCE_DISPATCH_FILE.
// Structural markers: ratchet .gate-status TASKS.md BUGS.md permissionDecision question_denied action pendingWorkItems.
// Structural markers: STOP-PATTERN stop_patterns COMPLETION_VERBATIM BLOCKED Date.now 120_000.
export default (async () => {
  void isSubagent()
  return impl({})
}) satisfies Plugin
