// Workflow enforced (see AGENTS.md "CRITICAL: TDD Policy"):
// Layer map (AGENTS.md "Meta-Rule: Guardrail Policy"):
// 3. Agent prompt       — AGENTS.md "CRITICAL: TDD Policy" section
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive, getProjectRoot } from "../lib/shared.ts";
import {
  shouldAllowEdit,
  TDD_DENY_MESSAGE,
} from "../lib/enforce_tdd_logic.ts";
// ── DEFAULT IMPLEMENTATION (compiled-in fallback) ──────────────────────────
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    // process.env.OPENCODE_SUBAGENT guard — subagents inherit the
    // orchestrator's enforcement, never their own.
    if (isSubagent()) return;
    reportAlive("enforce-tdd");
    if (process.env.GLUDD_TDD_ENFORCE === "0") return;
    if (input?.tool !== "edit" && input?.tool !== "write") {
      return;
    }
    try {
      const filePath: string =
        output?.args?.filePath ?? output?.args?.path ?? "";
      if (!filePath) {
        return;
      }
      const projectRoot = getProjectRoot();
      const verdict = shouldAllowEdit(filePath, projectRoot);
      if (!verdict.allow) {
        const candidateList = verdict.candidates
          ? `\nExpected test file (one of):\n  - ${verdict.candidates.join("\n  - ")}`
          : "";
        return {
          permissionDecision: "deny",
          message: `${verdict.reason ?? TDD_DENY_MESSAGE}${candidateList}`,
        };
      }
    } catch {
      // Fail-open: never wedge the editor on a plugin error.
      return;
    }
  },
};
// ── PROXY PLUGIN (hot-reload aware) ────────────────────────────────────────
export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return;
      const impl = loadHotModule("enforce-tdd", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
