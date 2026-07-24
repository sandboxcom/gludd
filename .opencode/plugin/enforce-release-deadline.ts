// enforce-release-deadline.ts — escalating enforcement during release tasks (RP.19).
//
// PROBLEM: a release task marked in_progress in TASKS.md ran for 4+ hours
// without producing artifacts. No deadline tracking existed, so the agent
// drifted into unrelated work while the release stalled.
//
// WHAT IT DOES:
//   * tool.execute.before — detects a release task in_progress in TASKS.md,
//     records the start time in STATE_FILE. After BLOCK_MS, denies the
//     non-release bash targets listed in BLOCKED_TARGETS.
//   * experimental.text.complete — after WARN_MS, injects a warning
//     directing the agent back to release-critical work.
//
// THRESHOLDS (configurable via env):
//   WARN_MS  (default 7200000  = 2h) — inject warning via text.complete
//   BLOCK_MS (default 10800000 = 3h) — block non-release bash commands
//
// ALLOWED during block: release-critical operations — editing
// .github/workflows/build.yml, pushing, tagging, verify-release-completeness.
// These are never blocked; the block only applies to the non-release targets
// listed in BLOCKED_TARGETS.
//
// FAIL-OPEN: every path is wrapped so an internal error never wedges the
// session. Worst case = no deadline enforcement, never a blocked tool call.
//
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts. Hook functions
// check /tmp/gludd-hot-release-deadline.js on every invocation. Run
// `make hot-reload-plugins` after editing this file to generate the hot module.
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import {
  isSubagent,
  reportAlive,
  readJsonFile,
  writeJsonFile,
  getProjectRoot,
} from "../lib/shared.ts"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"

// ============================================================================
// CONFIG
// ============================================================================
const WARN_MS = parseInt(
  process.env.GLUDD_RELEASE_DEADLINE_WARN_MS || "7200000",
  10,
)
const BLOCK_MS = parseInt(
  process.env.GLUDD_RELEASE_DEADLINE_BLOCK_MS || "10800000",
  10,
)
const STATE_FILE =
  process.env.GLUDD_RELEASE_DEADLINE_STATE || "/tmp/gludd-release-deadline.json"
const ENFORCE = process.env.GLUDD_RELEASE_DEADLINE_ENFORCE !== "0"

// Non-release bash targets blocked after BLOCK_MS. These are work categories
// that should not run once a release has stalled past 3h. Release-critical
// targets (release-cut, git-push, git-tag-push, verify-release-completeness)
// are deliberately NOT in this list so they remain available.
const BLOCKED_TARGETS = ["test-unit", "lint", "typecheck", "ci-status", "ci-view"]

// ============================================================================
// STATE FILE
// ============================================================================
interface ReleaseDeadlineState {
  release_task?: string | null
  start_ms?: number | null
  last_check_ms?: number | null
  warned?: boolean | null
}

function loadState(): ReleaseDeadlineState {
  return readJsonFile<ReleaseDeadlineState>(STATE_FILE, {})
}

function saveState(s: ReleaseDeadlineState): void {
  writeJsonFile(STATE_FILE, s)
}

function elapsedMs(state: ReleaseDeadlineState): number {
  if (typeof state.start_ms !== "number") return 0
  return Date.now() - state.start_ms
}

// ============================================================================
// RELEASE TASK DETECTION
// ============================================================================
// Scans TASKS.md for a release task marked in_progress. Returns the task
// identifier (first token, e.g. "RP.19") or null when no release task is
// active. Fails open (returns null) on any read/parse error.
function detectReleaseTaskInProgress(): string | null {
  const root = getProjectRoot()
  try {
    const tasksPath = path.join(root, "TASKS.md")
    if (!fs.existsSync(tasksPath)) return null
    const content = fs.readFileSync(tasksPath, "utf8")
    for (const line of content.split("\n")) {
      if (!/status:\s*in_progress/i.test(line)) continue
      if (!/release/i.test(line)) continue
      const m = line.match(/^\s*[-*]\s+\[\s*x?\s*\]\s+(\S+)/)
      if (m && m[1]) return m[1]
      return "release"
    }
    return null
  } catch {
    return null
  }
}

// ============================================================================
// WARNING TEXT
// ============================================================================
const WARN_TEXT =
  "\n\u25888\u25888\u25888  RELEASE DEADLINE WARNING  \u25888\u25888\u25888\n" +
  "A release task has been in_progress for over 2 hours. The release has " +
  "stalled \u2014 focus on release-critical work: fix CI, edit build.yml, push, " +
  "tag, run verify-release-completeness. Do NOT drift into unrelated work.\n"

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return
    reportAlive("enforce-release-deadline")
    if (!ENFORCE) return
    try {
      const tool = input?.tool
      const taskId = detectReleaseTaskInProgress()
      const s = loadState()
      const now = Date.now()
      if (taskId) {
        // Record or refresh the start time for the active release task.
        if (!s.release_task || s.release_task !== taskId) {
          s.release_task = taskId
          s.start_ms = now
          s.warned = false
        }
        s.last_check_ms = now
        saveState(s)
      } else {
        // No release task in progress — clear any stale state.
        if (s.release_task) {
          s.release_task = null
          s.start_ms = null
          s.warned = null
          saveState(s)
        }
        return
      }
      const elapsed = elapsedMs(s)
      if (elapsed <= BLOCK_MS) return
      // Past 3h: block non-release bash targets.
      if (tool !== "bash") return
      const args = input?.args ?? {}
      const cmd = String(args?.command ?? input?.command ?? "")
      if (!cmd.startsWith("make ")) return
      const target = cmd.slice(5).trim().split(/\s+/)[0]
      if (BLOCKED_TARGETS.includes(target)) {
        const hrs = (elapsed / 3600000).toFixed(1)
        return {
          permissionDecision: "deny",
          message: (
            `RELEASE DEADLINE BLOCK: release task ${taskId} has been ` +
            `in_progress for ${hrs}h (limit 3h). Non-release work ` +
            `(\`make ${target}\`) is blocked until the release completes. ` +
            `Focus on release-critical ops: edit build.yml, push, tag, ` +
            `verify-release-completeness. Complete the release or mark it ` +
            `cancelled in TASKS.md to resume normal work.`
          ),
        }
      }
    } catch {
      // fail-open
    }
  },
  "experimental.text.complete": async (output: any) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return output
    reportAlive("enforce-release-deadline")
    if (!ENFORCE) return output
    try {
      const s = loadState()
      if (!s.release_task) return output
      const elapsed = elapsedMs(s)
      if (elapsed <= WARN_MS) return output
      // Past 2h: inject the warning into the text response.
      s.warned = true
      s.last_check_ms = Date.now()
      saveState(s)
      const text = typeof output === "object" && output !== null && "text" in output
        ? String((output as Record<string, unknown>).text)
        : typeof output === "string" ? output : ""
      return {
        ...(output as Record<string, unknown>),
        text: WARN_TEXT + (text || ""),
      }
    } catch {
      // fail-open
      return output
    }
  },
}

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (() => {
  return {
    "tool.execute.before": async (input: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("release-deadline", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
    },
    "experimental.text.complete": async (_input: any, output: any) => {
      if (isSubagent()) return output
      const impl = loadHotModule("release-deadline", defaultImpl)
      const fn = impl["text.complete"] || impl["experimental.text.complete"]
      return fn ? await fn(output) : output
    },
  }
}) satisfies Plugin
