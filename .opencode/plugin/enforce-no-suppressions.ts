// Per AGENTS.md "No Lint-Suppression Comments" policy: `# noqa`, `# type: ignore`,
// Layer map (see AGENTS.md "Meta-Rule: Guardrail Policy"):
// 3. Agent prompt       — AGENTS.md "No Lint-Suppression Comments" section
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";

const SUPPRESSION_PATTERNS: RegExp[] = [
  /#\s*noqa/, /#\s*type:\s*ignore/, /#\s*pylint:/,
  /#\s*fmt:\s*(?:off|skip|on)/, /#\s*isort:\s*skip/,
];
const ALLOWLIST_PATHS = [
  "src/general_ludd/security/fix_not_disable.py",
  "tests/unit/test_type_safety_guardrails.py",
];

function shouldAllowEdit(
  filePath: string,
  content: string,
): { allow: boolean; reason?: string } {
  try {
    if (ALLOWLIST_PATHS.some((allowed) => filePath.includes(allowed))) {
      return { allow: true };
    }
    if (typeof content === "string" && SUPPRESSION_PATTERNS.some((re) => re.test(content))) {
      return { allow: false, reason: DENY_MESSAGE };
    }
    return { allow: true };
  } catch {
    return { allow: true };
  }
}

const DENY_MESSAGE =
  "Lint-suppression comments forbidden. Fix the underlying issue. " +
  "See AGENTS.md Guardrail Integrity Policy.";

const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    if (isSubagent()) return;
    reportAlive("enforce-no-suppressions");
    if (process.env.GLUDD_NO_SUPPRESSIONS_ENFORCE === "0") return;
    if (input?.tool !== "edit" && input?.tool !== "write") return;
    try {
      const filePath: string = output?.args?.filePath ?? output?.args?.path ?? "";
      const writeContent: string = output?.args?.content ?? "";
      const editNew: string = output?.args?.newString ?? "";
      const text = writeContent || editNew;
      if (!text) return;
      const verdict = shouldAllowEdit(filePath, text);
      if (!verdict.allow) {
        return { permissionDecision: "deny", message: verdict.reason ?? DENY_MESSAGE };
      }
    } catch {
      // Fail-open: never wedge the editor on a plugin error.
    }
  },
};

export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return;
      const impl = loadHotModule("enforce-no-suppressions", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
