/**
 * enforce-branch-discipline.ts — verifies the agent is on the correct branch
 * before performing mutating operations (commit, push, merge).
 *
 * Codified 2026-07-19 per BEHAVIORAL_SPECS.md Group B (B01-B25).
 *
 * Rules:
 *   - Reads SESSION.md for intended branch (from PRIMARY OBJECTIVE or branch
 *     context).
 *   - Checks actual git branch before mutating operations.
 *   - If on wrong branch: DENY the tool call with guidance.
 *   - Special: denies push to master when on a worktree or when the objective
 *     says to work on development.
 *
 * This is a BLOCKING plugin. Env: GLUDD_BRANCH_DISCIPLINE_ENFORCE=0 to disable.
 * FORCE=1 bypasses the branch check (hotfix only).
 *
 * Fail-open. Subagent guard. Hot-reload capable.
 */
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { createRequire } from "node:module"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import {
  isSubagent,
  reportAlive,
  getProjectRoot,
} from "../lib/shared.ts"

const nodeRequire = typeof require === "function" ? require : createRequire(import.meta.url)

function execSync(...args: any[]): Buffer {
  return nodeRequire("node:child_" + "process").execSync(...args)
}

function getCurrentBranch(root: string): string {
  try {
    const result = execSync("git rev-parse --abbrev-ref HEAD", {
      cwd: root,
      timeout: 5000,
      stdio: ["pipe", "pipe", "pipe"],
    })
    return result.toString().trim()
  } catch {
    return ""
  }
}

function isWorktree(root: string): boolean {
  try {
    const gitDir = path.join(root, ".git")
    if (!fs.existsSync(gitDir)) return false
    const stat = fs.statSync(gitDir)
    return stat.isFile()
  } catch {
    return false
  }
}

function getIntendedBranch(): string | null {
  try {
    const root = getProjectRoot()
    const sessionPath = path.join(root, "SESSION.md")
    if (!fs.existsSync(sessionPath)) return null
    const content = fs.readFileSync(sessionPath, "utf8")
    const m = content.match(/^## PRIMARY OBJECTIVE:.*$/m)
    if (!m) return null
    const obj = m[0]
    if (/DEVELOPMENT/i.test(obj) && !/MASTER/i.test(obj)) return "development"
    if (/MASTER/i.test(obj) && !/DEVELOPMENT/i.test(obj)) return "master"
    return null
  } catch {
    return null
  }
}

function isMutatingCommand(cmd: string): boolean {
  return /\bmake\s+(git-commit|ship-commit|git-push|batch-push|development-push|development-merge-to-master|git-merge|release-cut|release-promote|feature-done|agent-merge)\b/.test(cmd)
}

const DENY_MESSAGE =
  "BRANCH DISCIPLINE: you are on the wrong branch for this operation. " +
  "Check your primary objective in SESSION.md and switch to the correct branch " +
  "before committing/pushing/merging. Use FORCE=1 to bypass (hotfix only). " +
  "Set GLUDD_BRANCH_DISCIPLINE_ENFORCE=0 to disable."

const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    if (isSubagent()) return
    reportAlive("enforce-branch-discipline")
    try {
      if (process.env.GLUDD_BRANCH_DISCIPLINE_ENFORCE === "0") return
      if (process.env.FORCE === "1") return

      const tool = input.tool ?? ""
      if (tool !== "bash") return

      const cmd: string = input.args?.command ?? ""
      if (!cmd) return

      if (!isMutatingCommand(cmd)) return

      const root = getProjectRoot()
      const currentBranch = getCurrentBranch(root)
      if (!currentBranch) return

      const intendedBranch = getIntendedBranch()
      const worktree = isWorktree(root)

      // Rule: Never push master directly from a worktree
      if (worktree && /\b(batch-push|git-push|development-push)\b/.test(cmd)) {
        return {
          permissionDecision: "deny" as const,
          message: "Pushing from a worktree is forbidden. Shared-branch mutations " +
            "must happen on the main checkout. See AGENTS.md Branch Discipline rule #2.",
        }
      }

      // Rule: Never merge to master from a worktree
      if (worktree && /\b(agent-merge|development-merge-to-master|release-promote)\b/.test(cmd)) {
        return {
          permissionDecision: "deny" as const,
          message: "Merging from a worktree is forbidden. Merges to master must happen " +
            "on the main checkout. See AGENTS.md Branch Discipline rule #5.",
        }
      }

      // Rule: If objective says DEVELOPMENT but we're on master trying to push
      if (intendedBranch === "development" && currentBranch === "master") {
        const isPush = /\b(git-push|batch-push)\b/.test(cmd)
        if (isPush) {
          return {
            permissionDecision: "deny" as const,
            message: DENY_MESSAGE + ` Current: ${currentBranch}, intended: ${intendedBranch}.`,
          }
        }
      }

      // Rule: If objective says MASTER but we're on development trying to do release-cut
      if (intendedBranch === "master" && currentBranch === "development") {
        if (/\brelease-cut\b/.test(cmd)) {
          return {
            permissionDecision: "deny" as const,
            message: "release-cut must run from master. Current: development. " +
              "Merge development→master first (when CI green).",
          }
        }
      }
    } catch {
      // fail-open
    }
  },
}

export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return
      const impl = loadHotModule("branch-discipline", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
  }
}) satisfies Plugin
