/**
 * enforce-objective.ts — ties tool calls to PRIMARY OBJECTIVE in SESSION.md.
 *
 * v2 (2026-07-19): Upgraded from ADVISORY to BLOCKING per BEHAVIORAL_SPECS.md
 * O02-O03. Tangential tool calls while the PRIMARY OBJECTIVE is unmet are now
 * DENIED (not just console.warn). Dispatch, read, and CI-advancing tools
 * always pass through.
 *
 * Reads SESSION.md for a `PRIMARY OBJECTIVE:` field (e.g. "GREEN CI ON
 * DEVELOPMENT → 12/12 ARTIFACTS"). When set and not yet met, non-dispatch /
 * non-read / non-CI-advancing tool calls are BLOCKED with a deny.
 * When NOT set, a nag is injected at response time.
 *
 * Objective-met detection: if the objective text mentions CI GREEN, the
 * plugin reads /tmp/gludd-watchdog-ci.json for `last_ci_status === "SUCCESS"`.
 * Non-CI objectives are treated as not-yet-met (blocking).
 *
 * BLOCKING by default. Env: GLUDD_OBJECTIVE_ENFORCE=0 to disable.
 * FORCE=1 bypasses the objective check (hotfix only).
 *
 * Env knobs:
 *   GLUDD_OBJECTIVE_ENFORCE=0 — disable entirely
 *
 * Default ON. Fail-open. Subagent guard. Hot-reload capable.
 */
import type { Plugin } from "@opencode-ai/plugin";
import * as fs from "node:fs";
import * as path from "node:path";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive, getProjectRoot } from "../lib/shared.ts";

/** Prefix for objective nag injection. */
export const NAG_PREFIX = "███  NO PRIMARY OBJECTIVE SET";

/** Extract the PRIMARY OBJECTIVE from SESSION.md. Returns "" if missing. */
export function getPrimaryObjective(): string {
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

/** True if the cached CI status indicates success. */
export function isCiGreenFromCache(): boolean {
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

/** True when the primary objective is already met. */
export function isObjectiveMet(): boolean {
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
    "text.complete": async (output: any) => {
      if (isSubagent()) return;
      const impl = loadHotModule("objective", defaultImpl);
      const fn = impl["text.complete"];
      return fn ? await fn(output) : undefined;
    },
  };
}) satisfies Plugin;
