/**
 * enforce-no-suppressions.ts — deny lint-suppression comments in edited/written
 * files, structurally preventing the "suppression comments hide real bugs" problem.
 *
 * Per AGENTS.md "No Lint-Suppression Comments" policy: `# noqa`, `# type: ignore`,
 * `# pylint: disable=...`, `# fmt: off/skip/on`, `# isort:skip` are FORBIDDEN
 * in `src/` and `tests/`. Fix the underlying issue; never silence the warning.
 *
 * Layer map (see AGENTS.md "Meta-Rule: Guardrail Policy"):
 *   1. Config permission  — n/a (this is a content-based check, not a tool ban)
 *   2. Runtime hook       — THIS FILE (tool.execute.before on edit/write)
 *   3. Agent prompt       — AGENTS.md "No Lint-Suppression Comments" section
 *   + Behavior pin         — tests/unit/test_no_suppression_comments_plugin.py
 *   + Repo-wide scan       — tests/unit/test_type_safety_guardrails.py
 *
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
 * check /tmp/gludd-hot-enforce-no-suppressions.js on every invocation.  If present
 * and newer than cached, the hot module's hook overrides the compiled-in
 * default.  Run `make hot-reload-plugins` after editing this file.
 */
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";

const SUPPRESSION_PATTERNS: RegExp[] = [
  /#\s*noqa/,
  /#\s*type:\s*ignore/,
  /#\s*pylint:/,
  /#\s*fmt:\s*(?:off|skip|on)/,
  /#\s*isort:\s*skip/,
];

const ALLOWLIST_PATHS: string[] = [
  "src/general_ludd/security/fix_not_disable.py",
  "tests/unit/test_type_safety_guardrails.py",
];

const DENY_MESSAGE =
  "Lint-suppression comments forbidden. Fix the underlying issue. " +
  "See AGENTS.md Guardrail Integrity Policy.";

export function isSuppressionComment(text: string): boolean {
  if (typeof text !== "string" || text.length === 0) return false;
  return SUPPRESSION_PATTERNS.some(re => re.test(text));
}

export function isAllowlistedPath(filePath: string): boolean {
  if (typeof filePath !== "string" || filePath.length === 0) return false;
  return ALLOWLIST_PATHS.some(allowed => filePath.includes(allowed));
}

export function shouldAllowEdit(
  filePath: string,
  content: string,
): { allow: boolean; reason?: string } {
  try {
    if (isAllowlistedPath(filePath)) {
      return { allow: true };
    }
    if (isSuppressionComment(content)) {
      return { allow: false, reason: DENY_MESSAGE };
    }
    return { allow: true };
  } catch {
    return { allow: true };
  }
}

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return;
    reportAlive("enforce-no-suppressions");
    if (process.env.GLUDD_NO_SUPPRESSIONS_ENFORCE === "0") return;
    if (input?.tool !== "edit" && input?.tool !== "write") {
      return;
    }

    try {
      const filePath: string =
        output?.args?.filePath ?? output?.args?.path ?? "";

      const writeContent: string = output?.args?.content ?? "";
      const editNew: string = output?.args?.newString ?? "";
      const text = writeContent || editNew;

      if (!text) {
        return;
      }

      const verdict = shouldAllowEdit(filePath, text);
      if (!verdict.allow) {
        return {
          permissionDecision: "deny",
          message: verdict.reason ?? DENY_MESSAGE,
        };
      }
    } catch {
      // Fail-open: never wedge the editor on a plugin error.
      return;
    }
  },
};

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return;
      const impl = loadHotModule("enforce-no-suppressions", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
