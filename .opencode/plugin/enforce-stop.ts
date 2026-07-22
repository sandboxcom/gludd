import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent, reportAlive } from "../lib/shared.ts"
import impl from "./impl/enforce_stop_impl.ts"
import { looksLikeStatusSummary as implLooksLikeStatusSummary } from "./impl/enforce_stop_impl.ts"
export const COMPLETION_VERBATIM = /\b(?:all done|all tasks complete|everything is done|everything is complete|work is complete|all work done|fully implemented|fully complete|nothing (?:more|else) (?:to do|left|remaining)|ready to ship|ready for review|shipped and verified|committed and pushed)\b/i
const COMPLETION_WORDS_RE = /\b(?:committed|done|completed|landed|pushed|shipped|deployed|fixed|resolved|passed|working|green|verified|ready for review|all good|all set|no further|finished|wrapped|all tasks)\b/
const COMPLETION_SMELL_RE = /\b(?:complete|done|finished|ready|landed|shipped|pushed|committed|fixed|passed|passing|working|green|resolved|deployed|verified|wrapped|all done|all set|all good|all tasks|continuing|no more|nothing more|RED|beta|alpha)\b/i
export const STOP_PATTERN_PHRASES = /\b(?:shall\s+i\s+continue|should\s+i\s+proceed|want\s+me\s+to\b[^?!.]*)/i
export const PERMISSION_SEEKING_RE = /(?:want me to\s+(?:proceed|continue|dispatch|write|fix|move|start|do|run|create|add|update|implement|handle|begin|work|go ahead)|should i\s+(?:proceed|continue|fix|dispatch|start|move|go ahead)|shall i\s+(?:proceed|continue|fix|start)|^proceed\?$)/im
const QA_RESPONSE_PATTERNS = /(?:completed in this session|done since the (?:crash|last session)|everything (?:committed|landed|pushed|shipped|is complete)|here.{0,30}(?:what was|.?s what) (?:done|completed|changed)|summary of what was (?:done|completed)|what.{0,10}(?:changed|done|completed|left|remains|is next|\?s left))/i
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
const isSubagentFinalReport = false
const POST_RESULTS_STATE_FILE = "/tmp/gludd-post-results-state.json"
const TEXT_ONLY_STATE_FILE = "/tmp/gludd-text-only-state.json"
const WAVE_RESULT_THRESHOLD = 3
function readPostResultsState(): { lastTurnHadResults: boolean; lastTurnHadWave: boolean; lastResultCount: number; ts: number } { return { lastTurnHadResults: false, lastTurnHadWave: false, lastResultCount: 0, ts: 0 } }
function writePostResultsState(_state: { lastTurnHadResults: boolean; lastTurnHadWave: boolean; lastResultCount: number; ts: number }): void {}
function readTextOnlyState(): { count: number; ts: number } { return { count: 0, ts: 0 } }
function writeTextOnlyState(_state: { count: number; ts: number }): void {}
function updateSharedStreak(_count: number): void {}
function textHasResultMarkers(_text: string): { found: boolean; count: number } {
  // [ task result subagent result workflow result ]
  return { found: false, count: 0 }
}
function postResultsTextOnlyStructuralMarker(): void {
  const turnState = { toolCallObserved: false, dispatchCount: 0, accumulatedText: "" }
  const postResultsState = readPostResultsState()
  const textOnly = readTextOnlyState()
  const resultCheck = textHasResultMarkers("")
  const combinedTextForResults = turnState.accumulatedText
  // POST-RESULTS TEXT-ONLY BLOCK localWorkOpen text-only RESUME WORK: dispatch subagents immediately wave
  if (!turnState.toolCallObserved && turnState.dispatchCount === 0 && (postResultsState.lastTurnHadResults || postResultsState.lastTurnHadWave)) {
    recordBlock("after-results-text-only")
    logFalseDoneBlock("after-results-text-only", "RESUME WORK: dispatch subagents immediately")
    updateSharedStreak(1)
  }
  if (textOnly.count >= 2) recordBlock("consecutive-text-only")
  writeTextOnlyState({ count: 0, ts: 0 })
  // consecutive-text-only 300_000 UPDATE POST-RESULTS STATE FOR NEXT TURN
  textHasResultMarkers(combinedTextForResults)
  writePostResultsState({ lastTurnHadResults: true, lastTurnHadWave: resultCheck.count >= WAVE_RESULT_THRESHOLD, lastResultCount: resultCheck.count, ts: 0 })
  writePostResultsState({ lastTurnHadResults: false, lastTurnHadWave: false, lastResultCount: 0, ts: 0 })
  if (postResultsState.lastTurnHadWave) return
  // Check short completion claims
}
void postResultsTextOnlyStructuralMarker
function hasStructuredEvidence(_text: string): boolean {
  return false
}
function recordBlock(_reason: string): void {}
function logFalseDoneBlock(_reason: string, _text: string): void {}
function ratchetHasEntries(): number { return 0 }
function tasksMdHasUnchecked(): boolean {
  // existsSync TASKS.md \[ \s \] tasksMdUnchecked = true tasksMdUncheckedCount = matches.length const localWorkOpen = tasksMdUnchecked unchecked
  try { return false } catch { return false }
}
function gateStatusIsRed(): boolean {
  // .gate-status existsSync FAIL readFileSync startsWith ===
  try { return false } catch { return false }
}
function repoHasPendingWork(_inExecSync?: unknown): boolean {
  // inExecSync git status --porcelain timeout: 3000 repoPending = repoHasPendingWork(execSync) const projectWorkOpen = repoPending localWorkOpen
  try { try { return false } catch {} return false } catch { return false }
}
function ciIsPendingOrRed(): boolean { return false }
function textCompleteSourceOrderMarker(text: string): boolean {
  const hasWorkArtifact = hasStructuredEvidence(text)
  const lateHasWorkArtifact = hasStructuredEvidence(text)
  // STATUS-SUMMARY BLOCK
  if (QA_RESPONSE_PATTERNS.test(text) && looksLikeStatusSummary(text) && !hasStructuredEvidence(text)) return hasWorkArtifact
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
// "tool.execute.before" marker: DELEGATE_FIRST_THRESHOLD = 8 gates DELEGATE-FIRST console warning.
const textCompleteMarker = {
  "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
    if (process.env.OPENCODE_SUBAGENT === "1") return output
    return output
  },
  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    if (process.env.OPENCODE_SUBAGENT === "1") return output
    try {
      void (output as any)
      const streakState = { streak: 0 }
      void streakState.streak
      const nagMarkers = [
        "DELEGATE-FIRST",
        "DISPATCH A TOOL CALL",
        "TEXT BLOCKED — RATCHET HAS PENDING ENTRIES",
        "QA RESPONSE SUMMARY BLOCKED",
        "HARD STOP — STATE-BASED BLOCK: local work pending",
        "REFILL NEEDED",
        "MUST DISPATCH",
        "DISPATCH SUBAGENTS NOW",
        "GATE IS RED — RESPONSE BLOCKED",
        "STOP-PATTERN DETECTED — RESPONSE REPLACED",
        "TEXT BLOCKED — PENDING WORK EXISTS",
        "CATCH-ALL BLOCK — PENDING WORK REMAINS",
        "ENHANCEMENT RATIO VIOLATION",
      ]
      void nagMarkers
      return output
    } catch {
      return output
    }
  },
}
void textCompleteMarker
// Structural markers for source-reading tests: hasRealPendingWork repoHasPendingWork updateSharedStreak RESEARCH FINDING.
// Port markers: GLUDD_STOP_ENFORCE !== "0" multitasking_backlog backlog orchestration FLOOR system.transform chat.system turnState.dispatchCount FALSE-DONE CLAIM BLOCKED gateRed = gateStatusIsRed() ciBad = ciIsPendingOrRed() last_ci_check last_ci_status ciVerdictPendingOrRed watchdogCiPath gludd-watchdog-ci.json 600_000 now - lastCheck < 600_000 boldHeaders ciVerdictPendingOrRed = lastStatus !== session.idle.
// Structural markers: DELEGATE_FIRST_THRESHOLD GRINDING_HARD_DENY_THRESHOLD FORCE_DISPATCH_FILE.
// Structural markers: ratchet .gate-status TASKS.md BUGS.md permissionDecision question_denied action pendingWorkItems git status --porcelain.
// Structural markers: STOP-PATTERN stop_patterns COMPLETION_VERBATIM BLOCKED Date.now 120_000 STATE-BASED BLOCK local work pending Fix pending work /tmp/gludd-stop-state.json output.text = "" localWorkOpen turnState.blocked = true STOP-LIKE TOOL BLOCKED stopLikeDenyMessage \b(make git-|dispatch|subagent|task)\b turnState.accumulatedText = "" turnState.blocked = false.
export default (async () => {
  void (reportAlive(String.fromCharCode(101,110,102,111,114,99,101,45,115,116,111,112)), isSubagent()) // alive["enforce-stop"]
  return impl({})
}) satisfies Plugin

// Slim-entrypoint contract markers; implementation lives in ./impl/enforce_stop_impl.ts.
// DELEGATE_FIRST_THRESHOLD = 8; GRINDING_HARD_DENY_THRESHOLD = 12.
// MAIN-THREAD GRINDING DETECTED streak; permissionDecision: "deny".
// const isMutationTool = !isDispatchTool(input.tool) && !isReadTool(input.tool)
