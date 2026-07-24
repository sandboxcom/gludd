// Per AGENTS.md "Clean Tree Before Dispatch" (2026-07-08): subagents edit
// - Fail-open on any error (git not found, not a repo, etc.) so the editor
// Default ON. Fail-open: any throw/exception → allow (don't wedge the editor).
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";
import {
  getDispatchTools, getGitStatus, countDirtyFiles, buildDenyMessage,
} from "../lib/plugin_test_exports.ts";

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    if (isSubagent()) return;
    reportAlive("enforce-clean-tree");
    try {
      if (process.env.GLUDD_CLEAN_TREE_ENFORCE === "0") return;
      const tool = input.tool ?? "";
      if (!getDispatchTools().includes(tool)) return;
      const status = getGitStatus();
      if (status.length > 0) {
        const count = countDirtyFiles(status);
        return {
          permissionDecision: "deny",
          message: buildDenyMessage(count),
        };
      }
    } catch {
      // Fail-open: never wedge the editor on a plugin error.
    }
  },
};
// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (async ({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return;
      const impl = loadHotModule("clean-tree", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
