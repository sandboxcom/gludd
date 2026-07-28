// Per AGENTS.md "No Lint-Suppression Comments" policy: `# noqa`, `# type: ignore`.
// This guard is intentionally hard-coded ON; suppression comments bypass the
// quality gate itself, so a per-plugin disable switch would defeat the policy.
// Layer map (see AGENTS.md "Meta-Rule: Guardrail Policy"):
// 3. Agent prompt       — AGENTS.md "No Lint-Suppression Comments" section
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";

const DENY_MESSAGE =
  "Lint-suppression comments forbidden. Fix the underlying issue. " +
  "See AGENTS.md Guardrail Integrity Policy.";

const SUPPRESSION_PATTERNS = [
  /#\s*noqa/,
  /#\s*type:\s*ignore/,
  /#\s*pylint:/,
  /#\s*fmt:\s*(?:off|skip|on)/,
  /#\s*isort:\s*skip/,
];

const ALLOWLIST_PATHS = [
  "src/general_ludd/security/fix_not_disable.py",
  "tests/unit/test_type_safety_guardrails.py",
];

function isSuppressionComment(text: unknown): boolean {
  if (typeof text !== "string" || text.length === 0) return false;
  return SUPPRESSION_PATTERNS.some((pattern) => pattern.test(text));
}

function isAllowlistedPath(filePath: unknown): boolean {
  if (typeof filePath !== "string" || filePath.length === 0) return false;
  const normalized = filePath.replaceAll("\\", "/");
  return ALLOWLIST_PATHS.some(
    (allowed) => normalized === allowed || normalized.endsWith(`/${allowed}`),
  );
}

const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    if (isSubagent()) return;
    reportAlive("enforce-no-suppressions");
    if (input?.tool !== "edit" && input?.tool !== "write") return;

    // Keep the runtime hook self-contained. Hot modules are built from this
    // fallback body without external imports, so imported helper calls would
    // become undefined and silently disable enforcement after a hot reload.
    try {
      const rawPath = output?.args?.filePath ?? output?.args?.path;
      if (isAllowlistedPath(rawPath)) {
        return;
      }

      const rawWrite = output?.args?.content;
      const rawEdit = output?.args?.newString;
      const text = typeof rawWrite === "string" && rawWrite.length > 0
        ? rawWrite
        : typeof rawEdit === "string"
          ? rawEdit
          : "";
      if (!text) return;

      if (isSuppressionComment(text)) {
        return { permissionDecision: "deny", message: DENY_MESSAGE };
      }
    } catch {
      // Fail open: allow a malformed editor payload rather than wedge the process.
      return;
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
