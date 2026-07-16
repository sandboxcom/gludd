/**
 * enforce-batch-push.ts — deny push commands when CI is in_progress on the
 * target branch, structurally preventing push-cancellation.
 *
 * Per AGENTS.md "Don't Push Every Commit" rule: pushing while CI is running
 * cancels the prior CI run, resulting in zero validation. This plugin checks
 * CI state before allowing `make git-push-sandboxcom` or `make development-push`.
 *
 * Mechanism:
 *   - `tool.execute.before`: if the tool is `bash` and the command matches
 *     one of the push-target patterns, run `make ci-verdict BRANCH=<branch>`
 *     via execSync. If CI is PENDING (exit code 2), DENY the push.
 *   - Fail-open on any error (gh not found, network error, timeout).
 *
 * Env knobs:
 *   GLUDD_BATCH_PUSH_ENFORCE=0  — disable (no-op)
 *   FORCE=1                     — bypass CI check (hotfix only)
 *
 * Default ON. Fail-open: any throw/exception → allow (don't wedge the editor).
 *
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.
 */
import * as fs from "node:fs";
import { execSync } from "node:child_process";
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";

const MAKEFILE_PATH = "/Users/shawnwilson/gludd/Makefile";

function getMakefile(): string {
  try {
    return fs.readFileSync(MAKEFILE_PATH, "utf8");
  } catch {
    return "";
  }
}

function hasTarget(targetName: string): boolean {
  const mf = getMakefile();
  const re = new RegExp(`^${targetName}:`, "m");
  return re.test(mf);
}

function branchForCommand(cmd: string): string | null {
  if (/\bmake\s+git-push-sandboxcom\b/.test(cmd)) return "master";
  if (/\bmake\s+development-push\b/.test(cmd)) return "development";
  if (/\bmake\s+batch-push\b/.test(cmd)) return "master";
  return null;
}

function isCiPending(branch: string): boolean {
  try {
    const result = execSync(
      `make ci-verdict BRANCH=${branch}`,
      {
        cwd: "/Users/shawnwilson/gludd",
        timeout: 15000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    return false;
  } catch (e: any) {
    if (e.status === 2) return true;
    return false;
  }
}

export const PUSH_PATTERNS: readonly RegExp[] = Object.freeze([
  /\bmake\s+git-push-sandboxcom\b/,
  /\bmake\s+development-push\b/,
  /\bmake\s+batch-push\b/,
]) as readonly RegExp[];

export const DENY_MESSAGE =
  "CI-BUSY: a CI run is in_progress on the target branch. " +
  "Pushing now would cancel the running CI, producing zero validation. " +
  "Wait for CI to complete (use `make ci-verdict` to check), then push. " +
  "Use FORCE=1 to bypass (hotfix only). " +
  "Set GLUDD_BATCH_PUSH_ENFORCE=0 to disable.";

const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    if (isSubagent()) return;
    reportAlive("enforce-batch-push");
    try {
      if (process.env.GLUDD_BATCH_PUSH_ENFORCE === "0") return;
      if (process.env.FORCE === "1") return;

      if (input.tool !== "bash") return;
      const cmd: string = input.args?.command ?? "";
      if (!cmd) return;

      const branch = branchForCommand(cmd);
      if (!branch) return;

      if (isCiPending(branch)) {
        return {
          permissionDecision: "deny" as const,
          message: DENY_MESSAGE,
        };
      }
    } catch {
    }
  },
};

export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return;
      const impl = loadHotModule("batch-push", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
