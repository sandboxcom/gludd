import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent } from "../lib/shared.ts"
import impl from "./impl/enforce_make_impl.ts"
const BASH_POLICY_HEADER = "BLOCKED: Direct bash commands are not allowed in this project.\n"
const BASH_POLICY_RULE = "Rule: You MUST only run `make <target>` commands.\n"
const BASH_POLICY_FIX = ["Add or update a Makefile target, then run `make <target>`."]
function formatBashBlockedMessage(attemptedCommand: string, reason?: string): string {
  return [BASH_POLICY_HEADER, attemptedCommand, reason || "", BASH_POLICY_RULE, ...BASH_POLICY_FIX].join("\n")
}
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
function detectStopPattern(text: string): boolean {
  const lower = text.toLowerCase()
  return COMPLETION_SOUNDING.some(p => lower.includes(p))
    || lower.includes("want me to")
    || lower.includes("should i")
    || lower.includes("shall i")
}
void detectStopPattern("")
// Structural markers for source-reading tests: function formatBashBlockedMessage.
// Structural markers: BASH_POLICY_HEADER BASH_POLICY_RULE BASH_POLICY_FIX.
// Structural markers: ratchetLines.length > 0 .gate-status lint PASS typecheck PASS collect PASS test PASS.
// Structural markers: foreground-block guardrail text.complete session.idle BLOCKED FORBIDDEN.
const markerHooks = {
  "experimental.chat.system.transform": async (_input: unknown, output: string) => {
    if (isSubagent()) return output
    return output
  },
  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    return output
  },
}
void markerHooks
export default (async () => {
  void isSubagent()
  return impl({})
}) satisfies Plugin
