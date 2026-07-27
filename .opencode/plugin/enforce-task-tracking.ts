// Enforce-task-tracking: hard task-registration guard.
//
// Block edit/write to src/general_ludd/**/*.py until TASKS.md has been
// updated (mtime change detected). The agent MUST:
//   1. Add an unchecked entry to TASKS.md describing the work
//   2. Save TASKS.md (updates mtime, satisfying the guard)
//   3. Edit/write src/general_ludd/<module>.py — ALLOWED
//
// Skip step 1 → step 3 is mechanically DENIED.
//
// AGENTS.md policy: "CRITICAL: Task Self-Tracking (Anti-Forgetting)"
// HOT-RELOAD: proxy pattern from hot_reload.ts.

import type { Plugin } from "@opencode-ai/plugin";
import * as fs from "node:fs";
import * as path from "node:path";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import {
  isSubagent,
  reportAlive,
  getProjectRoot,
  readJsonFile,
  writeJsonFile,
} from "../lib/shared.ts";

const STATE_FILE = "/tmp/gludd-task-tracking.json";
const SRC_PREFIX = "src/general_ludd/";
const TESTS_PREFIX = "tests/";
const OPENCODE_PREFIX = ".opencode/";

const DENY_MESSAGE =
  "TASK TRACKING VIOLATION: update TASKS.md BEFORE editing implementation " +
  "code. The AGENTS.md \"Task Self-Tracking (Anti-Forgetting)\" policy " +
  "requires every src/general_ludd/ edit to be preceded by a TASKS.md " +
  "update describing the work. Workflow: (1) add an unchecked entry to " +
  "TASKS.md, (2) save TASKS.md, (3) THEN edit the implementation file.";

interface TaskTrackingState {
  pid: number;
  last_tasks_md_mtime: number;
  tasks_md_path: string;
}

function isImplementationFile(filePath: string): boolean {
  if (typeof filePath !== "string" || filePath.length === 0) return false;
  const normalized = filePath.replace(/\\/g, "/");
  if (normalized.includes(TESTS_PREFIX)) return false;
  if (normalized.includes(OPENCODE_PREFIX)) return false;
  return normalized.includes(SRC_PREFIX) && normalized.endsWith(".py");
}

function shouldAllowEdit(
  filePath: string,
  projectRoot: string,
): { allow: boolean; reason?: string } {
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
}

const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    if (isSubagent()) return;
    reportAlive("enforce-task-tracking");
    if (process.env.GLUDD_TASK_TRACKING_ENFORCE === "0") return;
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
        return {
          permissionDecision: "deny",
          message: verdict.reason ?? DENY_MESSAGE,
        };
      }
    } catch {
      return { allow: true };
    }
  },
};

export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      try {
        if (isSubagent()) return;
        const impl = loadHotModule("task-tracking", defaultImpl);
        const fn = impl["tool.execute.before"];
        return fn ? await fn(input, output) : undefined;
      } catch {
        return { allow: true };
      }
    },
  };
}) satisfies Plugin;
