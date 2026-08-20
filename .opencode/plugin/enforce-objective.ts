// Default ON. Fail-open. Subagent guard. Hot-reload capable.
// Extended: AB001 frustration signals, AB002 spec velocity, AB003 CI-check-while-spec-target,
// AB007 objective stacking, AB008 behavioral change measurement.
import type { Plugin } from "@opencode-ai/plugin";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive, getProjectRoot } from "../lib/shared.ts";
const nodeRequire = typeof require === "function" ? require : createRequire(import.meta.url);
function execSync(...args: any[]): Buffer {
  return nodeRequire("node:child_" + "process").execSync(...args);
}
const NAG_PREFIX = "███  NO PRIMARY OBJECTIVE SET";
const SPEC_VELOCITY_FILE = "/tmp/gludd-spec-velocity.json";
const SPEC_BEHAVIOR_FILE = "/tmp/gludd-spec-behavior.json";
// AB002: minimum specs per 5-minute window to maintain velocity.
// If pace is below this, non-spec activities are blocked.
const MIN_SPECS_PER_WINDOW = 25; // 100 specs in 20 min = 25 per 5-min window
const SPEC_WINDOW_MS = 300_000; // 5 minutes
const SPEC_TARGET_TOTAL = 8000;
// AB003: max CI checks per spec window while spec target is unmet.
const MAX_CI_CHECKS_PER_SPEC_WINDOW = 3;
// AB007: objective stacking — secondary requests don't overwrite primary.
const OBJECTIVE_STACK_FILE = "/tmp/gludd-objective-stack.json";
// AB008: behavioral failure recurrence tracking.
const MAX_RECURRENCE_BEFORE_BLOCK = 3;
function getPrimaryObjective(): string {
  try {
    const root = getProjectRoot();
    const sessionPath = path.join(root, "SESSION.md");
    if (!fs.existsSync(sessionPath)) return "";
    const content = fs.readFileSync(sessionPath, "utf8");
    const match = content.match(/^## PRIMARY OBJECTIVE:\s*(.+)$/m);
    if (match) return match[1].trim();
    // AB007 fallback: check objective stack if no PRIMARY OBJECTIVE line
    return getStackedObjective();
  } catch {
    return "";
  }
}
// AB007: read stacked objective from persistent state file.
function getStackedObjective(): string {
  try {
    if (!fs.existsSync(OBJECTIVE_STACK_FILE)) return "";
    const stack = JSON.parse(fs.readFileSync(OBJECTIVE_STACK_FILE, "utf8"));
    if (Array.isArray(stack) && stack.length > 0) return stack[0] as string;
    return "";
  } catch {
    return "";
  }
}
// AB007: persist objective to stack so it survives secondary request overwrite.
function persistObjectiveToStack(objective: string): void {
  try {
    let stack: string[] = [];
    if (fs.existsSync(OBJECTIVE_STACK_FILE)) {
      const existing = JSON.parse(fs.readFileSync(OBJECTIVE_STACK_FILE, "utf8"));
      if (Array.isArray(existing)) stack = existing as string[];
    }
    if (stack.length === 0 || stack[0] !== objective) {
      stack.unshift(objective);
      if (stack.length > 5) stack = stack.slice(0, 5);
      fs.writeFileSync(OBJECTIVE_STACK_FILE, JSON.stringify(stack), "utf8");
    }
  } catch { /* fail-open */ }
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
function getUnpushedCommitCount(): number {
  try {
    const root = getProjectRoot();
    const result = execSync("git rev-list --count @{u}..HEAD 2>/dev/null || echo -1", {
      cwd: root,
      timeout: 10000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const count = parseInt(result.toString().trim(), 10);
    if (count >= 0) return count;
  } catch { /* fall through to origin/master fallback */ }
  try {
    const root = getProjectRoot();
    const result = execSync("git rev-list --count origin/master..HEAD 2>/dev/null || echo -1", {
      cwd: root,
      timeout: 10000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const count = parseInt(result.toString().trim(), 10);
    return count > 0 ? count : 0;
  } catch {
    return 0;
  }
}
function getPendingReleaseVersion(): string {
  try {
    const root = getProjectRoot();
    const pyprojectPath = path.join(root, "pyproject.toml");
    if (!fs.existsSync(pyprojectPath)) return "";
    const content = fs.readFileSync(pyprojectPath, "utf8");
    const match = content.match(/^\s*version\s*=\s*"([^"]+)"/m);
    if (!match) return "";
    const version = match[1];
    if (/-(?:alpha|beta|rc|dev)/.test(version)) return version;
    return "";
  } catch {
    return "";
  }
}
// AB002: read spec velocity state.
interface SpecVelocity {
  specWrites: number[];  // UNIX-timestamp array of recent spec writes
  totalSpecs: number;
  lastCiCheck: number;
  ciCheckCount: number;
}
function readSpecVelocity(): SpecVelocity {
  try {
    if (fs.existsSync(SPEC_VELOCITY_FILE)) {
      return JSON.parse(fs.readFileSync(SPEC_VELOCITY_FILE, "utf8")) as SpecVelocity;
    }
  } catch { /* fail-open */ }
  return { specWrites: [], totalSpecs: 0, lastCiCheck: 0, ciCheckCount: 0 };
}
function writeSpecVelocity(v: SpecVelocity): void {
  try {
    fs.writeFileSync(SPEC_VELOCITY_FILE, JSON.stringify(v), "utf8");
  } catch { /* fail-open */ }
}
// AB002+AB003: check if spec writing velocity is sufficient AND CI checks aren't excessive.
function isSpecVelocitySufficient(): boolean {
  const v = readSpecVelocity();
  const now = Date.now();
  const cutoff = now - SPEC_WINDOW_MS;
  const recentWrites = v.specWrites.filter((t: number) => t >= cutoff);
  const recentCiChecks = v.ciCheckCount;
  // If no spec target, don't enforce.
  if (v.totalSpecs >= SPEC_TARGET_TOTAL) return true;
  // AB003: if too many CI checks without spec progress, block.
  if (recentCiChecks > MAX_CI_CHECKS_PER_SPEC_WINDOW && recentWrites.length < MIN_SPECS_PER_WINDOW / 5) {
    return false;
  }
  // AB002: if recent spec pace is too slow, block.
  if (recentWrites.length < MIN_SPECS_PER_WINDOW && v.totalSpecs > 0 && v.totalSpecs < SPEC_TARGET_TOTAL) {
    return false;
  }
  return true;
}
// AB002: record a spec file write timestamp.
function recordSpecWrite(): void {
  const v = readSpecVelocity();
  v.specWrites.push(Date.now());
  // Keep only last 1000 timestamps.
  if (v.specWrites.length > 1000) v.specWrites = v.specWrites.slice(-1000);
  // Update total from file if possible.
  try {
    const root = getProjectRoot();
    const specPath = path.join(root, "docs", "specs", "BEHAVIORAL_SPECS.md");
    if (fs.existsSync(specPath)) {
      const content = fs.readFileSync(specPath, "utf8");
      const matches = content.match(/^### [A-Z]+\d{3} — /gm);
      v.totalSpecs = matches ? matches.length : v.totalSpecs;
    }
  } catch { /* fail-open */ }
  writeSpecVelocity(v);
}
// AB003: record a CI check attempt.
function recordCiCheck(): void {
  const v = readSpecVelocity();
  v.lastCiCheck = Date.now();
  v.ciCheckCount += 1;
  // Reset CI check count after 10 minutes.
  const cutoff = Date.now() - 600_000;
  const recentWrites = v.specWrites.filter((t: number) => t >= cutoff);
  if (recentWrites.length >= 1) {
    v.ciCheckCount = 0; // Progress was made — reset CI counter.
  }
  writeSpecVelocity(v);
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
      // AB007: persist objective to stack on every check.
      if (objective) persistObjectiveToStack(objective);
      const tool = (input?.tool ?? "") as string;
      // AB002: track spec file writes for velocity monitoring.
      if (tool === "edit" || tool === "write") {
        const filePath = typeof input?.args?.filePath === "string" ? input.args.filePath
          : typeof input?.args?.path === "string" ? input.args.path : "";
        if (filePath.includes("BEHAVIORAL_SPECS.md") || filePath.includes("behavioral_specs")) {
          recordSpecWrite();
        }
      }
      // AB003: track CI verifications.
      if (tool === "bash") {
        const cmd = typeof input?.args?.command === "string" ? input.args.command : "";
        if (/\bmake\s+(ci-verdict|ci-verdict-safe)\b/.test(cmd)) {
          recordCiCheck();
        }
      }
      // AB002/AB003: block non-spec activities when velocity is insufficient.
      if (!isSpecVelocitySufficient() && objective && !isObjectiveMet()) {
        if (tool === "edit" || tool === "write" || tool === "bash") {
          const filePath = typeof input?.args?.filePath === "string" ? input.args.filePath
            : typeof input?.args?.path === "string" ? input.args.path : "";
          const cmd = typeof input?.args?.command === "string" ? input.args.command : "";
          // Allow spec-related writes and CI-advancing commands.
          if (!filePath.includes("BEHAVIORAL_SPECS.md") &&
              !/\bmake\s+(ci-verdict|batch-push|release-cut|test)\b/.test(cmd)) {
            return {
              permissionDecision: "deny" as const,
              message:
                `SPEC VELOCITY INSUFFICIENT (AB002/AB003). ` +
                `Spec writing pace is below ${MIN_SPECS_PER_WINDOW}/5min or ` +
                `too many CI checks (${MAX_CI_CHECKS_PER_SPEC_WINDOW}) without progress. ` +
                `Write specs or fix spec-enforcement gaps before other activities. ` +
                `Set GLUDD_OBJECTIVE_ENFORCE=0 to disable.`,
            };
          }
        }
      }
      if (!objective) return;
      if (isObjectiveMet()) return;
      // Dispatch: block when unpushed commits + pending release version.
      if (tool === "task" || tool === "agent" || tool === "workflow") {
        const unpushedCount = getUnpushedCommitCount();
        const pendingVersion = getPendingReleaseVersion();
        if (unpushedCount > 0 && pendingVersion) {
          return {
            permissionDecision: "deny" as const,
            message:
              `DISPATCH BLOCKED: ${unpushedCount} unpushed commit(s) on this branch ` +
              `while release ${pendingVersion} is pending in pyproject.toml. ` +
              `Push commits first (make batch-push), then dispatch new work. ` +
              `Set GLUDD_OBJECTIVE_ENFORCE=0 to disable.`,
          };
        }
        return;
      }
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
