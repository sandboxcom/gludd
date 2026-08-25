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
// Additionally, text.complete injects advisory nags when TASKS.md goes
// stale across multiple response cycles (1 → NOTE, 3 → WARNING, 5 →
// CRITICAL), and system.transform injects a task-tracking directive
// into the agent's system prompt.
//
// AGENTS.md policy: "CRITICAL: Task Self-Tracking (Anti-Forgetting)"
// Spec: docs/specs/SPEC_TASK_TRACKING_ENFORCEMENT.md
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
  missed_update_count: number;
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
    missed_update_count: 0,
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
    state.missed_update_count = 0;
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

  // ------------------------------------------------------------------
  // text.complete — advisory injection when TASKS.md is stale.
  // ------------------------------------------------------------------
  "text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output;
    if (process.env.GLUDD_TASK_TRACKING_ENFORCE === "0") return output;
    try {
      const root = getProjectRoot();
      const tasksPath = path.join(root, "TASKS.md");
      if (!fs.existsSync(tasksPath)) return output;

      const currentMtime = fs.statSync(tasksPath).mtimeMs;
      const state = readJsonFile<TaskTrackingState>(STATE_FILE, {
        pid: process.pid,
        last_tasks_md_mtime: 0,
        tasks_md_path: tasksPath,
        missed_update_count: 0,
      });
      state.tasks_md_path = tasksPath;

      if (state.last_tasks_md_mtime === 0) {
        state.last_tasks_md_mtime = currentMtime;
        writeJsonFile(STATE_FILE, state);
        return output;
      }

      if (currentMtime > state.last_tasks_md_mtime) {
        state.last_tasks_md_mtime = currentMtime;
        state.missed_update_count = 0;
        writeJsonFile(STATE_FILE, state);
        return output;
      }

      state.missed_update_count++;
      writeJsonFile(STATE_FILE, state);

      const text = typeof output === "string" ? output : "";
      if (state.missed_update_count >= 5) {
        return (
          text +
          "\n\n[TASK TRACKING: CRITICAL — TASKS.md is stale. " +
          String(state.missed_update_count) +
          " response cycles without TASKS.md update. Add unchecked entries " +
          "to TASKS.md for pending work.]"
        );
      }
      if (state.missed_update_count >= 3) {
        return (
          text +
          "\n\n[TASK TRACKING: WARNING — " +
          String(state.missed_update_count) +
          " response cycles without TASKS.md update. Review and update TASKS.md.]"
        );
      }
      if (state.missed_update_count === 1) {
        return (
          text +
          "\n\n[TASK TRACKING: NOTE — TASKS.md may need updating after this response.]"
        );
      }
    } catch {
      // fail-open
    }
    return output;
  },

  // ------------------------------------------------------------------
  // system.transform — task-tracking directive in system prompt.
  // ------------------------------------------------------------------
  "experimental.chat.system.transform": async (
    _input: unknown,
    output: unknown,
  ) => {
    if (isSubagent()) return output;
    if (typeof output === "string") {
      const directive = [
        "================ TASK TRACKING DIRECTIVE ================",
        "After every user prompt that requests new work:",
        "  1. Add a new entry to TASKS.md describing the request",
        "  2. After each subagent result is codified, tick the",
        "     corresponding checkbox with evidence",
        "  3. TASKS.md is the single source of truth",
        "This directive is ENFORCED by enforce-task-tracking.ts.",
        "========================================================",
      ].join("\n");
      return directive + "\n\n" + output;
    }
    return output;
  },
};

export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return;
      try {
        const impl = loadHotModule("task-tracking", defaultImpl);
        const fn = impl["tool.execute.before"];
        return fn ? await fn(input, output) : undefined;
      } catch {
        return { allow: true };
      }
    },
    "experimental.text.complete": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output;
      try {
        const impl = loadHotModule("task-tracking", defaultImpl);
        const fn = impl["text.complete"];
        return fn ? await fn(_input, output) : output;
      } catch {
        return output;
      }
    },
    "experimental.chat.system.transform": async (
      _input: unknown,
      output: unknown,
    ) => {
      if (isSubagent()) return output;
      try {
        const impl = loadHotModule("task-tracking", defaultImpl);
        const fn = impl["experimental.chat.system.transform"];
        return fn ? await fn(_input, output) : output;
      } catch {
        return output;
      }
    },
  };
}) satisfies Plugin;
