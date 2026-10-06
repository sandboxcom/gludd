// Fail-open: any throw/exception → allow (never wedge the editor).
// HOT-RELOAD: proxy pattern from hot_reload.ts. Run `make hot-reload-plugins`
import type { Plugin } from "@opencode-ai/plugin"
import type { HotModule } from "../lib/hot_reload.ts"
import { loadHotModule } from "../lib/hot_reload.ts"
import { isSubagent, reportAlive } from "../lib/shared.ts"
import { shouldBlock, shouldBlockCoverageClaim } from "../lib/plugin_test_exports.ts"

const BLOCK_MESSAGE = [
  "BLOCKED: response contains done-claims without verification evidence.",
  "Run make git-status, make git-log, make ci-verdict-safe, or make test-iso",
  "and paste the output before claiming work is done.",
  "See AGENTS.md 'Evidence-Based Response Policy' and 'Done Claims Require Observable Verification Evidence'.",
].join("\n")

const COVERAGE_BLOCK = [
  "BLOCKED: response claims completion (final/complete/done) about e2e/coverage/test",
  "with coverage below the 85% target. Claims of completion at insufficient",
  "coverage are premature — fix the coverage gap before declaring done.",
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
      if (msg && shouldBlock(msg)) {
        throw Object.assign(new Error(BLOCK_MESSAGE), { permissionDecision: "deny" })
      }
    } catch (e) {
      if (e instanceof Error && (e as Error & { permissionDecision?: string }).permissionDecision === "deny") throw e
    }
  },

  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    try {
      if (isSubagent()) return output
      if (process.env.GLUDD_VERIFIED_CLAIMS_ENFORCE === "0") return output
      const out = output as { text?: string }
      const text = out?.text ?? ""
      if (!text) return output
      if (shouldBlockCoverageClaim(text)) {
        return { ...(output as Record<string, unknown>), text: COVERAGE_BLOCK + "\n\n" + text }
      }
      return output
    } catch {
      return output
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
    "experimental.text.complete": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output
      const impl = loadHotModule("verified-claims", defaultImpl)
      const fn = impl["text.complete"] || impl["experimental.text.complete"]
      return fn ? await fn(_input, output) : output
    },
  }
}) satisfies Plugin
