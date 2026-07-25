import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive, getProjectRoot } from "../lib/shared.ts"
// enforce-release-deadline.ts — release-task elapsed-time enforcement (RP.19/BP.4).
//
// PROBLEM: release work (cut a version, verify artifacts, promote master) had no
// time-box. A release task sat in_progress for hours with no surfaced heartbeat,
// blocking every other lane of work. AGENTS.md "Release Pipeline Must Be CI-Green"
// codified the PROCEDURE but not the DEADLINE — this plugin makes the deadline
// observable and mechanically enforced.
//
// WHAT IT DOES:
//   * tool.execute.before (ANY tool)   -> scan TASKS.md for a release task marked
//                                          status: in_progress; if found, record
//                                          start timestamp (once) into STATE_FILE.
//                                          If elapsed > WARN  -> inject directive.
//                                          If elapsed > BLOCK -> deny non-release
//                                          bash targets (test/lint/typecheck/
//                                          ci-status/ci-view), ALLOWING only
//                                          release-critical ops (release-cut,
//                                          verify-release-completeness, pushes,
//                                          tags, edits).
//   * experimental.text.complete       -> inject the warning directive into the
//                                          outgoing text so the orchestrator sees
//                                          it without reading a state file.
//
// ALLOWED (never blocked) past the hard deadline:
//   release-cut, verify-release-completeness, verify-release-artifact,
//   git-push-*, git-tag-push, verify-remote, release-view, release-create,
//   release-branch-new, release-promote, release-recut, ci-verdict*,
//   require-ci-green, edits/writes (the agent must be free to complete the
//   release), task/agent/workflow dispatch (keep the pipeline primed).
//
// FAIL-OPEN: every code path is wrapped so an internal error NEVER wedges the
// session. Worst case = no deadline enforcement (back to old behavior), never
// a blocked tool call.
//
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
// check /tmp/gludd-hot-release-deadline.js on every invocation.  If present and
// newer than cached, the hot module's hook overrides the compiled-in default.
// Run `make hot-reload-plugins` after editing this file to generate the hot
// module.
// ============================================================================
// CONFIG
// ============================================================================
const RELEASE_DEADLINE_WARN_MS = parseInt(
  process.env.GLUDD_RELEASE_DEADLINE_WARN_MS || "7200000", 10
)
const RELEASE_DEADLINE_BLOCK_MS = parseInt(
  process.env.GLUDD_RELEASE_DEADLINE_BLOCK_MS || "10800000", 10
)
const STATE_FILE =
  process.env.GLUDD_RELEASE_DEADLINE_STATE || "/tmp/gludd-release-deadline.json"
const ENFORCE = process.env.GLUDD_RELEASE_DEADLINE_ENFORCE !== "0"
// Bash targets that become DENIED once the hard deadline elapses.
// Release-critical targets (release-cut, verify-release-*, pushes, tags) are
// intentionally absent so the agent can still complete the release.
const BLOCKED_TARGETS = Object.freeze([
  "test-unit",
  "lint",
  "typecheck",
  "ci-status",
  "ci-view",
  "test",
  "test-integration",
  "test-e2e",
  "qa",
  "gate",
  "gate-lite",
  "preflight",
  "validate",
  "ansible-syntax",
  "molecule-test",
  "security",
  "sast",
  "sbom",
  "pip-audit",
  "collect-check",
  "healthcheck",
])
// ============================================================================
// STATE FILE
// ============================================================================
interface ReleaseDeadlineState {
  release_task: string | null
  start_ms: number | null
  warned: boolean | null
}
function loadState(): ReleaseDeadlineState {
  try {
    if (!fs.existsSync(STATE_FILE)) return newFreshState()
    const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
    if (!raw || typeof raw !== "object") return newFreshState()
    return {
      release_task: typeof raw.release_task === "string" ? raw.release_task : null,
      start_ms: typeof raw.start_ms === "number" ? raw.start_ms : null,
      warned: typeof raw.warned === "boolean" ? raw.warned : null,
    }
  } catch {
    return newFreshState()
  }
}
function newFreshState(): ReleaseDeadlineState {
  return { release_task: null, start_ms: null, warned: null }
}
function saveState(s: ReleaseDeadlineState): void {
  try {
    const tmp = `${STATE_FILE}.tmp.${process.pid}`
    fs.writeFileSync(tmp, JSON.stringify(s), "utf8")
    fs.renameSync(tmp, STATE_FILE)
  } catch {
    // fail-open: permission / disk-full → silently skip
  }
}
function clearState(): void {
  try {
    if (fs.existsSync(STATE_FILE)) {
      const fresh = newFreshState()
      const tmp = `${STATE_FILE}.tmp.${process.pid}`
      fs.writeFileSync(tmp, JSON.stringify(fresh), "utf8")
      fs.renameSync(tmp, STATE_FILE)
    }
  } catch {
    // fail-open
  }
}
// ============================================================================
// TASKS.md release-task detection
// ============================================================================
// Scan TASKS.md for a task line that:
//   1. matches /status:\s*in_progress/i
//   2. contains "release" (case-insensitive)
// Returns the first word of the task id (e.g. "RP.19") or null.
// Returns null if TASKS.md is missing or unreadable (fail-open).
function detectReleaseTaskInProgress(): string | null {
  try {
    const root = getProjectRoot()
    const tasksPath = path.join(root, "TASKS.md")
    if (!fs.existsSync(tasksPath)) return null
    const text = fs.readFileSync(tasksPath, "utf8")
    for (const line of text.split("\n")) {
      if (!/status:\s*in_progress/i.test(line)) continue
      if (!/release/i.test(line)) continue
      // task id = first token after the checkbox marker
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
// Bash target classification
// ============================================================================
// Extract the make target from a bash command string.  Returns "" if not a
// `make <target>` invocation.
function extractMakeTarget(cmd: unknown): string {
  try {
    if (typeof cmd !== "string") return ""
    const trimmed = cmd.trim()
    if (!trimmed.startsWith("make ")) return ""
    const rest = trimmed.slice(5).trim()
    // stop at the first flag (`MSG=`, `FILES=`, etc.)
    const m = rest.match(/^([A-Za-z0-9_.-]+)/)
    return m ? m[1] : ""
  } catch {
    return ""
  }
}
function isBlockedTarget(target: string): boolean {
  return BLOCKED_TARGETS.includes(target)
}
// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, _output: any) => {
    if (isSubagent()) return
    reportAlive("enforce-release-deadline")
    if (!ENFORCE) return
    try {
      const task = detectReleaseTaskInProgress()
      const state = loadState()
      // If no release task is in_progress, clear any stale tracking state.
      if (!task) {
        if (state.release_task !== null) clearState()
        return
      }
      // Release task IS in_progress — record start (once) and compute elapsed.
      if (state.release_task !== task || state.start_ms === null) {
        state.release_task = task
        state.start_ms = Date.now()
        state.warned = false
        saveState(state)
      }
      const elapsed = Date.now() - (state.start_ms as number)
      const elapsedMin = Math.round(elapsed / 60000)
      // HARD BLOCK at 3h: deny non-release bash targets.
      if (elapsed > RELEASE_DEADLINE_BLOCK_MS) {
        const tool = typeof input?.tool === "string" ? input.tool : ""
        if (tool === "bash") {
          const cmd =
            typeof input?.args?.command === "string" ? input.args.command :
            typeof input?.command === "string" ? input.command : ""
          const target = extractMakeTarget(cmd)
          if (target && isBlockedTarget(target)) {
            return {
              permissionDecision: "deny",
              message:
                `RELEASE DEADLINE BLOCK: release task ${state.release_task} has ` +
                `been in_progress for ${elapsedMin}min (hard limit ` +
                `${Math.round(RELEASE_DEADLINE_BLOCK_MS / 60000)}min). ` +
                `"${target}" is not release-critical. Allowed: release-cut, ` +
                `verify-release-completeness, git-push-*, git-tag-push, edits. ` +
                `Complete or cancel the release task to resume normal work.`,
            }
          }
        }
        return
      }
      // WARNING at 2h: inject directive, persist warned flag.
      if (elapsed > RELEASE_DEADLINE_WARN_MS && !state.warned) {
        state.warned = true
        saveState(state)
        const line =
          `RELEASE DEADLINE WARNING: release task ${state.release_task} has ` +
          `been in_progress for ${elapsedMin}min (warn threshold ` +
          `${Math.round(RELEASE_DEADLINE_WARN_MS / 60000)}min). Hard block on ` +
          `non-release bash commands in ` +
          `${Math.round((RELEASE_DEADLINE_BLOCK_MS - elapsed) / 60000)}min.`
        console.warn(line)
      }
    } catch {
      // fail-open: never block on an internal error
    }
  },
  "experimental.text.complete": async (_output: any) => {
    if (isSubagent()) return
    reportAlive("enforce-release-deadline")
    if (!ENFORCE) return
    try {
      const task = detectReleaseTaskInProgress()
      if (!task) return
      const state = loadState()
      if (state.release_task !== task || state.start_ms === null) return
      const elapsed = Date.now() - (state.start_ms as number)
      if (elapsed > RELEASE_DEADLINE_WARN_MS) {
        const elapsedMin = Math.round(elapsed / 60000)
        const remainMin = Math.max(
          0, Math.round((RELEASE_DEADLINE_BLOCK_MS - elapsed) / 60000)
        )
        const verb = elapsed > RELEASE_DEADLINE_BLOCK_MS
          ? "PAST HARD BLOCK"
          : "WARNING"
        const text =
          typeof _output?.text === "string" ? _output.text : ""
        if (text.includes("RELEASE DEADLINE")) return
        const directive =
          `\n\n⚠ RELEASE DEADLINE ${verb}: release task ${task} has been ` +
          `in_progress for ${elapsedMin}min. ${remainMin > 0
            ? `Hard block on non-release bash in ${remainMin}min. `
            : `Non-release bash commands are BLOCKED. `
          }` +
          `Complete the release (release-cut + verify-release-completeness) or ` +
          `cancel the task to resume normal work.`
        if (_output && typeof _output === "object") {
          (_output as Record<string, unknown>).text = text + directive
        }
      }
    } catch {
      // fail-open
    }
  },
}
// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  return {
    "tool.execute.before": async (input: any, output: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("release-deadline", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
    "experimental.text.complete": async (output: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("release-deadline", defaultImpl)
      const fn = impl["text.complete"] || impl["experimental.text.complete"]
      return fn ? await fn(output) : undefined
    },
  }
}) satisfies Plugin
