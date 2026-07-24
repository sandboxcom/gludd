// Fail-open: any throw/exception → allow (never wedge the editor).
// HOT-RELOAD: proxy pattern from hot_reload.ts. Run `make hot-reload-plugins`
import type { Plugin } from "@opencode-ai/plugin"
import type { HotModule } from "../lib/hot_reload.ts"
import { loadHotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive } from "../lib/shared.ts"
import { shouldBlock } from "../lib/plugin_test_exports.ts"

const BLOCK_MESSAGE = [
  "BLOCKED: response contains done-claims without verification evidence.",
  "Run make git-status, make git-log, make ci-verdict-safe, or make test-iso",
  "and paste the output before claiming work is done.",
  "See AGENTS.md 'Evidence-Based Response Policy' and 'Done Claims Require Observable Verification Evidence'.",
].join("\n")

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: unknown) => {
    try {
      if (isSubagent()) return
      if (process.env.GLUDD_VERIFIED_CLAIMS_ENFORCE === "0") return
      const ctx = input as Record<string, unknown> | undefined
      const tool = ctx?.tool
      if (tool !== "bash" && tool !== "Bash") return
      const args = (ctx?.args ?? {}) as Record<string, unknown>
      const cmd = String(args?.command ?? "")
      if (!cmd.startsWith("make ") || !/\b(git-commit|commit-no-verify|repo-commit|ship-commit|test-and-commit)\b/.test(cmd)) return
      const msgMatch = cmd.match(/MSG=(?:"([^"]*)"|'([^']*)'|(\S+))/)
      const msg = msgMatch ? (msgMatch[1] ?? msgMatch[2] ?? msgMatch[3] ?? "") : ""
      if (msg && shouldBlock(msg) && !/[0-9a-f]+/.test(msg)) {
        throw Object.assign(new Error(BLOCK_MESSAGE), { permissionDecision: "deny" })
      }
    } catch (e) {
      if (e instanceof Error && (e as Error & { permissionDecision?: string }).permissionDecision === "deny") throw e
    }
  },
}

export default (() => {
  return {
    "tool.execute.before": async (input: unknown) => {
      if (isSubagent()) return
      reportAlive("enforce-verified-claims")
      const impl = loadHotModule("verified-claims", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
    },
  }
}) satisfies Plugin
