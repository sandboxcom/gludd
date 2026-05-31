import type { Plugin } from "@opencode-ai/plugin"

const BASH_POLICY_HEADER = "BLOCKED: Direct bash commands are not allowed in this project.\n"
const BASH_POLICY_RULE = "Rule: You MUST only run `make <target>` commands.\n"
const BASH_POLICY_FIX = [
  "What to do instead:",
  "  1. Open the Makefile.",
  "  2. Add or update a target that wraps the command you need.",
  "  3. Run `make <targetname>`.",
  "",
  "Example Makefile target:",
  "  my-check:",
  "  \t@uv run ruff check src tests",
  "Then run: make my-check",
  "",
].join("\n")
const BASH_POLICY_REF = "See AGENTS.md for existing make targets and the full policy.\n"

function formatBashBlockedMessage(attemptedCommand: string): string {
  return [
    BASH_POLICY_HEADER,
    `Attempted command: ${attemptedCommand}`,
    "",
    BASH_POLICY_RULE,
    "",
    BASH_POLICY_FIX,
    BASH_POLICY_REF,
  ].join("\n")
}

const TDD_REMINDER = [
  "TDD REMINDER: You are about to edit production code.",
  "",
  "Before writing implementation, you MUST:",
  "  1. Write a failing test that defines the expected behavior.",
  "  2. Run `make test-unit` to confirm the test fails.",
  "  3. Only then write the minimal implementation to make it pass.",
  "  4. Run `make test-unit` again to confirm it passes.",
  "",
  "This is enforced by AGENTS.md and the guardrail-pattern skill.",
  "Skipping TDD is a policy violation.",
].join("\n")

const COMMIT_REMINDER = [
  "COMMIT REMINDER: Tests are passing.",
  "",
  "When tests pass and work is complete on a change, you MUST commit.",
  "Run `make test-and-commit` to verify tests still pass and commit the result.",
  "",
  "Do not leave passing work uncommitted.",
  "See AGENTS.md for the commit policy.",
].join("\n")

const TASK_COMPLETION_WARNING = [
  "⚠️ TASK COMPLETION CHECK: You are sending a message to the user.",
  "",
  "Before responding, ask yourself:",
  "  1. Was I given a multi-step task (sprint, objective list, etc.)?",
  "  2. Have I completed ALL steps, or did I stop partway through?",
  "  3. Am I responding because I finished, or because I got distracted",
  "     by infrastructure, tooling, or a side concern?",
  "",
  "RULES:",
  "  - Do NOT stop work to report status unless tests are failing and",
  "    you are genuinely blocked.",
  "  - Do NOT pause to ask if the user wants you to continue when",
  "    the instructions were explicit about completing all work.",
  "  - Do NOT treat infrastructure setup (guardrails, tooling) as",
  "    the deliverable. They are means to an end.",
  "  - Keep working through ALL objectives until done or blocked by",
  "    something you cannot fix (missing credentials, etc.).",
  "",
  "If you stopped early, RESUME WORK NOW. Do not explain why you",
  "stopped. Just keep going.",
].join("\n")

const SELF_DIRECTED_WORK_WARNING = [
  "⚠️ SELF-DIRECTED WORK CHECK: You identified a gap and are about to",
  "ask the user for permission instead of fixing it.",
  "",
  "RULE: When you find a gap, bug, or missing integration, you MUST",
  "fix it immediately. Do NOT say 'this is a gap worth fixing' and",
  "then stop. Do NOT ask 'want me to...' or 'should I...'.",
  "",
  "If you found it, you own it. Fix it now.",
].join("\n")

export default (async ({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool === "bash") {
        const command = output?.args?.command ?? ""
        const trimmed = typeof command === "string" ? command.trim() : ""
        if (!trimmed.startsWith("make ") && trimmed !== "make") {
          throw new Error(formatBashBlockedMessage(trimmed))
        }
      }

      if (input.tool === "edit") {
        const filePath: string = output?.args?.filePath ?? ""
        const isTest = filePath.includes("/tests/") || filePath.includes("\\tests\\")
        const isProduction = filePath.includes("/src/") || filePath.includes("\\src\\")
        if (isProduction && !isTest) {
          console.warn(TDD_REMINDER)
        }
      }
    },

    "tool.execute.after": async (input, output) => {
      if (input.tool === "bash") {
        const command: string = output?.args?.command ?? ""
        if (
          typeof command === "string" &&
          (command.includes("make test") || command.includes("make qa") || command.includes("make validate"))
        ) {
          const stdout: string = output?.result?.stdout ?? output?.result ?? ""
          if (
            typeof stdout === "string" &&
            stdout.includes("passed") &&
            !stdout.includes("failed")
          ) {
            console.warn(COMMIT_REMINDER)
          }
        }
      }
    },

    "experimental.chat.system.transform": async (_input, output) => {
      const completionPrompt = [
        "",
        "## CRITICAL: Task Completion Policy",
        "",
        "You MUST complete ALL work requested before stopping. Specifically:",
        "",
        "1. If given a sprint, objective list, or multi-step task, you MUST",
        "   work through every step until all are complete or genuinely blocked.",
        "2. Do NOT stop early to report progress, ask permission, or wait for",
        "   feedback unless tests are failing and you cannot fix them.",
        "3. Do NOT treat tooling/infrastructure setup as the deliverable.",
        "   Guardrails, hooks, and make targets are means to an end.",
        "4. Do NOT get sidetracked. If you notice yourself spending time on",
        "   something that is not the requested work, stop and refocus.",
        "5. After each objective is complete, immediately start the next one.",
        "6. Only stop when ALL objectives are complete or you hit a hard blocker",
        "   (missing credentials, environment issues you cannot fix).",
        "",
        "## CRITICAL: Self-Directed Work Rule",
        "",
        "When you identify a gap, bug, or missing integration while working,",
        "you MUST fix it immediately. Do NOT:",
        "  - Stop to ask the user whether to proceed",
        "  - List the gap and wait for approval",
        "  - Say 'this is a gap worth fixing' and then stop",
        "  - Ask 'want me to...' or 'should I...'",
        "",
        "If you found it, you own it. Fix it, test it, commit it, then continue",
        "with the original task. The ONLY exception is if fixing it would require",
        "credentials, payment, or environment changes you cannot make.",
        "",
      ].join("\n")
      const evidencePrompt = [
        "",
        "## CRITICAL: Evidence-Based Response Policy",
        "",
        "Every factual claim in your responses MUST cite its source. Acceptable sources:",
        "- File paths with line numbers (e.g., src/foo.py:42)",
        "- Tool output (e.g., test results, grep results)",
        "- URLs with retrieved content",
        "- Direct quotes from documentation",
        "- Test pass/fail counts from make output",
        "",
        "If you cannot cite a source, you MUST NOT make the claim. Instead say",
        '"I don\'t have evidence for this" or "Based on [source], ..."',
        "",
      ].join("\n")
      if (typeof output === "string") {
        output += completionPrompt
        output += evidencePrompt
      }
    },
  }
}) satisfies Plugin
