/**
 * enforce-worktree.ts — deny push/merge/tag operations from inside git
 * worktrees, structurally preventing the "worktree agent silently fails
 * to advance shared branch" failure mode (spec group W).
 *
 * Per AGENTS.md "Branch-landing integrity" (a): mutations to shared/RC
 * branches MUST happen on the main checkout, never in a worktree-isolated
 * agent. A worktree agent branches off a divergent HEAD; commits go to
 * its own branch, silently failing to advance the shared branch tip.
 *
 * Mechanism:
 *   - `tool.execute.before`: if the tool is `bash` and the command matches
 *     a push/merge/tag pattern, detect whether we are in a git worktree
 *     (.git is a FILE, not a directory). If inside a worktree, DENY.
 *   - Fail-open: git unavailable / not a repo → allow.
 *
 * Env knobs:
 *   GLUDD_WORKTREE_ENFORCE=0  — disable (no-op)
 *
 * Default ON. Fail-open: any throw/exception → allow.
 *
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.
 */
import * as fs from "node:fs";
import { execSync } from "node:child_process";
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";

const WORKTREE_BLOCKED_PATTERNS = [
  /\bmake\s+git-push/,
  /\bmake\s+batch-push/,
  /\bmake\s+development-push/,
  /\bmake\s+git-merge/,
  /\bmake\s+development-merge-to-master/,
  /\bmake\s+git-tag/,
  /\bmake\s+release-cut/,
  /\bmake\s+release-promote/,
  /\bmake\s+release-recut/,
  /\bmake\s+agent-merge/,
  /\bmake\s+agent-merge-dev/,
] as readonly RegExp[];

export function isInsideWorktree(): boolean {
  try {
    const result = execSync("git rev-parse --git-dir", {
      stdio: ["pipe", "pipe", "pipe"],
    }).toString().trim();
    const gitDirStat = fs.statSync(result);
    return gitDirStat.isFile();
  } catch {
    return false;
  }
}

export function isBlockedCommand(cmd: string): boolean {
  return WORKTREE_BLOCKED_PATTERNS.some((re) => re.test(cmd));
}

const defaultImpl: HotModule = {
  "tool.execute.before": async (input, _output) => {
    if (isSubagent()) return;
    reportAlive("enforce-worktree");
    try {
      if (process.env.GLUDD_WORKTREE_ENFORCE === "0") return;

      const tool = input.tool ?? "";
      if (tool !== "bash") return;

      const cmd = input.tool_input?.command ?? "";
      if (!isBlockedCommand(cmd)) return;
      if (!isInsideWorktree()) return;

      return {
        permissionDecision: "deny",
        message:
          "WORKTREE PUSH/MERGE BLOCKED: You are inside a git worktree. " +
          "Push/merge/tag operations on shared branches must run on the " +
          "main checkout. Run this command from the main checkout instead. " +
          "Set GLUDD_WORKTREE_ENFORCE=0 to disable.",
      };
    } catch {
      // Fail-open
    }
  },
};

export default (async ({}) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return;
      const impl = loadHotModule("worktree", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
