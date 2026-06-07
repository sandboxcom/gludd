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

const SHELL_META_CHARS = /[|;&(){}$`\\!]/

function formatBashBlockedMessage(attemptedCommand: string, reason?: string): string {
  return [
    BASH_POLICY_HEADER,
    `Attempted command: ${attemptedCommand}`,
    reason ? `\nReason: ${reason}` : "",
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
  "FORBIDDEN STOP PATTERNS (these are policy violations):",
  "  - Listing remaining tasks and asking 'Want me to proceed?'",
  "  - Listing remaining tasks and asking 'What priority order?'",
  "  - Listing remaining tasks and asking 'Should I continue?'",
  "  - Listing audit findings/gaps and asking 'Want me to start building?'",
  "  - Listing audit findings/gaps and asking 'Want me to fix this?'",
  "  - Saying 'X is done. Next steps are A, B, C.' and then stopping",
  "  - Answering a status question and then stopping instead of resuming",
  "  - Presenting a plan or analysis and waiting for approval",
  "  - Saying 'Here is what needs to be done' and NOT doing it",
  "  - Asking ANY question that is really 'should I do my job?' in disguise",
  "",
  "THE ONLY VALID RESPONSE TO IDENTIFYING WORK IS TO DO IT.",
  "Never ask. Never wait. Just do the work.",
  "",
  "If there are pending tasks in your todo list, you MUST continue",
  "working on them. The ONLY valid reasons to stop are:",
  "  1. ALL tasks are completed (all items marked 'completed' or 'cancelled')",
  "  2. You are genuinely blocked (missing credentials, broken environment)",
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

const BASH_METACHAR_POLICY = [
  "",
  "## CRITICAL: Bash Metacharacter Policy",
  "",
  "Shell metacharacters are FORBIDDEN in bash commands. This includes:",
  "  | (pipe)   ; (semicolon)   && (and)   || (or)",
  "  () (subshell)   $ (variable)   ` (backtick)   ! (history)",
  "  {} (brace expansion)   \\ (escape)",
  "",
  "These allow chaining commands, piping output, running subcommands,",
  "and other side effects that bypass the make-only policy.",
  "",
  "If you need to combine operations, create a Makefile target that",
  "does the combination. Make targets ARE allowed to use these",
  "characters internally because they execute in a controlled context.",
  "",
  "VIOLATIONS (all blocked):",
  "  make test-unit 2>&1 | tail -20     # pipe forbidden",
  "  cd /foo && make test                # cd and && forbidden",
  "  make test; make lint                # ; forbidden",
  "  $(cat file)                         # $() forbidden",
  "  make test || true                   # || forbidden",
  "",
  "CORRECT (all allowed):",
  "  make test-unit                      # plain make",
  "  make test-unit TESTFILE=path        # make with variable",
  "  make lint                           # plain make",
  "",
].join("\n")

const STOP_SIGNAL_WORDS = [
  "want me to proceed",
  "want me to continue",
  "want me to start",
  "want me to fix",
  "should i continue",
  "should i proceed",
  "should i start",
  "should i fix",
  "shall i continue",
  "shall i proceed",
  "shall i finish",
  "shall i do",
  "next steps are",
  "remaining tasks",
  "here are the remaining",
  "what priority",
  "here is what needs to be done",
  "i'll stop here",
  "let me know if you'd like",
  "let me know if you want",
  "i can continue",
  "i could continue",
  "would you like me to",
  "ready to proceed",
  "here's my plan",
  "here is my plan",
  "all done",
  "work is complete",
  "sprint is complete",
  "everything is done",
  "all objectives delivered",
  "all objectives complete",
  "all work done",
  "now everything is truly complete",
  "this is truly done",
  "all green",
  "ready for review",
  "waiting for your",
  "fixed:",
  "now continuing",
  "continuing with",
  "to summarize",
  "in summary",
  "recap:",
  "the answer is",
  "committed .",
  "tests pass",
  "test-and-commit",
  "passed, 0 failed",
  "0 failures",
  "coverage:",
  "mypy: 0 errors",
  "lint: 0 errors",
  "here's what was",
  "here is what was",
  "what did we do",
  "summary of changes",
  "changes made",
  "completed work",
  "finished work",
  "done with",
  "wrapped up",
  "that completes",
  "this completes",
  "wrapping up",
  "finishing up",
]

const RESUME_COMMAND = [
  "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
  "⛔ STOP-PATTERN DETECTED — AUTO-INJECTED OVERRIDE",
  "",
  "You just generated a response that appears to be a completion",
  "report, status summary, or permission-asking pattern. This is",
  "FORBIDDEN when there may be pending work.",
  "",
  "YOUR NEXT ACTION MUST be a tool call — not text.",
  "Do not explain yourself. Do not apologize. Do not ask questions.",
  "Call your tools and CONTINUE WORKING immediately.",
  "",
  "The ONLY exception: ALL todo items are marked 'completed'.",
  "If any todo is 'in_progress' or 'pending', you MUST keep working.",
  "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
].join("\n")

function detectStopPattern(responseText: string): boolean {
  const lower = responseText.toLowerCase()
  if (STOP_SIGNAL_WORDS.some(pattern => lower.includes(pattern))) {
    return true
  }
  const lines = lower.split("\n")
  let commitHashCount = 0
  let passedCount = false
  let summaryTable = false
  let lastLineIsSummary = false
  let testResultLine = false
  let coverageLine = false
  let bulletListCount = 0
  for (const line of lines) {
    const trimmed = line.trim()
    if (/^\[master [a-f0-9]{7}\]/.test(trimmed)) commitHashCount++
    if (/\d+ passed/.test(trimmed) && /0 failed/.test(trimmed)) passedCount = true
    if (/^\d+ passed/.test(trimmed)) testResultLine = true
    if (/^\d+\.\d+%/.test(trimmed) || /coverage.*\d+%/.test(trimmed)) coverageLine = true
    if (trimmed.includes("|") && trimmed.includes("---")) summaryTable = true
    if (/^[-*]\s/.test(trimmed)) bulletListCount++
  }
  if (lines.length > 0) {
    const last = lines[lines.length - 1].trim().toLowerCase()
    if (last === "done." || last === "done!" || last === "complete." || last === "all green." || last === "ready." || last === "committed." || last === "finished.") {
      lastLineIsSummary = true
    }
  }
  if (commitHashCount >= 1 && passedCount) return true
  if (passedCount && lastLineIsSummary) return true
  if (testResultLine && coverageLine) return true
  if (commitHashCount >= 1 && summaryTable) return true
  if (testResultLine && bulletListCount >= 3) return true
  if (coverageLine && lastLineIsSummary) return true
  return false
}

export default (async ({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool === "bash") {
        const command = output?.args?.command ?? ""
        const trimmed = typeof command === "string" ? command.trim() : ""

        if (!trimmed.startsWith("make ") && trimmed !== "make") {
          throw new Error(formatBashBlockedMessage(trimmed, "Command does not start with 'make'"))
        }

        if (SHELL_META_CHARS.test(trimmed)) {
          const matched = trimmed.match(SHELL_META_CHARS)
          throw new Error(
            formatBashBlockedMessage(
              trimmed,
              `Shell metacharacter(s) forbidden: ${matched?.join(", ")}. ` +
              `Pipes (|), chaining (&&, ||, ;), subshells ($(), ()), backticks (\`), ` +
              `variable expansion ($), and brace expansion ({}) are not allowed. ` +
              `Create a Makefile target instead.`
            )
          )
        }

        // Preflight gate: warn before test-and-commit
        const isCommitTarget = /\bmake\s+test-and-commit\b/.test(trimmed)
        if (isCommitTarget) {
          const PREFLIGHT_GATE = [
            "⛔ PREFLIGHT GATE — make preflight runs first inside test-and-commit",
            "",
            "If preflight fails (including completion_audit with gaps),",
            "the commit is BLOCKED. All 9 checks must pass.",
            "Fix all gaps before attempting commit.",
            "",
          ].join("\n")
          console.warn(PREFLIGHT_GATE)
        }

        const afterMake = trimmed.slice(5).trim()
        const words = afterMake.split(/\s+/)
        const targetName = words[0] || ""
        const restArgs = words.slice(1).join(" ")

        // only scan arguments (not target name) for forbidden patterns
        // target names like git-status, git-diff, etc. are valid Makefile targets
        const toScan = restArgs

        const invalidPatterns = [
          /\b2>&1\b/,
          /\b>\s/,
          /\b<\s/,
          /\brg\b/,
          /\btail\b/,
          /\bhead\b/,
          /\bgrep\b/,
          /\bcat\b/,
          /\bfind\b/,
          /\bls\b/,
          /\bcd\b/,
          /\bpython\b/,
          /\bpython3\b/,
          /\buv\b/,
          /\bpip\b/,
          /\bgit\b/,
          /\brm\b/,
          /\bcp\b/,
          /\bmv\b/,
          /\bwhich\b/,
          /\bcommand\b/,
          /\bexport\b/,
          /\bsource\b/,
        ]
        for (const pattern of invalidPatterns) {
          if (pattern.test(toScan)) {
            throw new Error(
              formatBashBlockedMessage(
                trimmed,
                `Forbidden command/shell builtin detected: ${pattern.source}. ` +
                `Only 'make <target> VAR=val' is allowed. ` +
                `Create a Makefile target for this operation.`
              )
            )
          }
        }
      }

      if (input.tool === "edit") {
        const filePath: string = output?.args?.filePath ?? ""
        const isTest = filePath.includes("/tests/") || filePath.includes("\\tests\\")
        const isProduction = filePath.includes("/src/") || filePath.includes("\\src\\")
        if (isProduction && !isTest) {
          throw new Error([
            "TDD VIOLATION: You are editing production code without writing a test first.",
            "",
            "Before editing " + filePath + ", you MUST:",
            "  1. Write a failing test that defines the expected behavior.",
            "  2. Run the test to confirm it fails (proving the behavior doesn't exist).",
            "  3. Only then edit the production code to make the test pass.",
            "",
            "This is NOT advisory. This is a hard block. You will not be allowed",
            "to edit production code until you have written and run a test first.",
            "",
            "To proceed:",
            "  1. Cancel this edit (do not retry).",
            "  2. Write a test file under tests/ that covers the behavior.",
            "  3. Run the test to confirm it FAILS (red).",
            "  4. Then come back and edit the production code.",
            "",
            "Skipping TDD is a policy violation. See AGENTS.md.",
          ].join("\n"))
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
      const preResponseAudit = [
        "⛔ PRE-RESPONSE AUDIT — VIOLATIONS ARE BLOCKED",
        "",
        "1. Check todowrite. Items pending/in_progress? → TOOL CALL REQUIRED.",
        "2. About to type a summary/table/status? STOP. Make a tool call.",
        "3. User says 'fix bug X first' → fix it FIRST, not later.",
        "4. Text-only response with pending work = AUTO-DETECTED AND BLOCKED.",
        "",
        "PAST VIOLATIONS (do not repeat):",
        "- Reported 'X passed, Y failed — committed' instead of continuing",
        "- Sent completion table when work remained",
        "- Said 'This is truly done' before all tests written",
        "- Said 'Ready for review' with unchecked sprint boxes",
        "- Said 'Done.' as final word with pending todos",
        "",
        "CORRECT: Brief answer (1-2 lines) → tool call immediately.",
        "",
      ].join("\n");

      const instructionFollowing = [
        "⛔ INSTRUCTION FOLLOWING: When user says 'fix bug X first' or 'do Y",  
        "   before continuing', you MUST do that specific thing before any",
        "   other work. Do not continue with your own plan. Do not do both",
        "   simultaneously. Fix the requested thing FIRST, then continue.",
        "",
      ].join("\n");

      const completionRules = [
        "You MUST complete ALL work requested before stopping. Specifically:",
        "",
        "1. If given a sprint, objective list, or multi-step task, you MUST",
        "   work through every step until all are complete or genuinely blocked.",
        "2. Do NOT stop early to report progress, ask permission, or wait for",
        "   feedback unless tests are failing and you cannot fix them.",
        "3. Do NOT treat tooling/infrastructure setup as the deliverable.",
        "4. After each objective is complete, immediately start the next one.",
        "5. Only stop when ALL objectives are complete or you hit a hard blocker.",
        "",
        "FORBIDDEN PATTERNS:",
        '  "Want me to proceed/continue/start?"',
        '  "Should I continue?" or "Shall I finish?"',
        '  "X done. Next steps are..." (then stopping)',
        '  Status tables/summaries followed by no tool calls',
        '  "All done" / "Everything is complete" / "Ready for review"',
        '  Answering status questions and then stopping',
        "",
        "THE ONLY VALID RESPONSE TO IDENTIFYING WORK IS TO DO IT.",
        "Never ask. Never wait. Just do the work.",
        "",
      ].join("\n")
      const selfDirected = [
        "If the user wants you to stop if they want you to stop.",
        "",
        "CORRECT BEHAVIOR: When asked for status, answer briefly, then",
        "immediately continue working on the next pending task. Do not ask",
        "for permission. Do not wait for acknowledgment. Just keep going.",
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

      const preflightCommitPrompt = [
        "",
        "## CRITICAL: Preflight Quality Gate — Pre-Commit Verification",
        "",
        "Before committing (make test-and-commit), the preflight gate runs:",
        "  1. Coverage >85% line rate",
        "  2. Lint zero errors",
        "  3. Mypy zero errors",
        "  4. All prompt templates exist",
        "  5. All playbooks exist",
        "  6. Molecule scenarios present",
        "  7. FileStore readable",
        "  8. Sprint checkboxes all checked [x]",
        "",
        "If ANY check fails, the commit is BLOCKED. You MUST fix the failing",
        "checks before commit will succeed.",
        "",
        "BEFORE calling a task 'complete':",
        "  1. Define explicit acceptance criteria for the task.",
        "  2. Run make preflight to verify all quality gates.",
        "  3. Run verify_task_completion(criteria, evidence) to confirm.",
        "  4. Only mark todo 'completed' if verify_task_completion returns",
        "     complete=true with confidence > 0.8.",
        "",
        "This is a HARD gate. Do not mark tasks complete without verification.",
        "",
      ].join("\n")

      const noManualDefault = [
        "",
        "## CRITICAL: No-Manual-Default Policy",
        "",
        "Every process you build MUST be fully automated by default.",
        "No manual steps allowed. Specifically:",
        "",
        "  1. If you create a downloader, the make target MUST download.",
        "     Not check — DO the download.",
        "  2. If you create a bootstrapper, it MUST run at init time.",
        "  3. If you create a healthcheck, it MUST remedy if possible.",
        "  4. Every config value must have a working zero-config default.",
        "  5. Every binary/resource needed must be prefetched in make dist.",
        "  6. Every class in src/ must be importable from daemon startup.",
        "",
        "Leaving a step as 'run X manually' is a BUG. Fix it immediately.",
        "Never say 'download with' or 'run manually' — make it automatic.",
        "",
      ].join("\n")

      if (typeof output === "string") {
        const stopAuditOverride = [
          "⛔⛔⛔ PRE-RESPONSE STOP AUDIT — HIGHEST PRIORITY ⛔⛔⛔",
          "",
          "BEFORE generating ANY text response, you MUST check:",
          "  1. Are there items in todowrite with status 'pending' or 'in_progress'?",
          "  2. If YES → your response MUST contain at least one tool call.",
          "     Text-only responses are FORBIDDEN when work remains.",
          "  3. The ONLY time to send text-only: ALL items are 'completed' or 'cancelled'.",
          "",
          "THIS IS NOT ADVISORY. THIS IS A HARD RULE.",
          "Every past session failure was caused by violating this rule.",
          "BUGS.md documents 6+ incidents of premature stops.",
          "",
          "STOP = tool call required. PERIOD.",
          "",
        ].join("\n")

        output = stopAuditOverride + "\n\n" + preResponseAudit + "\n\n" + instructionFollowing + "\n\n" + output
        output += "\n\n" + completionRules
        output += "\n\n" + selfDirected
        output += evidencePrompt
        output += preflightCommitPrompt
        output += noManualDefault
        output += BASH_METACHAR_POLICY
      }
    },

    "experimental.chat.response.transform": async (_input, output) => {
      if (typeof output !== "string") return
      if (detectStopPattern(output)) {
        output = [
          "⛔ STOP-PATTERN DETECTED — RESPONSE REPLACED ⛔",
          "",
          "Your previous message was a completion report. It has been",
          "COMPLETELY REPLACED. You will NOT see your original text.",
          "",
          "You MUST immediately make a tool call to continue working.",
          "Do NOT explain. Do NOT apologize. Call your tools NOW.",
          "",
          "Check todowrite — any pending or in_progress items?",
          "→ Work on them NOW. Do NOT send another text message.",
        ].join("\n")
      }

      // Preflight: detect task-completion claims and inject verification demand
      const lower = output.toLowerCase()
      const completionClaims = [
        "task is complete",
        "all tasks complete",
        "tasks are complete",
        "mark done",
        "mark complete",
        "now done",
        "now complete",
        "all done",
        "all complete",
        "completed successfully",
        "sprint complete",
        "objectives delivered",
        "all objectives",
        "task completed",
        "tasks completed",
      ]
      if (completionClaims.some(c => lower.includes(c))) {
        const verification = [
          "",
          "⛔ PREFLIGHT: TASK COMPLETION VERIFICATION REQUIRED",
          "",
          "You claimed a task is complete. BEFORE marking it complete:",
          "  1. Run `make preflight` — all 8 checks must PASS.",
          "  2. Use verify_task_completion(criteria, evidence) from",
          "     general_ludd.quality.preflight.",
          "  3. Only mark complete if confidence > 0.8.",
          "  4. Evidence required: coverage%, lint errors, mypy errors,",
          "     test pass/fail counts.",
          "",
          "Without this verification, do NOT call the task complete.",
          "",
        ].join("\n")
        output = output + verification
      }
    },
  }
}) satisfies Plugin
