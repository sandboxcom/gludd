import type { Plugin } from "@opencode-ai/plugin"

// --- No-lint-suppression-comments guardrail (3-layer) -----------------------
// This plugin enforces the "No Lint-Suppression Comments" policy from
// AGENTS.md. The user reported that prior codification was advisory-only and
// allowed regression; this plugin is the hard, mechanical layer that makes
// regression structurally impossible at edit time.
//
// Layer map (see AGENTS.md "Meta-Rule: Guardrail Policy"):
//   1. Config permission  — n/a (this is a content-based check, not a tool ban)
//   2. Runtime hook       — THIS FILE (tool.execute.before on edit/write)
//   3. Agent prompt       — AGENTS.md "No Lint-Suppression Comments" section
//   + Behavior pin         — tests/unit/test_no_suppression_comments_plugin.py
//   + Repo-wide scan       — tests/unit/test_type_safety_guardrails.py
//                            (assert-based, NOT warnings.warn — the prior
//                            advisory-only failure mode that allowed regression)

// --- Forbidden patterns -----------------------------------------------------
// The exact regex set mandated by the task spec. Exported as a named const so
// tests/unit/test_no_suppression_comments_plugin.py can extract and exercise
// them. ORDER MUST MATCH the spec list (noqa, type:ignore, pylint, fmt, isort).
export const SUPPRESSION_PATTERNS: RegExp[] = [
  /#\s*noqa/,
  /#\s*type:\s*ignore/,
  /#\s*pylint:/,
  /#\s*fmt:\s*(?:off|skip|on)/,
  /#\s*isort:\s*skip/,
]

// --- Allowlisted paths ------------------------------------------------------
// These files legitimately CONTAIN the patterns as DATA (string literals,
// frozenset entries, regex fixtures) — not as live suppression comments.
// Blocking them would break the policy's own enforcement code.
export const ALLOWLIST_PATHS: string[] = [
  "src/general_ludd/security/fix_not_disable.py",
  "tests/unit/test_type_safety_guardrails.py",
]

export const DENY_MESSAGE =
  "Lint-suppression comments forbidden. Fix the underlying issue. " +
  "See AGENTS.md Guardrail Integrity Policy."

// --- Matcher (exported for unit-test extraction) ----------------------------
// Pure function: returns true iff `text` contains any forbidden suppression
// comment. Does NOT consult the allowlist — callers gate on path first.
export function isSuppressionComment(text: string): boolean {
  if (typeof text !== "string" || text.length === 0) return false
  return SUPPRESSION_PATTERNS.some(re => re.test(text))
}

// Returns true iff `filePath` matches any allowlisted path.
export function isAllowlistedPath(filePath: string): boolean {
  if (typeof filePath !== "string" || filePath.length === 0) return false
  return ALLOWLIST_PATHS.some(allowed => filePath.includes(allowed))
}

// Aggregate verdict: deny when content contains a suppression pattern AND the
// file is not allowlisted. Exposed for testability.
export function shouldAllowEdit(
  filePath: string,
  content: string,
): { allow: boolean; reason?: string } {
  try {
    if (isAllowlistedPath(filePath)) {
      return { allow: true }
    }
    if (isSuppressionComment(content)) {
      return { allow: false, reason: DENY_MESSAGE }
    }
    return { allow: true }
  } catch {
    // Fail-open: never wedge the editor. A matcher bug must NOT block edits.
    return { allow: true }
  }
}

// --- Plugin entry point -----------------------------------------------------
// tool.execute.before hook: inspects the args of `edit` and `write` tool calls
// and denies when the would-be content carries a forbidden suppression
// comment. Fail-open on ANY error (a hook fault must never wedge the editor).
export default (async () => {
  return {
    "tool.execute.before": async (input: any, output: any) => {
      // Only edit/write are in scope. Other tools pass through unchanged.
      if (input?.tool !== "edit" && input?.tool !== "write") {
        return
      }

      // FAIL-OPEN WRAPPER. Any throw (malformed args, regex backtracking, etc.)
      // is swallowed and the edit is allowed. A broken hook is preferable to
      // a wedged editor — per the task spec: "any throw/exception → return
      // allow (don't wedge the editor)".
      try {
        const filePath: string =
          output?.args?.filePath ?? output?.args?.path ?? ""

        // `write` carries the full new content under `content`.
        // `edit` carries the replacement snippet under `newString`.
        // Inspect BOTH so neither tool can smuggle a suppression through.
        const writeContent: string = output?.args?.content ?? ""
        const editNew: string = output?.args?.newString ?? ""
        const text = writeContent || editNew

        if (!text) {
          // No text to scan — nothing to do.
          return
        }

        const verdict = shouldAllowEdit(filePath, text)
        if (!verdict.allow) {
          // Clean deny: structured permissionDecision + exit-0 semantics.
          // Never a hook error — the plugin process returns normally and the
          // harness surfaces `message` to the agent.
          return {
            permissionDecision: "deny",
            message: verdict.reason ?? DENY_MESSAGE,
          }
        }
      } catch {
        // Fail-open: return undefined = allow. See wrapper comment above.
        return
      }
    },
  }
}) satisfies Plugin
