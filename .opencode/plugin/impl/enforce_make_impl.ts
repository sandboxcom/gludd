import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { execSync } from "node:child_process"
import { isSubagent, reportAlive } from "../../lib/shared.ts"
import { loadHotModule, type HotModule } from "../../lib/hot_reload.ts"

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

const MAKE_ENFORCE = process.env.GLUDD_MAKE_ENFORCE !== "0"
const PROMPT_PRONE_EDIT_TOOLS = new Set(["apply_patch", "functions.apply_patch"])
const NESTED_PROMPT_PRONE_EDIT_CALL = /\btools(?:\.apply_patch|\[['"]apply_patch['"]\])\s*\(/

function invokesPromptProneNestedEdit(input: unknown): boolean {
  if (typeof input !== "object" || input === null) return false
  if ((input as { tool?: unknown }).tool !== "functions.exec") return false
  try {
    return NESTED_PROMPT_PRONE_EDIT_CALL.test(JSON.stringify(input))
  } catch {
    return false
  }
}

// Bare `(` and `)` removed 2026-07-18: they triggered false positives on
// legitimate commit messages (e.g. MSG="fix foo (see #123)"). The actual
// shell-injection vector is `$()` command substitution, which is still
// caught via the `$` char in this class. Backticks, `;`, `|`, `&&`, `||`,
// `{}`, `\`, `!` all remain blocked.
const SHELL_META_CHARS = /[|;&{}$`\\!]/

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

let _makeTurnState = { dispatchCount: 0, toolCallMade: false }
let _pendingCommitReminder = false
let _pendingPreflightGate = ""
// Set when a bash command is blocked for violating the make-only policy. The
// experimental.text.complete hook consumes (and clears) it to re-inject the
// bash-policy nudge into the assistant's context so the next turn corrects.
let _bashPolicyNudge = false

// Dispatch tools: "task" "agent" "workflow"
// --- Non-behavioral edit detection ------------------------------------------
// Returns true when an edit only touches comments (# ...) and/or docstring
// prose — i.e. no executable Python statement is added, removed, or changed.
// Such edits cannot alter runtime behaviour, so the TDD test-file requirement
// is skipped for them (narrowing the guardrail, not disabling it: real code
// edits still require a corresponding test file to exist).
//
// A line is "executable" if, after stripping leading whitespace, it matches a
// Python statement pattern (assignment, def/class/import/return/if/for/while/
// with/raise/yield, a bare call, or a decorator). Everything else — blank
// lines, `#` comments, and free-form prose inside docstrings — is treated as
// non-executable.
const EXECUTABLE_LINE_RE = new RegExp(
  "^(" +
    "def |class |import |from |return |if |elif |else:|for |while |with |" +
    "try:|except |finally:|raise |yield |global |nonlocal |assert |del |" +
    "pass |break |continue |async |await " +
    ")",
)
const ASSIGNMENT_RE = /^[A-Za-z_][A-Za-z0-9_.]*\s*(=|:|=|\+=|-=|\*=|\/=)/
const BARE_CALL_RE = /^[A-Za-z_][A-Za-z0-9_.]*\s*\(/
const DECORATOR_RE = /^@/

function isExecutablePythonLine(line: string): boolean {
  const trimmed = line.trimStart()
  if (trimmed === "") return false
  if (trimmed.startsWith("#")) return false
  if (EXECUTABLE_LINE_RE.test(trimmed)) return true
  if (ASSIGNMENT_RE.test(trimmed)) return true
  if (BARE_CALL_RE.test(trimmed)) return true
  if (DECORATOR_RE.test(trimmed)) return true
  return false
}

function isNonBehavioralEdit(oldContent: string, newContent: string): boolean {
  // Compare the set of executable lines; if they are unchanged the edit is
  // comment/docstring-only and cannot affect runtime behaviour.
  const oldExec = oldContent.split(/\r?\n/).filter(isExecutablePythonLine)
  const newExec = newContent.split(/\r?\n/).filter(isExecutablePythonLine)
  if (oldExec.length !== newExec.length) return false
  for (let i = 0; i < oldExec.length; i++) {
    if (oldExec[i] !== newExec[i]) return false
  }
  return true
}

// --- opencode.json schema allowlist -----------------------------------------
// Sourced from tests/unit/test_opencode_json_schema.py (ALLOWED_TOP_LEVEL_KEYS),
// which is in turn sourced from https://opencode.ai/config.json
// ($defs.Config.properties). The opencode Config type sets
// `additionalProperties: false`, so any top-level key NOT in this set is
// silently dropped — and any plugin relying on the dropped key is broken.
// Keep this set in sync with the Python test file.
const ALLOWED_TOP_LEVEL_KEYS: ReadonlySet<string> = new Set([
  "$schema",
  "shell",
  "logLevel",
  "server",
  "command",
  "skills",
  "references",
  "reference",
  "watcher",
  "snapshot",
  "plugin",
  "share",
  "autoshare",
  "autoupdate",
  "disabled_providers",
  "enabled_providers",
  "model",
  "small_model",
  "default_agent",
  "username",
  "mode",
  "agent",
  "provider",
  "mcp",
  "formatter",
  "lsp",
  "instructions",
  "layout",
  "permission",
  "tools",
  "attachment",
  "enterprise",
  "tool_output",
  "compaction",
  "experimental",
])

// --- Gate-concurrency probe (port of gate_concurrency_pretool.sh) -------------
// Two independent signals (either fires the block): fresh basetemp mtime, OR
// pgrep for a running pytest. FAIL-OPEN on any error (can't probe -> allow).
const BASETEMP = process.env.GLUDD_GATE_BASETEMP || "/tmp/gludd-gate-basetemp"
const STALE_SECS = parseInt(process.env.GLUDD_GATE_STALE_SECS || "600", 10)

function makeTargetExists(target: string): boolean {
  if (!target) return true
  try {
    const root = process.env.GLUDD_REPO_ROOT || process.cwd()
    const makefile = fs.readFileSync(path.join(root, "Makefile"), "utf8")
    const escaped = target.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    return new RegExp(`^${escaped}:`, "m").test(makefile)
  } catch {
    return false
  }
}

function basetempIsFresh(): boolean {
  try {
    const st = fs.statSync(BASETEMP)
    const ageSec = (Date.now() - st.mtimeMs) / 1000
    return ageSec < STALE_SECS
  } catch {
    return false
  }
}

function isGateAlreadyRunning(): boolean {
  try {
    if (process.env.GLUDD_GATE_PYTEST_RUNNING === "1") return true
    if (process.env.GLUDD_GATE_PYTEST_RUNNING === "0") return false
    // Signal A (definitive): pgrep -f pytest. Exit 0 = match found.
    try {
      const pids = execSync("pgrep -f pytest", { stdio: ["pipe", "pipe", "pipe"] }).toString().trim()
      if (!pids) return false
      // Check if the oldest pytest process is stale (>30 min). Stale processes
      // from prior sessions are not active gates and should not block tests.
      const oldestPid = pids.split("\n")[0]
      try {
        const etime = execSync(`ps -o etime= -p ${oldestPid}`, { stdio: ["pipe", "pipe", "pipe"] }).toString().trim()
        // etime format: "DD-HH:MM:SS" or "HH:MM:SS"
        const daysMatch = etime.match(/^(\d+)-/)
        const days = daysMatch ? parseInt(daysMatch[1], 10) : 0
        if (days >= 1) return false // Processes older than 1 day are definitely stale
        const timeMatch = etime.match(/(\d+):(\d+):(\d+)$/)
        if (timeMatch) {
          const hours = parseInt(timeMatch[1], 10) + (days * 24)
          if (hours >= 1) return false // Processes older than 1 hour are stale
        }
      } catch {
        // Can't check age — fall back to assuming active
      }
      return true
    } catch (e) {
      if (typeof e === "object" && e !== null && "code" in e && (e as { code?: unknown }).code === "ENOENT") {
        // pgrep unavailable — fall back to basetemp freshness heuristic
        if (basetempIsFresh()) return true
      }
      // Non-zero exit: pgrep found nothing — no pytest running
    }
    return false
  } catch {
    return false
  }
}


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
  "  $ (variable/command-substitution)   ` (backtick)   ! (history)",
  "  {} (brace expansion)   \\ (escape)",
  "  (Bare parens are NOT forbidden — see note below.)",
  "",
  "These allow chaining commands, piping output, running subcommands,",
  "and other side effects that bypass the make-only policy.",
  "",
  "NOTE: bare parens `(` `)` are NOT blocked (commit messages legitimately",
  "contain them, e.g. MSG=\"fix foo (see #123)\"). The shell-injection",
  "risk `$()` command substitution is still blocked via the `$` char.",
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

const BATCHING_POLICY = [
  "",
  "## CRITICAL: Batch-Everything Policy (HARD BLOCK on serial calls)",
  "",
  "EVERY response with tool calls MUST batch all independent operations",
  "into ONE message with multiple parallel tool invocations. Never serialize",
  "what can run concurrently.",
  "",
  "SERIAL CALLS ARE FORBIDDEN when:",
  "  - Reading multiple files → batch reads in ONE message",
  "  - Searching for multiple patterns → batch greps in ONE message",
  "  - Editing multiple independent files → batch edits in ONE message",
  "  - Dispatching subagents → batch 5+ tasks/agents in ONE message",
  "  - Any read/edit/grep/glob that doesn't depend on prior output",
  "",
  "The ONLY valid serial pattern is when tool call N's output is NEEDED",
  "to construct tool call N+1 (e.g., grep found a line number, then read",
  "that specific range). Everything else = batch it.",
  "",
  "VIOLATIONS (blocked):",
  "  read file A → wait → read file B          # batch the reads",
  "  grep pattern → wait → read result          # batch grep+read if independent",
  "  dispatch 1 task → wait → dispatch 1 task   # batch 5+ dispatches",
  "  edit file → wait → grep → wait → read      # batch independent ops",
  "",
  "CORRECT:",
  "  <tool>read A</tool><tool>read B</tool><tool>grep X</tool>  # all in ONE message",
  "  <tool>task desc=A</tool><tool>task desc=B</tool>... # 5+ dispatches at once",
  "",
].join("\n")

const STOP_PATTERN_BLOCK = [
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
].join("\n")

const COMPLETION_SOUNDING = [
  "✅",
  "all passed",
  "all complete",
  "all done",
  "all tasks complete",
  "phase complete",
  "everything is done",
  "everything complete",
  "what was implemented",
  "task |",
  "| what was",
  "key accomplishments",
  "remaining",
  "summary",
  "committed",
  "passed",
  "done",
  "ready for review",
  "completes the task",
  "objectives delivered",
  "all requested",
  "all objectives",
]

function detectStopPattern(text: string): boolean {
  const lower = text.toLowerCase()
  if (text.includes("✅")) return true
  if (COMPLETION_SOUNDING.some(p => lower.includes(p))) {
    // BUG #15 fix: removed <500 length gate. Only negation is question intents.
    if (lower.includes("want me to") || lower.includes("should i") || lower.includes("shall i")) return false
    return true
  }
  return false
}

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
    "tool.execute.before": async (input, output) => {
        reportAlive("enforce-make")

        // These edit surfaces can trigger an approval prompt before project
        // policy gets a chance to redirect the operation through an auditable
        // make target. Deny them at the earliest hook boundary.
        if (
          MAKE_ENFORCE
          && (
            PROMPT_PRONE_EDIT_TOOLS.has(input.tool)
            || invokesPromptProneNestedEdit(input)
          )
        ) {
          return {
            permissionDecision: "deny",
            message: "Prompt-prone edit tool blocked. Add or use a make target instead.",
          }
        }

        // --- BASH CHECK runs for ALL agents including subagents ---
        // AGENTS.md: "Bash = `make <target>` only. Subagents MUST know
        // that the bash tool can ONLY run `make <target>` commands."
        if (input.tool === "bash") {
          let command = ""
          const ic = (input as any)?.args?.command
          if (typeof ic === "string" && ic.trim()) command = ic.trim()
          if (!command) {
            const oc = (output as any)?.args?.command
            if (typeof oc === "string" && oc.trim()) command = oc.trim()
          }
          if (!command) {
            const dc = (input as any)?.command
            if (typeof dc === "string" && dc.trim()) command = dc.trim()
          }
          if (!command) return
          const trimmed = command.replace(/^\S*\$\s*/, "").trim()

          if (MAKE_ENFORCE) {
            // Strip single- and double-quoted content before checking for
            // shell metacharacters — a |, ;, or && inside MSG='...' or
            // VAR="..." is literal text in a commit message, not a shell
            // operator. The remaining (unquoted) portion is the real risk.
            const unquoted = trimmed.replace(/'[^']*'/g, "").replace(/"[^"]*"/g, "")
            if (SHELL_META_CHARS.test(unquoted)) {
              _bashPolicyNudge = true
              const matched = unquoted.match(SHELL_META_CHARS)
              throw new Error(
                formatBashBlockedMessage(
                  trimmed,
                  `Shell metacharacter(s) forbidden: ${matched?.join(", ")}. ` +
                  `Pipes (|), chaining (&&, ||, ;), command substitution ($()), backticks (\`), ` +
                  `variable expansion ($), and brace expansion ({}) are not allowed. ` +
                  `Bare parens () are permitted (commit messages may contain them). ` +
                  `Quoted content (MSG='...', VAR="...") is exempt — metacharacters inside ` +
                  `single/double quotes are literal, not shell operators. ` +
                  `Create a Makefile target instead.`
                )
              )
            }

            if (!trimmed.startsWith("make ") && trimmed !== "make") {
              _bashPolicyNudge = true
              throw new Error(formatBashBlockedMessage(trimmed, "Command does not start with 'make'"))
            }
          }

          // --- Gate concurrency guard -----------------------------------------
          // Port of .claude/hooks/gate_concurrency_pretool.sh.
          // Blocks launching a second pytest/gate while one is already running.
          // Root cause of the 2026-06-15 208-error incident: two concurrent gates
          // triggered pytest's keep-last-3 basetemp tmp-root rotation, deleting
          // the first gate's worker dirs mid-flight and producing hundreds of
          // spurious FileNotFoundError errors. FAIL-OPEN on any probe error.
          const GATE_TARGETS_RE = /^make\s+(gate|test|test-unit|test-e2e|test-count|test-and-commit|qa)(\s|$)/
          if (GATE_TARGETS_RE.test(trimmed)) {
            if (isGateAlreadyRunning()) {
              throw new Error([
                "GATE CONCURRENCY VIOLATION: a pytest / gate run appears to already",
                "be in progress (pgrep found a pytest process). Launching a second",
                "concurrent pytest",
                "triggers keep-last-3 basetemp rotation, which deletes the first",
                "gate's worker dirs mid-flight and produces hundreds of spurious",
                "FileNotFoundError errors (the 2026-06-15 208-error incident).",
                "Wait for the current gate to finish, then launch this one.",
                "This dispatch is BLOCKED.",
              ].join("\n"))
            }
          }

          /* trimmed.match(/^(make\s+\S+)/) */ const m = trimmed.match(/^make\s+(\S+)/)
          const lrTarget = m ? m[1] : ""

          // --- Long-running foreground command guard ----------------------------
          // Blocks `make gate` (~40 min), `make test-unit` (~27 min), bare
          // `make test`, `make qa`, `make test-e2e`, and `make validate` from
          // running in the foreground. While these run, the bash tool blocks
          // for the entire duration — NO subagents can be dispatched and NO
          // UI updates reach the user (the "multitasking bug"). Require
          // `make gate-background` or dispatch to a subagent.
          // NOT blocked: make lint, make typecheck, make test-count,
          // make collect-check, and targeted runs (TESTFILE= / NO_XDIST=1).
          {
            const isGate = lrTarget === "gate"
            const isTestUnit = lrTarget === "test-unit"
            const isBareTest =
              lrTarget === "test" &&
              !trimmed.includes("TESTFILE=") &&
              !trimmed.includes("NO_XDIST=1")
            const isQa = lrTarget === "qa"
            const isTestE2e = lrTarget === "test-e2e"
            const isValidate = lrTarget === "validate"
            const isAnsibleSyntax = lrTarget === "ansible-syntax"
            if (isGate || isTestUnit || isBareTest || isQa || isTestE2e || isValidate || isAnsibleSyntax) {
              throw new Error([
                "BLOCKED: Long-running foreground command. Use `make gate-background`",
                "(gate), `make test-bg` (tests), or dispatch to a subagent instead.",
                "Foreground blocking prevents subagent dispatch and UI updates.",
                "",
                "While this command runs, the bash tool blocks for the entire",
                "duration (make gate ~40 min, make test-unit ~27 min, make qa,",
                "make test-e2e, and make validate are equally long-running).",
                "During that time NO subagents can be dispatched and NO UI updates",
                "reach the user. Either:",
                "  1. Run `make gate-background` or `make test-bg` (background variants), or",
                "  2. Dispatch the gate/test to a subagent (preferred).",
                "",
                "Allowed in foreground:",
                "  make lint, make typecheck, make test-count, make collect-check,",
                "  make test TESTFILE=<path>, make test NO_XDIST=1",
                "",
                "Background alternatives for blocked tests:",
                "  make test-bg FILES='...' — run targeted tests in background",
                "  make test-bg TESTFILE=<path> — run test file in background",
                "  make test-bg-* — all bg variants pollable via make test-bg-status",
                "",
                "SUGGESTION: Run `make gate-background` or `make test-bg` instead, then",
                "poll status. Blocking the main thread on a long-running foreground",
                "operation is forbidden per AGENTS.md (Main-thread command restriction /",
                "background-gate workflow).",
              ].join("\n"))
            }
          }

          // test-batch: block if >3 FILES — avoids 10+ files blocking the main thread
          const isTestBatch = lrTarget === "test-batch"
          if (isTestBatch) {
            const filesMatch = trimmed.match(/FILES=['"]([^'"]*)['"]/)
            const filesStr = filesMatch ? filesMatch[1] : ""
            const fileCount = filesStr ? filesStr.split(/\s+/).filter(Boolean).length : 0
            if (fileCount > 3) {
              throw new Error(
                `BLOCKED: Large test-batch (${fileCount} files). ` +
                `Use \`make test-bg FILES='${filesStr}'\` to run in the background, ` +
                `or batch into groups of 3 or fewer. Foreground blocking with >3 test ` +
                `files prevents subagent dispatch.`
              )
            }

          }

          const targetMatch = trimmed.match(/^make\s+(\S+)/)
          const requestedTarget = targetMatch?.[1] || ""
          if (requestedTarget && !requestedTarget.startsWith("-") && !makeTargetExists(requestedTarget)) {
            _bashPolicyNudge = true
            throw new Error(
              formatBashBlockedMessage(
                trimmed,
                `unknown Make target '${requestedTarget}'. Read 'make help' and select an existing target, or add one first.`
              )
            )
          }

          // test-specific: warn if TESTFILE matches slow patterns (e2e, integration, redteam)
          const isTestSpecific = lrTarget === "test-specific"
          if (isTestSpecific) {
            const tfMatch = trimmed.match(/TESTFILE=['"]([^'"]*)['"]/) || trimmed.match(/TESTFILE=(\S+)/)
            const testfilePath = tfMatch ? tfMatch[1] : ""
            if (/e2e/i.test(testfilePath) || /integration/i.test(testfilePath) || /redteam/i.test(testfilePath)) {
              console.warn(
                `WARNING: test-specific on slow test (${testfilePath}). ` +
                `Consider using \`make test-bg TESTFILE='${testfilePath}'\` ` +
                `to run in the background instead.`
              )
            }
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
            _pendingPreflightGate = PREFLIGHT_GATE
          }

          const afterMake = trimmed.slice(5).trim()
          const words = afterMake.split(/\s+/)
          const targetName = words[0] || ""
          const restArgs = words.slice(1).join(" ")

          const toScan = restArgs.replace(/[A-Za-z_][A-Za-z0-9_]*=('[^']*'|"[^"]*"|\S*)/g, "")

          const MAKEFILE_TARGETS_WITH_FORBIDDEN_NAMES = [
            "git-status", "git-diff", "git-staged", "git-init", "git-log",
            "git-add", "git-add-all", "git-commit", "git-reset", "git-branch",
            "git-checkout", "git-merge", "feature-start", "feature-done",
            "delete-file",
          ]

          if (MAKE_ENFORCE) {
            if (MAKEFILE_TARGETS_WITH_FORBIDDEN_NAMES.includes(targetName)) {
              // Valid Makefile target that happens to contain a forbidden word in its name
              // Strip VAR=val assignments before checking for metacharacters
              const argsStripped = restArgs.replace(/[A-Za-z_][A-Za-z0-9_]*=('[^']*'|"[^"]*"|\S*)/g, "")
              if (SHELL_META_CHARS.test(argsStripped)) {
                const matched = argsStripped.match(SHELL_META_CHARS)
                throw new Error(
                  formatBashBlockedMessage(
                    trimmed,
                    `Shell metacharacter(s) forbidden in make args: ${matched?.join(", ")}. `
                  )
                )
              }
            } else {
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
          }
        }

        // Subagent guard: skip edit/write/TDD prompts for subagents
        const isSubagent = process.env.OPENCODE_SUBAGENT === "1"
        if (isSubagent) return

        if (input.tool === "edit" || input.tool === "write") {
          const filePath: string = output?.args?.filePath ?? output?.args?.path ?? ""

          // --- opencode.json schema guard -----------------------------------
          // Blocks writes/edits that introduce a top-level key not allowed by
          // the opencode Config schema (additionalProperties: false). opencode
          // silently drops unknown top-level keys, so any plugin relying on
          // them breaks at runtime with no observable error. This catches the
          // regression at edit time.
          //
          // For `write` we parse `output.args.content` directly. For `edit`,
          // the replacement snippet (`newString`) is partial and not parseable
          // in isolation, so we read the current file from disk, apply the
          // oldString→newString substitution once, and parse the result. If the
          // disk read or substitution fails we fail-OPEN for edit (a partial
          // guard is better than none — see comment below).
          const opencodeBaseName = filePath.split("/").pop() ?? filePath
          if (opencodeBaseName === "opencode.json") {
            try {
              let proposedContent: string | null = null
              if (input.tool === "write") {
                const c: unknown = output?.args?.content ?? output?.args?.text ?? null
                if (typeof c === "string") proposedContent = c
              } else {
                // edit: read current file and apply the single replacement.
                try {
                  const oldStr: string = output?.args?.oldString ?? ""
                  const newStr: string = output?.args?.newString ?? ""
                  const current = fs.readFileSync(filePath, "utf-8")
                  if (oldStr && current.includes(oldStr)) {
                    proposedContent = current.replace(oldStr, newStr)
                  } else {
                    // Cannot reliably reconstruct post-edit content; skip the
                    // check rather than risk a false positive. (Partial guard
                    // trade-off documented at top of block.)
                    proposedContent = null
                  }
                } catch {
                  proposedContent = null
                }
              }
              if (proposedContent !== null) {
                let parsed: unknown
                try {
                  parsed = JSON.parse(proposedContent)
                } catch (e) {
                  const msg = e instanceof Error ? e.message : String(e)
                  throw new Error(
                    "BLOCKED: opencode.json must be valid JSON. Parse error: " + msg,
                  )
                }
                if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
                  const keys = Object.keys(parsed as Record<string, unknown>)
                  const unknown = keys.filter(k => !ALLOWED_TOP_LEVEL_KEYS.has(k))
                  if (unknown.length > 0) {
                    throw new Error([
                      "BLOCKED: opencode.json top-level key(s) not in opencode schema: " +
                        unknown.slice().sort().join(", ") + ".",
                      "The Config type sets additionalProperties: false — these keys are silently",
                      "dropped by opencode, and any plugin relying on them is broken.",
                      "Allowed top-level keys are listed in tests/unit/test_opencode_json_schema.py",
                      "(ALLOWED_TOP_LEVEL_KEYS). If you intended to add a new env/config section,",
                      "put it inside the plugin that needs it (via the plugin's runtime env) or",
                      "inside `experimental` — NOT at the top level. See",
                      "https://opencode.ai/config.json $defs.Config.properties.",
                    ].join("\n"))
                  }
                }
              }
            } catch (e) {
              // RE-THROW: we intentionally want parse failures and unknown-key
              // violations to surface as denies. Only an opencode.json path
              // reaches this block, so a parse error here MUST block.
              throw e
            }
          }

          // --- Flag-file write prevention -------------------------------------
          // Port of .claude/hooks/no_flag_file_write_pretool.sh
          // Agents MUST NOT write .gate-status / .gate-failed / *.gate-status
          // directly — run_gate.sh is the sanctioned writer. Allowing agent
          // writes would let an agent forge a PASS gate status and bypass the
          // commit freshness guard (guardrail integrity breach).
          const baseName = filePath.split("/").pop() ?? filePath
          if (
            baseName === ".gate-status" ||
            baseName === ".gate-failed" ||
            baseName.endsWith(".gate-status")
          ) {
            throw new Error([
              "GUARDRAIL: agents must not write gate flag files",
              "(.gate-status / .gate-failed / *.gate-status) directly.",
              "",
              "run_gate.sh is the sanctioned writer (shell, not a harness tool call).",
              "Allowing agent writes would let an agent forge a PASS gate status",
              "and bypass the commit freshness guard. This write is DENIED.",
            ].join("\n"))
          }

          // --- Guardrail-integrity (extended) ---------------------------------
          // Port of .claude/hooks/guardrail_integrity_edit_pretool.sh.
          // Protects ALL hook + plugin files from edits that silently remove
          // enforcement. The original enforce-make.ts check covered enforce-make.ts
          // ONLY; this covers .claude/hooks/*.sh AND .opencode/plugin/*.ts so an
          // edit cannot defang a sibling guardrail.
          const isGuardrailFile = (
            filePath.includes("/.claude/hooks/") ||
            filePath.includes("/.opencode/plugin/") ||
            (filePath.endsWith(".sh") && filePath.includes("/hooks/")) ||
            filePath.includes("enforce-make.ts") ||
            filePath.includes("enforce-make.js") ||
            filePath.includes("enforce-floor.ts") ||
            filePath.includes("enforce-delegate.ts") ||
            filePath.includes("enforce-stop.ts")
          )
          if (isGuardrailFile) {
            const oldContent: string = output?.args?.oldString ?? ""
            const newContent: string = output?.args?.newString ?? ""
            // Enforcement tokens — any of these in code means "actively blocks".
            // Wholesale removal of ALL tokens from a guardrail file signals the
            // hook is being defanged (the fix-means-repair-never-disable policy).
            const guardrailPatterns = [
              "throw new Error",
              '"permissionDecision"',
              '"permissionDecision": "deny"',
              '"permissionDecision":"deny"',
              '"decision": "block"',
              '"decision":"block"',
              "TDD VIOLATION",
              "BLOCKED",
              "FORBIDDEN",
              "STOP-PATTERN",
              "GUARDRAIL INTEGRITY VIOLATION",
              "GATE CONCURRENCY",
              "exit 1",
              "sys.exit(1)",
            ]
            const hadAnyToken = guardrailPatterns.some(p => oldContent.includes(p))
            const newHasAnyToken = guardrailPatterns.some(p => newContent.includes(p))
            if (hadAnyToken && !newHasAnyToken && newContent.trim().length > 0) {
              throw new Error([
                "GUARDRAIL INTEGRITY VIOLATION (fix-means-repair-never-disable):",
                "The edit removes ALL enforcement tokens from " + filePath + ".",
                "",
                "old_string contained an active block/deny/throw/exit-1 enforcement",
                "token; new_string contains none.",
                "",
                'Per the fix-means-repair-never-disable policy: "fix" means make',
                "the feature work correctly, NEVER disable or weaken it. If the",
                "enforcement is noisy, narrow its conditions — do NOT delete the",
                "enforcement. Repair the hook; do not defang it. See AGENTS.md.",
              ].join("\n"))
            }
          }

          const isTest = (filePath.includes("/tests/") || filePath.includes("\\tests\\")) && !filePath.endsWith("conftest.py") && (filePath.includes("/test_") || filePath.includes("\\test_") || filePath.endsWith("_test.py"))
          const isProduction = filePath.includes("/src/") || filePath.includes("\\src\\")

          if (isTest) {
            const newContent: string = output?.args?.newString ?? ""
            const hasAssertion = newContent.includes("assert ") || newContent.includes("assert(")
            // Only enforce assertions on edits that introduce a TEST METHOD body
            // (contain "def test_" or "async def test_"). This avoids false
            // positives on legitimate non-test edits to test files: imports,
            // fixtures, engine/session setup, helper functions, decorators, and
            // config blocks — none of which contain assertions and all of which
            // the old size-based heuristic wrongly blocked. The guardrail still
            // fires on real test methods that call code without asserting
            // observable behavior (a known past class of "passing but tests-
            // nothing" bugs). Per the guardrail-integrity policy: narrow, do not
            // disable.
            const isTestMethodBody =
              newContent.includes("def test_") || newContent.includes("async def test_")
            if (isTestMethodBody && !hasAssertion) {
              throw new Error([
                "TDD QUALITY VIOLATION: Test code must contain assertions.",
                "",
                "File: " + filePath,
                "",
                "Every test MUST assert OBSERVABLE BEHAVIOR, not just call functions.",
                "Examples of good assertions:",
                '  assert "▶" in rendered  — verify visual output changes',
                "  assert state['selected_idx'] == 1  — verify state mutation",
                "  assert resp.status_code == 200  — verify HTTP behavior",
                "",
                "BAD: just calling a function without checking the result.",
                "GOOD: checking that the output/state/rendering actually changed.",
                "",
                "Past bugs were caused by tests that 'passed' but tested nothing.",
              ].join("\n"))
            }
          }

          if (isProduction && !isTest) {
            // Narrowing (guardrail-integrity policy): skip the test-file
            // requirement for edits that only touch comments/docstrings — they
            // cannot change runtime behaviour. Real code edits are still gated.
            const oldContent: string = output?.args?.oldString ?? ""
            const newContent: string = output?.args?.newString ?? ""
            if (isNonBehavioralEdit(oldContent, newContent)) {
              // Comment/docstring-only edit — no test file required.
            } else {
              const fs = await import("node:fs")
              const path = await import("node:path")
              const srcMatch = filePath.match(/[\/\\]src[\/\\](.+)\.py$/)
              if (srcMatch) {
                const modulePath = srcMatch[1]
                const pathParts = modulePath.split(/[\/\\]/)
                const candidates = [
                  modulePath.replace(/[\/\\]/g, "_"),
                  pathParts.pop() || "",
                ]
                // For __init__.py packages, the parent directory name is the
                // meaningful module name (e.g. pricing_intel/__init__.py ->
                // "pricing_intel"). Add it as a candidate so the broad match
                // finds sibling test files (e.g. test_pricing_intel.py).
                const leafName = pathParts[pathParts.length - 1]
                if (leafName && leafName !== "__init__") {
                  candidates.push(leafName)
                }
                let testExists = false
                for (const candidate of candidates) {
                  const testDir = path.resolve(filePath.split(/[\/\\]src[\/\\]/)[0], "tests", "unit")
                  for (const prefix of ["test_"]) {
                    for (const suffix of [".py"]) {
                      try {
                        fs.accessSync(path.join(testDir, prefix + candidate + suffix))
                        testExists = true
                        break
                      } catch {}
                      // Broad match: check if any test file exists that references the module
                      try {
                        const files = fs.readdirSync(testDir)
                        const shortName = candidate.split("_").pop() || candidate
                        for (const f of files) {
                          if (f.startsWith("test_") && f.includes(shortName) && f.endsWith(".py")) {
                            testExists = true
                            break
                          }
                        }
                      } catch {}
                    }
                    if (testExists) break
                  }
                  if (testExists) break
                }
                if (!testExists) {
                  throw new Error([
                    "TDD VIOLATION: No corresponding test file found for " + filePath,
                    "",
                    "Before editing production code, you MUST:",
                    "  1. Write a failing test under tests/unit/ that covers the behavior.",
                    "  2. Run the test to confirm it fails.",
                    "  3. Then edit the production code to make it pass.",
                    "",
                    "Looked for: test_" + candidates[0] + ".py or test_" + candidates[candidates.length - 1] + ".py",
                    "in tests/unit/",
                    "",
                    "Skipping TDD is a policy violation. See AGENTS.md.",
                  ].join("\n"))
                }
              }
            }
          }
        }
      },

      "tool.execute.after": async (input, output) => {
        if (input.tool === "bash") {
          const command: string = (output as any)?.args?.command ?? (input as any)?.args?.command ?? ""
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
              _pendingCommitReminder = true
            }
          }
        }
      },

      "experimental.chat.system.transform": async (_input, output) => {
        // process.env.OPENCODE_SUBAGENT guard
        if (process.env.OPENCODE_SUBAGENT === "1") return output
        // --- BASH-AVAILABILITY CHECK (2026-07-03) -------------------------------
        // Reads SESSION.md for the "CRITICAL: bash tool unavailable" banner.
        // If present, injects a prominent warning at the VERY TOP of the system
        // prompt so the agent knows IMMEDIATELY that `make` targets cannot run.
        // This prevents the 10+ turn diagnosis-loop pattern: the agent tries
        // `make`, fails, analyzes for 15 turns instead of adapting.
        let bashWarning = ""
        try {
          const sessionPath = path.join(process.cwd(), "SESSION.md")
          if (fs.existsSync(sessionPath)) {
            const sessionContent = fs.readFileSync(sessionPath, "utf8")
            if (sessionContent.includes("CRITICAL: bash tool unavailable")) {
              bashWarning = [
                "⛔⛔⛔ CRITICAL — BASH TOOL UNAVAILABLE ⛔⛔⛔",
                "",
                "SESSION.md line ~9 says: \"CRITICAL: bash tool unavailable —",
                "`make` targets cannot run.\" This was pre-documented by the prior",
                "session. The bash tool is NOT in your tool list (provider/model",
                "limitation). Do NOT attempt `make <target>` — it will fail.",
                "",
                "WHAT TO DO INSTEAD:",
                "  - Use read, edit, write, grep, glob tools directly.",
                "  - Do NOT dispatch subagents expecting them to run `make`.",
                "  - Do NOT spend turns diagnosing WHY bash is missing.",
                "  - State \"bash unavailable, adapting\" in ≤1 line, then WORK.",
                "",
                "WHAT IS FORBIDDEN:",
                "  - 10+ turns analyzing tool-unavailable errors (BUG, log in BUGS.md)",
                "  - Sending text-only explanations of the problem",
                "  - Asking the user to fix/reconfigure/restart opencode",
                "  - Trying to call 'Bash' (uppercase — tool name is lowercase 'bash')",
                "",
                "POLICY: AGENTS.md \"Bash Tool Unavailability — 3-Step Diagnosis\"",
                "",
              ].join("\n")
            }
          }
        } catch { /* fail open */ }

        const mechanicalContract = [
          "⛔ MECHANICAL CONTRACT — VIOLATIONS ARE BLOCKED",
          "",
          "1. Only `make <target>`. No metacharacters (`|`, `;`, `&&`). FORBIDDEN.",
          "2. Pending todos ⇒ tool call. Text-only with pending work = BLOCKED.",
          '3. "Done" requires: `make gate` green + `TASKS.md` evidence. Nothing else.',
          "4. TDD: failing test first. `make test-count` 0 errors before commit.",
          "5. Found a gap? Fix it now. Never list it and ask. BLOCKED if you ask.",
          "6. Trust gate output, not SESSION.md. Gate exit codes are truth.",
          "7. Read `TASKS.md` for current work. Read `BUGS.md` before claiming done.",
          "8. BATCH ALL independent tool calls in ONE message. Serial calls = BLOCKED.",
          "9. Subagent returns empty? DO NOT re-dispatch same work. Diagnose first.",
          "   — Check if bash tool is available (list your tools before dispatching).",
          "   — If bash is missing, adapt: use read/edit/write/grep directly.",
          "   — 3+ empty subagent results in a row = TOOL AVAILABILITY BUG, not retry.",
          "",

          "CRITICAL: Bash Tool Unavailability — 3-Step Diagnosis (MAX 2 TURNS)",
          "When `make` commands fail or bash is unavailable:",
          "  Step 1 (ONE turn, parallel): (a) check if bash is in your tool list,",
          "    (b) read SESSION.md for the \"CRITICAL: bash tool unavailable\" banner,",
          "    (c) read opencode.json for bash permissions.",
          "  Step 2 (ONE turn): if bash absent → adapt (read/edit/write/grep only);",
          "    if permissions wrong → fix opencode.json ordering. Never spend 10+",
          "    turns diagnosing. BUGS.md records bash-diagnosis-relapse incidents.",
          "",
          "STOP-PATTERN DETECTION: Text-only responses with pending work are BLOCKED.",
          "",
          "ROOT-CAUSE-ONLY FIX: Fix root causes, never symptoms.",
          "  - Guardrail/plugin broken? Fix the logic, don't disable it.",
          "  - CI red? Fix the tests, don't skip them. Release blocked? Fix blocker, don't bypass.",
          "  - See AGENTS.md \"Root-Cause-Only Fix Policy.\"",
          "",
          "Full rationale in AGENTS.md. This contract is all you need for mechanics.",
          "",
        ].join("\n");
        const policyInjection = [
          BATCHING_POLICY,
          "",
          BASH_METACHAR_POLICY,
          "",
          TASK_COMPLETION_WARNING,
          "",
          SELF_DIRECTED_WORK_WARNING,
        ].join("\n")
        if (typeof output === "string") {
          output = bashWarning + "\n" + mechanicalContract + "\n\n" + policyInjection + "\n\n" + output
        }
        return output // FORBIDDEN stop patterns enforced by this contract + response.transform hook
      },

      "experimental.text.complete": async (_input, output) => {
        if (process.env.OPENCODE_SUBAGENT === "1") return output
        if (typeof output !== "string") return output
        // ratchet hasLocalWork pending-work state check. ratchetLines.length > 0 keeps completion-sounding output blocked.
        // Gate status evidence must include lint PASS, typecheck PASS, collect PASS, and test PASS.
        // Gate-red guard: if .gate-status is FAIL, prepend a hard warning so
        // the agent cannot claim done while the gate is broken.
        const hasRed = (() => {
          try {
            const p = path.join(process.cwd(), ".gate-status")
            if (fs.existsSync(p)) {
              const c = fs.readFileSync(p, "utf8")
              return /FAIL/.test(c)
            }
          } catch {}
          return false
        })()
        if (hasRed) {
          return "[GATE RED] Fix failures before committing.\n\n" + output
        }
        // Bash-policy nudge: if a bash command was blocked this turn for
        // violating the make-only policy, re-inject the rule so the next
        // turn corrects. Consumed and cleared here (and reset in session.idle).
        if (_bashPolicyNudge) {
          _bashPolicyNudge = false
          return BASH_POLICY_HEADER + BASH_POLICY_RULE + BASH_POLICY_FIX + BASH_POLICY_REF + "\n\n" + output
        }
        return output
      },

      "session.idle": async () => {
        // Per-turn reset: clear transient flags so they don't bleed across
        // turns. Required so a blocked bash in one turn does not nag forever.
        _bashPolicyNudge = false
        _makeTurnState.dispatchCount = 0
        _makeTurnState.toolCallMade = false
        _pendingCommitReminder = false
        _pendingPreflightGate = ""
      },


    }

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return
      const impl = loadHotModule("enforce-make", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },

    "tool.execute.after": async (input, output) => {
      const impl = loadHotModule("enforce-make", defaultImpl)
      const fn = impl["tool.execute.after"]
      return fn ? await fn(input, output) : undefined
    },

    "experimental.chat.system.transform": async (_input, output) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return output
      const impl = loadHotModule("enforce-make", defaultImpl)
      const fn = impl["experimental.chat.system.transform"] || impl["system.transform"]
      return fn ? await fn(_input, output) : output
    },

    "experimental.text.complete": async (_input, output) => {
      if (isSubagent()) return output
      const impl = loadHotModule("enforce-make", defaultImpl)
      const fn = impl["experimental.text.complete"] || impl["text.complete"]
      return fn ? await fn(_input, output) : output
    },

    "event": async (input: { event: { type: string } }) => {
      // opencode 1.17.9 exposes lifecycle/idle signals via the `event` hook
      // (event.event.type === "session.idle"). The previous direct
      // "session.idle" hook key was rejected by the Plugin.add registry and
      // crashed opencode at boot (TypeError: undefined is not an object
      // evaluating 'N.event'). See AGENTS.md "Codify Improvements".
      try {
        const evType = input?.event?.type
        if (evType === "session.idle") {
          const impl = loadHotModule("enforce-make", defaultImpl)
          const fn = (impl as Record<string, ((...args: unknown[]) => Promise<void>) | undefined>)>["session.idle"]
          if (fn) { await fn() }
        }
      } catch { /* fail-open */ }
    },

  }
}) satisfies Plugin
