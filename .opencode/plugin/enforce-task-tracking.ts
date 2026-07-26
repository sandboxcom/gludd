import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive, getProjectRoot } from "../lib/shared.ts"

const ENABLED = (process.env.GLUDD_TASK_TRACKING_ENFORCE || "1") !== "0"
const TASK_TRACKING_FILE = "TASKS.md"

const WRITE_TOOLS = ["edit", "write"] as readonly string[]

function uncheckedCount(filePath: string): number {
  try {
    const content = fs.readFileSync(filePath, "utf8")
    const matches = content.match(/^[ \t]*[-*]\s*\[\s*\]/gm)
    return matches ? matches.length : 0
  } catch {
    return -1
  }
}

function isTaskFile(filePath: string, tasksPath: string): boolean {
  try {
    return path.resolve(filePath) === path.resolve(tasksPath)
  } catch {
    return false
  }
}

function isSourceOrTestOrDocFile(filePath: string): boolean {
  if (typeof filePath !== "string" || !filePath) return false
  const n = filePath.replace(/\\/g, "/")
  if (n.includes("tests/") || n.includes("TASKS.md")) return true
  return n.endsWith(".py") || n.endsWith(".ts") || n.endsWith(".md") || n.endsWith(".yml") || n.endsWith(".yaml") || n.endsWith(".json") || n.endsWith(".sh") || n.endsWith(".toml")
}

function isReadTool(tool: string): boolean {
  return tool === "read" || tool === "grep" || tool === "glob"
}

function extractPath(input: any): string {
  if (!input) return ""
  if (typeof input === "object") {
    return input.filePath || input.path || input.args?.filePath || input.args?.path || input.tool_input?.filePath || input.tool_input?.path || ""
  }
  return ""
}

function getNewContent(input: any): string {
  if (!input) return ""
  return input.content || input.text || input.args?.content || input.args?.text || ""
}

/** Return task IDs declared on checkbox lines in TASKS.md. */
export function declaredTaskIds(tasksContent: string): Set<string> {
  const ids = new Set<string>()
  const taskLine = /^[ \t]*[-*]\s*\[[ xX]\]\s+([^\s|]+)/gm
  for (const match of tasksContent.matchAll(taskLine)) {
    ids.add(match[1])
  }
  return ids
}

function extractTaskId(input: any): string {
  if (!input || typeof input !== "object") return ""
  return String(
    input.taskId || input.task_id ||
    input.args?.taskId || input.args?.task_id ||
    input.tool_input?.taskId || input.tool_input?.task_id ||
    input.metadata?.taskId || input.metadata?.task_id || ""
  ).trim()
}

/**
 * Require either an exact path registration or a declared task ID on writes.
 * Absolute paths are canonicalized relative to the project root so a caller
 * cannot bypass the check by changing path spelling.
 */
export function isRegisteredTaskPath(
  filePath: string,
  tasksContent: string,
  projectRoot: string,
  input?: any,
): boolean {
  if (typeof filePath !== "string" || !filePath.trim()) return false
  const resolvedRoot = path.resolve(projectRoot)
  const resolvedPath = path.resolve(resolvedRoot, filePath)
  const relativePath = path.relative(resolvedRoot, resolvedPath).replace(/\\/g, "/")
  if (!relativePath || relativePath.startsWith("../") || path.isAbsolute(relativePath)) return false
  if (tasksContent.includes(relativePath)) return true
  const taskId = extractTaskId(input)
  return Boolean(taskId && declaredTaskIds(tasksContent).has(taskId))
}

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, _output: any) => {
    if (isSubagent()) return
    reportAlive("enforce-task-tracking")
    if (!ENABLED) return

    try {
      const root = getProjectRoot()
      const tasksPath = path.join(root, TASK_TRACKING_FILE)
      if (!fs.existsSync(tasksPath)) return

      const tool = (input?.tool ?? "") as string
      const lt = tool.toLowerCase()

      if (isReadTool(lt)) return

      const filePath = extractPath(input)

      if (isTaskFile(filePath, tasksPath)) {
        const newContent = getNewContent(input)
        if (!newContent) return

        const originalContent = fs.readFileSync(tasksPath, "utf8")

        const origChecked = (originalContent.match(/^[ \t]*[-*]\s*\[x\]/gim) || []).length
        const newChecked = (newContent.match(/^[ \t]*[-*]\s*\[x\]/gim) || []).length

        if (origChecked > 0 && newChecked === 0) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "TASKS.md CORRUPTION DETECTED: this edit would remove ALL checked items.",
              "Original file had " + String(origChecked) + " checked items; proposed content has 0.",
              "Corruption protection engaged — edit denied.",
              "Set GLUDD_TASK_TRACKING_ENFORCE=0 to disable.",
            ].join("\n"),
          }
        }

        if (origChecked > 0 && newChecked < origChecked * 0.5) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "TASKS.md CORRUPTION DETECTED: this edit would remove >50% of checked items.",
              "Original: " + String(origChecked) + " checked, proposed: " + String(newChecked) + " checked.",
              "An edit that removes the majority of checked items is likely corruption.",
              "If this is intentional, set GLUDD_TASK_TRACKING_ENFORCE=0 and retry.",
            ].join("\n"),
          }
        }

        const newLen = newContent.length
        const origLen = originalContent.length
        if (newLen < origLen * 0.5) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "TASKS.md CORRUPTION DETECTED: this edit would remove >50% of the file.",
              "Original: " + String(origLen) + " bytes, proposed: " + String(newLen) + " bytes.",
              "An edit that removes the majority of the file body is likely corruption.",
              "If this is intentional, set GLUDD_TASK_TRACKING_ENFORCE=0 and retry.",
            ].join("\n"),
          }
        }

        const hasTaskPattern = /^[ \t]*[-*]\s*\[[ x]\]/im.test(newContent)
        if (!hasTaskPattern) {
          console.warn(
            "TASKS.md WARNING: edit does not contain `- [ ]` or `- [x]` pattern. " +
            "The file should follow the task-tracking format."
          )
        }

        return
      }

      const isWriteOp = WRITE_TOOLS.includes(lt)
      if (isWriteOp && !filePath) {
        return {
          permissionDecision: "deny" as const,
          message: [
            "WRITE TARGET PATH MISSING: task registration cannot be verified.",
            "Retry the write with a workspace-relative filePath and a registered task.",
            "Set GLUDD_TASK_TRACKING_ENFORCE=0 to disable.",
          ].join("\n"),
        }
      }

      if (isWriteOp && isSourceOrTestOrDocFile(filePath)) {
        const originalContent = fs.readFileSync(tasksPath, "utf8")
        if (!isRegisteredTaskPath(filePath, originalContent, root, input)) {
          return {
            permissionDecision: "deny" as const,
            message: [
              "TASK REGISTRATION REQUIRED: this path is not registered in TASKS.md.",
              "Add the workspace-relative path to an active task or provide its declared taskId, then retry.",
              "Set GLUDD_TASK_TRACKING_ENFORCE=0 to disable.",
            ].join("\n"),
          }
        }
      }

      const noUnchecked = uncheckedCount(tasksPath) === 0

      if (noUnchecked && isWriteOp && isSourceOrTestOrDocFile(filePath)) {
        return {
          permissionDecision: "deny" as const,
          message: [
            "NO TASK ENTRY: you must first add an unchecked task to TASKS.md before starting new work.",
            "Use Write to add a line like:",
            "  - [ ] NEW — <description> | priority: high | effort: S | status: pending",
            "Then retry this operation.",
            "Set GLUDD_TASK_TRACKING_ENFORCE=0 to disable.",
          ].join("\n"),
        }
      }
    } catch {
      return
    }
  },

  "text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    if (!ENABLED) return output

    try {
      const root = getProjectRoot()
      const tasksPath = path.join(root, TASK_TRACKING_FILE)
      if (!fs.existsSync(tasksPath)) return output

      const unchecked = uncheckedCount(tasksPath)
      if (unchecked <= 0) return output

      const text = typeof output === "string" ? output
        : (output as any)?.text ? String((output as any).text) : ""

      if (!text || text.trim().length === 0) return output

      const hasToolCall = /<(?:tool_call|invoke)/.test(text)
      if (hasToolCall) return output

      return {
        text: [
          "COMPLETE PENDING TASKS BEFORE TEXT RESPONSE.",
          "Unchecked: " + String(unchecked) + " items in TASKS.md.",
          "Your text-only response has been blanked.",
        ].join("\n"),
      }
    } catch {
      return output
    }
  },
}

export default (({ }) => {
  return {
    "tool.execute.before": async (input: any, _output: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("task-tracking", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, _output) : undefined
    },
    "experimental.text.complete": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output
      if (!ENABLED) return output
      return await _textComplete(_input, output)
    },
  }
}) satisfies Plugin

async function _textComplete(_input: unknown, output: unknown): Promise<unknown> {
  try {
    const root = getProjectRoot()
    const tasksPath = path.join(root, TASK_TRACKING_FILE)
    if (!fs.existsSync(tasksPath)) return output

    const unchecked = uncheckedCount(tasksPath)
    if (unchecked <= 0) return output

    const text = typeof output === "string" ? output
      : (output as any)?.text ? String((output as any).text) : ""

    if (!text || text.trim().length === 0) return output

    const hasToolCall = /<(?:tool_call|invoke)/.test(text)
    if (hasToolCall) return output

    return {
      text: [
        "COMPLETE PENDING TASKS BEFORE TEXT RESPONSE.",
        "Unchecked: " + String(unchecked) + " items in TASKS.md.",
        "Your text-only response has been blanked.",
      ].join("\n"),
    }
  } catch {
    return output
  }
}
