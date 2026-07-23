// Companion exports for enforce-depth.ts.
// opencode's getLegacyPlugins() rejects non-function exports, so named
// constants/functions live here for tests to import.
import { type HotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive, isDisengaged } from "../lib/shared.ts"

export const MAX_DEPTH = parseInt(process.env.GLUDD_MAX_DEPTH || "3", 10)
export const defaultImpl: HotModule = {
  "tool.execute.before": async (input: { tool?: string }) => {
    if (isSubagent()) return
    reportAlive("enforce-depth")
    try {
      const ENFORCE = process.env.GLUDD_DEPTH_ENFORCE !== "0"
      if (!ENFORCE) return
      const tool = (input?.tool ?? "") as string
      if (isDisengaged()) return
      const lt = tool.toLowerCase()
      const isDispatch = lt === "task" || lt === "agent" || lt === "workflow"
      if (!isDispatch) return
      const depth = parseInt(process.env.OPENCODE_DEPTH || "0", 10)
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
