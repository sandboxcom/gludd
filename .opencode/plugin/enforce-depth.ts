/**
 * enforce-depth.ts — MECHANICALLY ENFORCES the subagent depth limit.
 *
 * AGENTS.md CRITICAL: Subagent Depth Policy:
 *   Depth 0 = main agent, depth 1 = first subagent, depth 2 = second,
 *   depth 3 = third. A depth-3 subagent CANNOT dispatch further.
 *
 * Depth is tracked via OPENCODE_DEPTH env var (default 0).
 * The orchestrator increments OPENCODE_DEPTH when dispatching.
 *
 * FAIL-OPEN: any error → allow. Set GLUDD_DEPTH_ENFORCE=0 to disable.
 * MAX_DEPTH: env-overridable via GLUDD_MAX_DEPTH (default 3).
 *
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.
 */
import type { Plugin } from "@opencode-ai/plugin"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive, isDisengaged } from "../lib/shared.ts"

const ENFORCE = process.env.GLUDD_DEPTH_ENFORCE !== "0"
export const MAX_DEPTH = parseInt(process.env.GLUDD_MAX_DEPTH || "3", 10)

function currentDepth(): number {
  return parseInt(process.env.OPENCODE_DEPTH || "0", 10)
}

function isDispatchTool(tool: string): boolean {
  const lt = tool.toLowerCase()
  return lt === "task" || lt === "agent" || lt === "workflow"
}

export const defaultImpl: HotModule = {
  "tool.execute.before": async (input: { tool?: string }) => {
    if (isSubagent()) return
    reportAlive("enforce-depth")

    try {
      if (!ENFORCE) return
      const tool = (input?.tool ?? "") as string

      if (isDisengaged()) return
      if (!isDispatchTool(tool)) return

      const depth = currentDepth()
      if (depth >= MAX_DEPTH) {
        return {
          permissionDecision: "deny" as const,
          message: [
            `MAX DEPTH EXCEEDED: depth=${depth}, limit=${MAX_DEPTH}.`,
            "AGENTS.md: Subagent delegation depth MUST NOT exceed 3 levels.",
            "A depth-3 subagent CANNOT dispatch further. Complete assigned work directly.",
            "Set GLUDD_DEPTH_ENFORCE=0 to disable.",
          ].join("\n"),
        }
      }
    } catch {
      return
    }
  },
}

export default (({ }) => {
  return {
    "tool.execute.before": async (input: { tool?: string }) => {
      if (isSubagent()) return
      const impl = loadHotModule("depth", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
    },
  }
}) satisfies Plugin
