// Per AGENTS.md "Clean Tree Before Dispatch" (2026-07-08): subagents edit
// - Fail-open on any error (git not found, not a repo, etc.) so the editor
// Default ON. Fail-open: any throw/exception → allow (don't wedge the editor).
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
import type { Plugin } from "@opencode-ai/plugin";
import { createRequire } from "node:module";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";
const nodeRequire = typeof require === "function" ? require : createRequire(import.meta.url);
function execSync(...args: any[]): Buffer {
  return nodeRequire("node:child_" + "process").execSync(...args);
}
export const DISPATCH_TOOLS = Object.freeze(["task", "agent", "workflow"]) as readonly string[];
export const DENY_MESSAGE_PREFIX = "DIRTY TREE";
// Empty = clean tree (or git unavailable — fail-open).
export function getGitStatus(): string {
  try {
    return execSync("git status --porcelain", {
      stdio: ["pipe", "pipe", "pipe"],
    }).toString().trim();
  } catch {
    return "";
  }
}
export function isTreeDirty(): boolean {
  return getGitStatus().length > 0;
}
export function countDirtyFiles(status: string): number {
  if (!status.trim()) return 0;
  return status
    .trim()
    .split("\n")
    .filter((l) => l.trim()).length;
}
export function buildDenyMessage(count: number): string {
  return (
    `DIRTY TREE: ${count} uncommitted file(s). Commit or stash before dispatching new work. ` +
    `Run \`make git-status\` to see the files, then \`make git-add FILES='...' && make ship-commit MSG='...'\` to commit. ` +
    `Or \`make git-stash\` to stash temporarily. ` +
    `Set GLUDD_CLEAN_TREE_ENFORCE=0 to disable.`
  );
}
// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return;
    reportAlive("enforce-clean-tree");
    try {
      if (process.env.GLUDD_CLEAN_TREE_ENFORCE === "0") return;
      const tool = input.tool ?? "";
      if (!DISPATCH_TOOLS.includes(tool)) return;
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
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return;
      const impl = loadHotModule("clean-tree", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
