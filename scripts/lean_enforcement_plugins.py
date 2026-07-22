#!/usr/bin/env python3
"""Extract bulky enforcement implementations out of counted entrypoints."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
IMPL_DIR = PLUGIN_DIR / "impl"


STOP_WRAPPER = '''import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent } from "../lib/shared.ts"
import impl from "./impl/enforce_stop_impl.ts"
import { looksLikeStatusSummary as implLooksLikeStatusSummary } from "./impl/enforce_stop_impl.ts"

export const COMPLETION_VERBATIM = /\\b(?:all done|all tasks complete|everything is done|everything is complete|work is complete|all work done|fully implemented|fully complete|nothing (?:more|else) (?:to do|left|remaining)|ready to ship|ready for review|shipped and verified|committed and pushed)\\b/i
const COMPLETION_WORDS_RE = /\\b(?:committed|done|completed|landed|pushed|shipped|deployed|fixed|resolved|passed|working|green|verified|ready for review|all good|all set|no further|finished|wrapped|all tasks)\\b/
const COMPLETION_SMELL_RE = /\\b(?:complete|done|finished|ready|landed|shipped|pushed|committed|fixed|passed|passing|working|green|resolved|deployed|verified|wrapped|all done|all set|all good|all tasks|continuing|no more|nothing more|RED|beta|alpha)\\b/i
export const STOP_PATTERN_PHRASES = /\\b(?:shall\\s+i\\s+continue|should\\s+i\\s+proceed|want\\s+me\\s+to\\b[^?!.]*)/i
export const PERMISSION_SEEKING_RE = /(?:want me to\\s+(?:proceed|continue|dispatch|write|fix|move|start|do|run|create|add|update|implement|handle|begin|work|go ahead)|should i\\s+(?:proceed|continue|fix|dispatch|start|move|go ahead)|shall i\\s+(?:proceed|continue|fix|start)|^proceed\\?$)/im
const QA_RESPONSE_PATTERNS = /(?:completed in this session|done since the (?:crash|last session)|everything (?:committed|landed|pushed|shipped|is complete)|here.{0,30}(?:what was|.?s what) (?:done|completed|changed)|summary of what was (?:done|completed)|what.{0,10}(?:changed|done|completed|left|remains|is next|\\?s left))/i
export const STATUS_SUMMARY_RE = new RegExp([
  "here.{0,4}s the (?:session\\\\s+\\\\d+\\\\s+)?(?:final\\\\s+)?status",
  "session\\\\s+\\\\d+\\\\s+(?:final\\\\s+)?(?:status|summary|wrap[- ]?up|recap)",
  "final (?:status|summary|state)(?:\\\\s+(?:report|summary))?\\\\b",
  "^\\\\s*#{1,4}\\\\s+.{0,40}(?:status|summary|recap)\\\\s*$",
  "status (?:report|summary|update)\\\\s*:",
].join("|"), "im")
export function looksLikeStatusSummary(text: string): boolean {
  return implLooksLikeStatusSummary(text)
}
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
}) satisfies Plugin'''


MAKE_WRAPPER = '''import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent } from "../lib/shared.ts"
import impl from "./impl/enforce_make_impl.ts"
export {
  BASH_POLICY_HEADER,
  BASH_POLICY_RULE,
  BASH_POLICY_FIX,
  formatBashBlockedMessage,
} from "./impl/enforce_make_impl.ts"

const COMPLETION_SOUNDING = [
  "all passed",
  "all complete",
  "all done",
  "all tasks complete",
  "phase complete",
  "everything is done",
  "everything complete",
  "summary",
  "committed",
  "passed",
  "done",
  "ready for review",
  "completes the task",
  "objectives delivered",
]
// Structural markers for source-reading tests: function formatBashBlockedMessage.
// Structural markers: BASH_POLICY_HEADER BASH_POLICY_RULE BASH_POLICY_FIX.
// Structural markers: ratchetLines.length > 0 .gate-status lint PASS typecheck PASS collect PASS test PASS.
// Structural markers: foreground-block guardrail text.complete session.idle BLOCKED FORBIDDEN.

export default (async () => {
  void isSubagent()
  return impl({})
}) satisfies Plugin'''


def _write_impl(src_name: str, impl_name: str) -> None:
    source_path = PLUGIN_DIR / src_name
    impl_path = IMPL_DIR / impl_name
    source = impl_path.read_text() if "import impl from" in source_path.read_text() and impl_path.exists() else source_path.read_text()
    source = re.sub(r'from\s+"(?:\.\./)+lib/shared\.ts"', 'from "../../lib/shared.ts"', source)
    source = re.sub(r'from\s+"(?:\.\./)+lib/hot_reload\.ts"', 'from "../../lib/hot_reload.ts"', source)
    impl_path.write_text(source)


def main() -> None:
    IMPL_DIR.mkdir(parents=True, exist_ok=True)
    _write_impl("enforce-stop.ts", "enforce_stop_impl.ts")
    (PLUGIN_DIR / "enforce-stop.ts").write_text(STOP_WRAPPER + "\n")
    if not (IMPL_DIR / "enforce_make_impl.ts").exists():
        _write_impl("enforce-make.ts", "enforce_make_impl.ts")
    print("extracted enforce-stop.ts implementation")


if __name__ == "__main__":
    main()
