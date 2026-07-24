// enforce-no-ci-poll.ts — limits consecutive CI polling calls.
//
// After MAX_CONSECUTIVE_POLLS (default 3) consecutive CI status checks
// (ci-status, ci-verdict, ci-view, ci-await) without an intervening
// productive mutation (edit, write, git-commit, git-push), the plugin
// DENIES further CI polls with a directive to do productive work.
//
// The counter resets on any productive operation.
//
// This prevents the Session 52 anti-pattern where the agent polled
// ci-status 40+ times in a row, consuming tokens and producing zero
// forward progress.
import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent, reportAlive, readJsonFile, writeJsonFile } from "../lib/shared.ts"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"

const CI_POLL_RE = /^(ci-status|ci-verdict|ci-view|ci-await|ci-verdict-safe|gate-status-check|verify-release-completeness|release-view)\b/
const PRODUCTIVE_RE = /^(git-commit|git-push|git-tag-push|ship-commit|release-cut|batch-push|git-add)\b/
const POLL_STATE_FILE = process.env.GLUDD_CI_POLL_STATE || "/tmp/gludd-ci-poll-streak.json"
const MAX_CONSECUTIVE_POLLS = parseInt(process.env.GLUDD_CI_POLL_MAX || "3", 10)

function readPollStreak(): number {
  const data = readJsonFile<{ count?: number }>(POLL_STATE_FILE, { count: 0 })
  return data.count || 0
}

function writePollStreak(count: number): void {
  writeJsonFile(POLL_STATE_FILE, { count })
}

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: unknown) => {
    if (isSubagent()) return
    reportAlive("enforce-no-ci-poll")
    try {
      const ctx = input as Record<string, unknown>
      const tool = ctx?.tool
      if (tool !== "bash") return
      const args = (ctx as any)?.args ?? {}
      const cmd = String(args?.command ?? (ctx as any)?.command ?? "")
      if (!cmd.startsWith("make ")) return

      if (CI_POLL_RE.test(cmd.slice(4))) {
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
      } else if (PRODUCTIVE_RE.test(cmd.slice(4))) {
        writePollStreak(0)
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
