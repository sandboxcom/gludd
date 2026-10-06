// Depth is the scoped exception to generic subagent isolation: delegated
// contexts must still be prevented from dispatching beyond the configured
// recursion boundary. Non-dispatch tools remain unaffected at every depth.
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.
import type { Plugin } from "@opencode-ai/plugin"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import { reportAlive, isDisengaged } from "../lib/shared.ts"
const ENFORCE = process.env.GLUDD_DEPTH_ENFORCE !== "0"
const MAX_DEPTH = parseInt(process.env.GLUDD_MAX_DEPTH || "4", 10)
function currentDepth(): number {
  const depth = parseInt(process.env.OPENCODE_DEPTH || "0", 10)
  return isNaN(depth) || depth < 0 ? 0 : depth
}
function isDispatchTool(tool: string): boolean {
  const lt = tool.toLowerCase()
  return lt === "task" || lt === "agent" || lt === "workflow"
}
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: { tool?: string }) => {
    reportAlive("enforce-depth")
    try {
      if (!ENFORCE) return
      const tool = (input?.tool ?? "") as string
      if (isDisengaged()) return
      if (!isDispatchTool(tool)) return
      const depth = currentDepth()
      console.warn(`[enforce-depth] depth=${depth} max=${MAX_DEPTH} tool=${tool}`)
      if (depth >= MAX_DEPTH) {
        return {
          permissionDecision: "deny" as const,
          message: [
            `MAX DEPTH EXCEEDED: depth=${depth}, limit=${MAX_DEPTH}.`,
            "AGENTS.md: Subagent delegation depth MUST NOT exceed 4 levels.",
            "A depth-4 subagent CANNOT dispatch further. Complete assigned work directly.",
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
      const impl = loadHotModule("depth", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
    },
  }
}) satisfies Plugin
