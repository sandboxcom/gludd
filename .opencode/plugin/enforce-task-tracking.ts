// Per AGENTS.md "CRITICAL: Task Self-Tracking (Anti-Forgetting)" policy:
// Hard task-registration guard — mechanical enforcement that blocks edit/write
// to src/general_ludd/**/*.py until TASKS.md has been updated.
// Layer map (see AGENTS.md "Meta-Rule: Guardrail Policy"):
// 1. Config permission  — (not applicable — plugin-level enforcement only)
// 2. Runtime hook       — .opencode/plugin/enforce-task-tracking.ts (tool.execute.before deny)
// 3. Agent prompt       — AGENTS.md "CRITICAL: Task Self-Tracking" section
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.
import type { Plugin } from "@opencode-ai/plugin";
import * as fs from "node:fs";
import * as path from "node:path";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive, getProjectRoot, readJsonFile, writeJsonFile } from "../lib/shared.ts";

const STATE_FILE = "/tmp/gludd-task-tracking.json";

const SRC_PREFIX = "src/general_ludd/";

interface TaskTrackingState {
  pid: number;
  last_tasks_md_mtime: number;
  tasks_md_path: string;
}

const DENY_MESSAGE =
  "TASK TRACKING VIOLATION: TASKS.md has not been updated. " +
  "Before editing src/ implementation code, add a task entry to TASKS.md " +
  "describing the work. See AGENTS.md \"CRITICAL: Task Self-Tracking " +
  "(Anti-Forgetting)\" policy. " +
  "Workflow: (1) add an unchecked entry to TASKS.md for the work, " +
  "(2) THEN edit the implementation file.";

function isImplementationFile(filePath: string): boolean {
  if (typeof filePath !== "string" || filePath.length === 0) return false;
  const normalized = filePath.replace(/\\/g, "/");
  if (normalized.includes("tests/")) return false;
  if (normalized.includes(".opencode/")) return false;
  return normalized.includes(SRC_PREFIX) && normalized.endsWith(".py");
}

function shouldAllowEdit(
  filePath: string,
  projectRoot: string,
): { allow: boolean; reason?: string } {
  try {
    if (!isImplementationFile(filePath)) return { allow: true };

    const tasksPath = path.join(projectRoot, "TASKS.md");
    if (!fs.existsSync(tasksPath)) return { allow: true };

    const state = readJsonFile<TaskTrackingState>(STATE_FILE, {
      pid: process.pid,
      last_tasks_md_mtime: 0,
      tasks_md_path: tasksPath,
    });

    const currentMtime = fs.statSync(tasksPath).mtimeMs;

    if (state.last_tasks_md_mtime === 0) {
      state.last_tasks_md_mtime = currentMtime;
      state.tasks_md_path = tasksPath;
      state.pid = process.pid;
      writeJsonFile(STATE_FILE, state);
      return { allow: true };
    }

    if (currentMtime > state.last_tasks_md_mtime) {
      state.last_tasks_md_mtime = currentMtime;
      writeJsonFile(STATE_FILE, state);
      return { allow: true };
    }

    return {
      allow: false,
      reason:
        DENY_MESSAGE +
        " (TASKS.md mtime: " +
        new Date(state.last_tasks_md_mtime).toISOString() +
        " — unchanged since last recorded update)",
    };
  } catch {
    return { allow: true };
  }
}

const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    if (isSubagent()) return;
    reportAlive("enforce-task-tracking");
    if (process.env.GLUDD_TASK_TRACKING_ENFORCE === "0") return;
    if (input?.tool !== "edit" && input?.tool !== "write") return;

    try {
      const filePath: string =
        output?.args?.filePath ?? output?.args?.path ?? "";
      if (!filePath) return;

      const projectRoot = getProjectRoot();
      const verdict = shouldAllowEdit(filePath, projectRoot);
      if (!verdict.allow) {
        return {
          permissionDecision: "deny",
          message: verdict.reason ?? DENY_MESSAGE,
        };
      }
    } catch {
      // Fail-open: never wedge the editor on a plugin error.
    }
  },
};

export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return;
      const impl = loadHotModule("task-tracking", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
