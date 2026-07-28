// enforce-no-ci-poll.ts — limits consecutive CI polling calls AND consecutive
// stagnant (read-only) operations.
//
// TWO detectors live here:
//
// 1. CI-POLL DETECTOR (original, Session 52 anti-pattern):
//    After MAX_CONSECUTIVE_POLLS (default 3) consecutive CI status checks
//    (ci-status, ci-verdict, ci-view, ci-await) without an intervening
//    productive mutation, DENIES further CI polls.
//
// 2. STAGNANT TOOL CALL DETECTOR (BP.3):
//    After MAX_STAGNANT_CALLS (default 5) consecutive read-only operations
//    (read, glob, grep tools; ci-status, ci-verdict, ci-view, ci-await,
//    gate-status-check, verify-state, git-status, git-log,
//    verify-release-completeness, release-view bash targets) without an
//    intervening productive mutation (edit, write, git-commit, git-push,
//    git-tag-push, ship-commit, release-cut, batch-push), DENIES with a
//    STOP-STAGNATION directive.
//
// Both counters reset on any productive operation.
import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent, reportAlive, readJsonFile, writeJsonFile } from "../lib/shared.ts"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"

const CI_POLL_RE = /^(ci-status|ci-verdict|ci-view|ci-await|ci-verdict-safe|gate-status-check|verify-release-completeness|release-view)\b/
const PRODUCTIVE_RE = /^(git-commit|git-push|git-tag-push|ship-commit|release-cut|batch-push|git-add)\b/
const POLL_STATE_FILE = process.env.GLUDD_CI_POLL_STATE || "/tmp/gludd-ci-poll-streak.json"
const MAX_CONSECUTIVE_POLLS = parseInt(process.env.GLUDD_CI_POLL_MAX || "3", 10)

// ── BP.3: Stagnant Tool Call Detector ─────────────────────────────────────
// Read-only bash targets that count toward stagnation. Superset of CI_POLL_RE
// (adds verify-state, git-status, git-log).
const STAGNANT_BASH_RE = /^(ci-status|ci-verdict|ci-view|ci-await|ci-verdict-safe|gate-status-check|verify-state|git-status|git-log|verify-release-completeness|release-view)\b/
// Direct read-only tool calls that count toward stagnation.
const STAGNANT_TOOLS = new Set(["read", "glob", "grep"])
// Direct mutation tool calls that reset the stagnant counter.
const PRODUCTIVE_TOOLS = new Set(["edit", "write"])
const STAGNANT_STATE_FILE =
  process.env.GLUDD_STAGNANT_STATE || "/tmp/gludd-stagnant-streak.json"
const MAX_STAGNANT_CALLS = parseInt(process.env.GLUDD_STAGNANT_MAX || "5", 10)
const ENFORCE =
  process.env.GLUDD_NO_CI_POLL_ENFORCE !== "0"
  && process.env.GLUDD_STAGNANT_ENFORCE !== "0"

function readPollStreak(): number {
  const data = readJsonFile<{ count?: number }>(POLL_STATE_FILE, { count: 0 })
  return data.count || 0
}

function writePollStreak(count: number): void {
  writeJsonFile(POLL_STATE_FILE, { count })
}

function readStagnantStreak(): number {
  const data = readJsonFile<{ count?: number }>(STAGNANT_STATE_FILE, { count: 0 })
  return data.count || 0
}

function writeStagnantStreak(count: number): void {
  writeJsonFile(STAGNANT_STATE_FILE, { count })
}

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: unknown) => {
    if (isSubagent()) return
    reportAlive("enforce-no-ci-poll")
    try {
      if (!ENFORCE) return
      const ctx = input as Record<string, unknown>
      const tool = ctx?.tool as string

      // ── BP.3: track direct read-only TOOL calls (read/glob/grep) ─────────
      // These increment the stagnant counter. edit/write reset it.
      if (typeof tool === "string" && STAGNANT_TOOLS.has(tool)) {
        const count = readStagnantStreak() + 1
        writeStagnantStreak(count)
        if (count > MAX_STAGNANT_CALLS) {
          return {
            permissionDecision: "deny",
            message: (
              `STAGNANT TOOL CALLS: ${count} consecutive read-only operations ` +
              `without productive work. STOP investigating and START producing. ` +
              `Make a mutation (edit, write, git-commit) or dispatch a subagent ` +
              `to do real work. Set GLUDD_STAGNANT_MAX to adjust the threshold ` +
              `(default 5) or GLUDD_STAGNANT_ENFORCE=0 to disable.`
            ),
          }
        }
        return
      }
      if (typeof tool === "string" && PRODUCTIVE_TOOLS.has(tool)) {
        writeStagnantStreak(0)
      }

      // ── Original CI-poll + stagnant bash tracking ───────────────────────
      if (tool !== "bash") return
      const args = (ctx as any)?.args ?? {}
      const cmd = String(args?.command ?? (ctx as any)?.command ?? "")
      if (!cmd.startsWith("make ")) return
      const target = cmd.slice(5)

      if (CI_POLL_RE.test(target)) {
        const count = readPollStreak() + 1
        writePollStreak(count)
        if (count > MAX_CONSECUTIVE_POLLS) {
          return {
            permissionDecision: "deny",
            message: (
              `CI POLLING IS NOT WORK. You have checked CI status ${count} ` +
              `times without making code changes. STOP POLLING. The CI run ` +
              `does not need you to watch it. Do something productive: fix a ` +
              `test, write a structural guard, update documentation. Check CI ` +
              `at the next natural break (15+ minutes).`
            ),
          }
        }
      }

      // BP.3: stagnant bash targets (includes verify-state, git-status, git-log
      // beyond the CI-poll set).
      if (STAGNANT_BASH_RE.test(target)) {
        const scount = readStagnantStreak() + 1
        writeStagnantStreak(scount)
        if (scount > MAX_STAGNANT_CALLS) {
          return {
            permissionDecision: "deny",
            message: (
              `STAGNANT TOOL CALLS: ${scount} consecutive read-only operations ` +
              `without productive work. STOP investigating and START producing. ` +
              `Make a mutation (edit, write, git-commit) or dispatch a subagent ` +
              `to do real work. Set GLUDD_STAGNANT_MAX to adjust the threshold ` +
              `(default 5) or GLUDD_STAGNANT_ENFORCE=0 to disable.`
            ),
          }
        }
      } else if (PRODUCTIVE_RE.test(target)) {
        // Reset both counters on any productive bash target.
        writePollStreak(0)
        writeStagnantStreak(0)
      }
    } catch {
      // Fail-open: never block on plugin error
    }
  },
}

export default (() => {
  return {
    "tool.execute.before": async (input: unknown) => {
      if (isSubagent()) return
      const impl = loadHotModule("no-ci-poll", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
    },
  }
}) satisfies Plugin
