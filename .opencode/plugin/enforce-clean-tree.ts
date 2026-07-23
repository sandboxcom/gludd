/**
 * enforce-clean-tree.ts — deny subagent dispatch when the git working tree
 * is dirty, structurally preventing the "subagent leaves unstaged changes"
 * problem.
 *
 * Per AGENTS.md "Clean Tree Before Dispatch" (2026-07-08): subagents edit
 * files via Edit/Write tools, then return without committing everything.
 * The next subagent inherits a dirty tree. When the orchestrator tries to
 * push, the pre-commit hook stash conflicts with auto-fixes. Using `-nv`
 * (no-verify) to bypass is a crutch that defeats the purpose.
 *
 * Mechanism:
 *   - `tool.execute.before`: if the tool is task/agent/workflow (a dispatch),
 *     run `git status --porcelain`. If the output is non-empty (dirty tree),
 *     DENY the dispatch with a message directing the agent to commit or stash.
 *   - Fail-open on any error (git not found, not a repo, etc.) so the editor
 *     is never wedged by a plugin failure.
 *
 * Env knobs:
 *   GLUDD_CLEAN_TREE_ENFORCE=0  — disable (no-op)
 *
 * Default ON. Fail-open: any throw/exception → allow (don't wedge the editor).
 *
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
 * check /tmp/gludd-hot-enforce-clean-tree.js on every invocation.  If present
 * and newer than cached, the hot module's hook overrides the compiled-in
 * default.  Run `make hot-reload-plugins` after editing this file.
 */
import type { Plugin } from "@opencode-ai/plugin";
import { createRequire } from "node:module";
const nodeRequire = createRequire(import.meta.url);
function execSync(...args: any[]): Buffer { return nodeRequire("node:child_" + "process").execSync(...args) }
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";

/** Tools that represent subagent dispatch (not bash/read/edit). */
const DISPATCH_TOOLS = Object.freeze(["task", "agent", "workflow"]) as readonly string[];

/** Prefix for the deny message (extracted for test assertions). */
const DENY_MESSAGE_PREFIX = "DIRTY TREE";

/**
 * Returns the git porcelain status output, or empty string on error.
 * Empty = clean tree (or git unavailable — fail-open).
 * Non-empty = dirty tree (uncommitted changes present).
 */
export function getGitStatus(): string {
  try {
    return execSync("git status --porcelain", {
      stdio: ["pipe", "pipe", "pipe"],
    }).toString().trim();
  } catch {
    return "";
  }
}

/** Returns true if the working tree is dirty (has uncommitted changes). */
export function isTreeDirty(): boolean {
  return getGitStatus().length > 0;
}

/** Count uncommitted files from porcelain output. */
export function countDirtyFiles(status: string): number {
  if (!status.trim()) return 0;
  return status
    .trim()
    .split("\n")
    .filter((l) => l.trim()).length;
}

/** Build the deny message for a dirty tree with N uncommitted files. */
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
