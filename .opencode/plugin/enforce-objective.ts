// Default ON. Fail-open. Subagent guard. Hot-reload capable.
import type { Plugin } from "@opencode-ai/plugin";
import * as fs from "node:fs";
import * as path from "node:path";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive, getProjectRoot } from "../lib/shared.ts";
const NAG_PREFIX = "███  NO PRIMARY OBJECTIVE SET";
function getPrimaryObjective(): string {
  try {
    const root = getProjectRoot();
    const sessionPath = path.join(root, "SESSION.md");
    if (!fs.existsSync(sessionPath)) return "";
    const content = fs.readFileSync(sessionPath, "utf8");
    const match = content.match(/^## PRIMARY OBJECTIVE:\s*(.+)$/m);
    return match ? match[1].trim() : "";
  } catch {
    return "";
  }
}
function isCiGreenFromCache(): boolean {
  try {
    const p = "/tmp/gludd-watchdog-ci.json";
    if (!fs.existsSync(p)) return false;
    const ci = JSON.parse(fs.readFileSync(p, "utf8"));
    const lastCheck = typeof ci.last_ci_check === "number" ? ci.last_ci_check : 0;
    if (Date.now() - lastCheck > 600_000) return false;
    return ci.last_ci_status === "SUCCESS";
  } catch {
    return false;
  }
}
function isObjectiveMet(): boolean {
  const obj = getPrimaryObjective();
  if (!obj) return true;
  if (/\bCI\s*GREEN\b|\bGREEN\s*CI\b/i.test(obj)) {
    return isCiGreenFromCache();
  }
  return false;
}
// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, _output) => {
    if (isSubagent()) return;
    reportAlive("enforce-objective");
    try {
      if (process.env.GLUDD_OBJECTIVE_ENFORCE === "0") return;
      if (process.env.FORCE === "1") return;
      const objective = getPrimaryObjective();
      if (!objective) return;
      if (isObjectiveMet()) return;
      const tool = (input?.tool ?? "") as string;
      // Dispatch and read tools always allowed.
      if (tool === "task" || tool === "agent" || tool === "workflow") return;
      if (tool === "read" || tool === "grep" || tool === "glob") return;
      // Bash: allow CI-advancing / test / commit targets.
      if (tool === "bash") {
        const cmd = typeof input?.args?.command === "string" ? input.args.command : "";
        if (
          /\bmake\s+(ci-verdict|batch-push|release-cut|verify-release|git-push|git-commit|ship-commit|test|gate|lint|typecheck)\b/.test(
            cmd,
          )
        )
          return;
      }
      // Non-allowed tool while objective unmet → BLOCK
      if (tool === "edit" || tool === "write" || tool === "bash") {
        return {
          permissionDecision: "deny" as const,
          message:
            `PRIMARY OBJECTIVE not yet met: "${objective}". ` +
            `Tool "${tool}" may be tangential to the objective. ` +
            `Set GLUDD_OBJECTIVE_ENFORCE=0 to disable, or FORCE=1 to bypass.`,
        };
      }
      // Other tools (unknown) — console.warn advisory as fallback
      console.warn(
        `[enforce-objective] PRIMARY OBJECTIVE not yet met: "${objective}". ` +
          `Tool "${tool}" may be tangential. Set GLUDD_OBJECTIVE_ENFORCE=0 to disable.`,
      );
    } catch {
      // fail-open
    }
  },
  "text.complete": async (output) => {
    if (isSubagent()) return;
    try {
      const objective = getPrimaryObjective();
      if (objective) return;
      const nag = `\n${NAG_PREFIX}  ███\n\n` +
        `SESSION.md is missing a PRIMARY OBJECTIVE: field.\n` +
        `Add one so tool calls stay focused:\n\n` +
        `  ## PRIMARY OBJECTIVE: GREEN CI ON DEVELOPMENT → v0.1.0-beta.2 WITH 12/12 ARTIFACTS\n\n`;
      if (output && typeof output === "object" && "text" in output) {
        return { ...(output as Record<string, unknown>), text: nag + (output as Record<string, unknown>).text };
      }
    } catch {
      // fail-open
    }
  },
};
// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (async ({}) => {
  return {
    "tool.execute.before": async (input: any, output: any) => {
      if (isSubagent()) return;
      const impl = loadHotModule("objective", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
    // opencode 1.17.9 only registers "experimental.text.complete" — bare
    // "text.complete" is rejected by Plugin.add and crashes opencode at boot.
    "experimental.text.complete": async (_input: any, output: any) => {
      if (isSubagent()) return output;
      const impl = loadHotModule("objective", defaultImpl);
      const fn = impl["text.complete"] || impl["experimental.text.complete"];
      return fn ? await fn(output) : output;
    },
  };
}) satisfies Plugin;
