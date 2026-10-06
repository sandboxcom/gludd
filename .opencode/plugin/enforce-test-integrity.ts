// Fail-open. Subagent guard. Hot-reload capable.
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import {
  isSubagent,
  reportAlive,
  getProjectRoot,
} from "../lib/shared.ts"
const CI_WORKFLOW_FILE = ".github/workflows/build.yml"
const PYPROJECT_FILE = "pyproject.toml"
const TEST_DISABLE_PATTERNS: readonly RegExp[] = Object.freeze([
  /@pytest\.mark\.skip\b(?!\s*\([^)]*reason\s*=)/,
  /@pytest\.mark\.xfail\b(?!\s*\([^)]*strict\s*=\s*True)/,
  /pytest\.skip\s*\(/,
  /continue-on-error\s*:\s*true/,
  /fail_under\s*=\s*\d+/,
  /--fail-under\s+\d+/,
]) as readonly RegExp[]
const ALLOWLIST_PATHS = Object.freeze([
  "tests/unit/test_behavioral_specs.py",
  "tests/unit/test_type_safety_guardrails.py",
])
function isTestFile(p: string): boolean {
  return p.includes("/tests/") && p.endsWith(".py")
}
function isCiWorkflow(p: string): boolean {
  return p.endsWith(CI_WORKFLOW_FILE) || p.endsWith("build.yml")
}
function isPyproject(p: string): boolean {
  return p.endsWith(PYPROJECT_FILE) || p.endsWith("pyproject.toml")
}
function isAllowlisted(p: string): boolean {
  return ALLOWLIST_PATHS.some(a => p.endsWith(a))
}
function wouldAddDisablePattern(
  _oldContent: string,
  newContent: string,
  filePath: string,
): { detected: boolean; pattern: string } {
  if (isAllowlisted(filePath)) return { detected: false, pattern: "" }
  if (isTestFile(filePath)) {
    for (const re of TEST_DISABLE_PATTERNS) {
      if (re.test(newContent)) {
        return { detected: true, pattern: re.source }
      }
    }
  }
  if (isCiWorkflow(filePath)) {
    if (/continue-on-error\s*:\s*true/.test(newContent)) {
      return { detected: true, pattern: "continue-on-error: true" }
    }
  }
  if (isPyproject(filePath)) {
    const oldMatch = _oldContent.match(/fail_under\s*=\s*(\d+)/)
    const newMatch = newContent.match(/fail_under\s*=\s*(\d+)/)
    if (oldMatch && newMatch) {
      const oldVal = parseInt(oldMatch[1], 10)
      const newVal = parseInt(newMatch[1], 10)
      if (newVal < oldVal) {
        return { detected: true, pattern: `fail_under lowered ${oldVal}→${newVal}` }
      }
    }
  }
  return { detected: false, pattern: "" }
}
const DENY_MESSAGE_PREFIX =
  "TEST INTEGRITY: test-disabling pattern detected. Fix the issue, do not disable the test. "
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, _output) => {
    if (isSubagent()) return
    reportAlive("enforce-test-integrity")
    try {
      if (process.env.GLUDD_TEST_INTEGRITY_ENFORCE === "0") return
      const tool = input.tool ?? ""
      if (tool !== "edit" && tool !== "write") return
      let filePath = ""
      let content = ""
      if (tool === "edit") {
        filePath = input.args?.filePath ?? ""
        content = input.args?.newString ?? ""
      }
      if (tool === "write") {
        filePath = input.args?.filePath ?? ""
        content = input.args?.content ?? ""
      }
      if (!filePath || !content) return
      const relevant = isTestFile(filePath) || isCiWorkflow(filePath) || isPyproject(filePath)
      if (!relevant) return
      const { detected, pattern } = wouldAddDisablePattern("", content, filePath)
      if (detected) {
        return {
          permissionDecision: "deny" as const,
          message: DENY_MESSAGE_PREFIX +
            `Pattern: ${pattern}. File: ${filePath}. ` +
            `Set GLUDD_TEST_INTEGRITY_ENFORCE=0 to disable this guard.`,
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
      const impl = loadHotModule("test-integrity", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
  }
}) satisfies Plugin
