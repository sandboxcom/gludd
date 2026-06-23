import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

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

let _pendingCommitReminder = false
let _pendingPreflightGate = ""

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

// --- Gate-concurrency probe (port of gate_concurrency_pretool.sh) -------------
// Two independent signals (either fires the block): fresh basetemp mtime, OR
// pgrep for a running pytest. FAIL-OPEN on any error (can't probe -> allow).
function isGateAlreadyRunning(): boolean {
  try {
    const { execSync } = require("node:child_process")
    const fsLocal = require("node:fs")
    const BASETEMP = process.env.GLUDD_GATE_BASETEMP || "/tmp/gludd-gate-basetemp"
    const STALE_SECS = parseInt(process.env.GLUDD_GATE_STALE_SECS || "600", 10)
    // Signal A: basetemp exists + fresh mtime.
    try {
      const st = fsLocal.statSync(BASETEMP)
      const ageSec = (Date.now() - st.mtimeMs) / 1000
      if (ageSec < STALE_SECS) return true
    } catch {}
    // Signal B: pgrep -f pytest. Exit 0 = match found.
    if (process.env.GLUDD_GATE_PYTEST_RUNNING === "1") return true
    if (process.env.GLUDD_GATE_PYTEST_RUNNING === "0") return false
    try {
      execSync("pgrep -f pytest", { stdio: ["pipe", "pipe", "pipe"] })
      return true  // pgrep exited 0 — a pytest process is running
    } catch {
      return false  // pgrep exited non-zero — no match
    }
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
  "all passed",
  "all complete",
  "all done",
  "phase complete",
  "everything is done",
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
  // Check for completion-sounding phrases
  if (COMPLETION_SOUNDING.some(p => lower.includes(p))) {
    // Only flag as stop pattern if the response is short (a summary, not
    // ongoing work narration that happens to contain one of these words).
    if (text.length < 500) return true
    // Also flag if it contains explicit permission-seeking
    if (lower.includes("want me to") || lower.includes("should i") || lower.includes("shall i")) return true
  }
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
              "be in progress (basetemp /tmp/gludd-gate-basetemp is fresh OR pgrep",
              "found a pytest process). Launching a second concurrent pytest",
              "triggers keep-last-3 basetemp rotation, which deletes the first",
              "gate's worker dirs mid-flight and produces hundreds of spurious",
              "FileNotFoundError errors (the 2026-06-15 208-error incident).",
              "Wait for the current gate to finish, then launch this one.",
              "This dispatch is BLOCKED.",
            ].join("\n"))
          }
        }

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
          const m = trimmed.match(/^make\s+(\S+)/)
          const lrTarget = m ? m[1] : ""
          const isGate = lrTarget === "gate"
          const isTestUnit = lrTarget === "test-unit"
          const isBareTest =
            lrTarget === "test" &&
            !trimmed.includes("TESTFILE=") &&
            !trimmed.includes("NO_XDIST=1")
          const isQa = lrTarget === "qa"
          const isTestE2e = lrTarget === "test-e2e"
          const isValidate = lrTarget === "validate"
          if (isGate || isTestUnit || isBareTest || isQa || isTestE2e || isValidate) {
            throw new Error([
              "BLOCKED: Long-running foreground command. Use `make gate-background`",
              "instead, or dispatch to a subagent. Foreground blocking prevents",
              "subagent dispatch and UI updates.",
              "",
              "While this command runs, the bash tool blocks for the entire",
              "duration (make gate ~40 min, make test-unit ~27 min, make qa,",
              "make test-e2e, and make validate are equally long-running).",
              "During that time NO subagents can be dispatched and NO UI updates",
              "reach the user. Either:",
              "  1. Run `make gate-background` (background variant), or",
              "  2. Dispatch the gate/test to a subagent (preferred).",
              "",
              "Allowed in foreground:",
              "  make lint, make typecheck, make test-count, make collect-check,",
              "  make test TESTFILE=<path>, make test NO_XDIST=1",
            ].join("\n"))
          }
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
          _pendingPreflightGate = PREFLIGHT_GATE
        }

        const afterMake = trimmed.slice(5).trim()
        const words = afterMake.split(/\s+/)
        const targetName = words[0] || ""
        const restArgs = words.slice(1).join(" ")

        const toScan = restArgs

        const MAKEFILE_TARGETS_WITH_FORBIDDEN_NAMES = [
          "git-status", "git-diff", "git-staged", "git-init", "git-log",
          "git-add", "git-add-all", "git-commit", "git-reset", "git-branch",
          "git-checkout", "git-merge", "feature-start", "feature-done",
          "delete-file",
        ]

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

      if (input.tool === "edit" || input.tool === "write") {
        const filePath: string = output?.args?.filePath ?? ""

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
            _pendingCommitReminder = true
          }
        }
      }
    },

    "experimental.chat.system.transform": async (_input, output) => {
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
        "",
        "STOP-PATTERN DETECTION: Text-only responses with pending work are BLOCKED.",
        "The chat.response.transform hook replaces completion claims when gate is red.",
        "Full rationale in AGENTS.md. This contract is all you need for mechanics.",
        "",
      ].join("\n");
      const policyInjection = [
        BASH_METACHAR_POLICY,
        "",
        TASK_COMPLETION_WARNING,
        "",
        SELF_DIRECTED_WORK_WARNING,
      ].join("\n")
      if (typeof output === "string") {
        output = mechanicalContract + "\n\n" + policyInjection + "\n\n" + output
      }
      return output // FORBIDDEN stop patterns enforced by this contract + response.transform hook
    },

    "experimental.chat.response.transform": async (_input, output) => {
      if (typeof output !== "string") return output

      const gateStatusPath = path.join(process.cwd(), ".gate-status")
      if (fs.existsSync(gateStatusPath)) {
        const gateContent = fs.readFileSync(gateStatusPath, "utf-8")
        const hasGreen = (
          gateContent.includes("lint PASS") &&
          gateContent.includes("typecheck PASS") &&
          gateContent.includes("collect PASS") &&
          gateContent.includes("test PASS")
        )
        if (!hasGreen) {
          output = [
            "⛔ GATE IS RED — RESPONSE BLOCKED ⛔",
            "",
            ".gate-status is red or stale. Completion claims are BLOCKED.",
            "Your message has been COMPLETELY REPLACED.",
            "",
            "Run `make gate`, fix all failures, then continue working.",
            "Do NOT send another text message without tool calls.",
          ].join("\n")
          return output
        }
      }

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
          "",
          STOP_PATTERN_BLOCK,
        ].join("\n")
        return output
      }

      // State-based check: if the ratchet has remaining entries,
      // the project is NOT done. Block any completion-looking response.
      const ratchetPath = path.join(process.cwd(), "config", "ratchet.yml")
      if (fs.existsSync(ratchetPath)) {
        const ratchetContent = fs.readFileSync(ratchetPath, "utf-8")
        const ratchetLines = ratchetContent.split("\n").filter(
          l => l.trim() && !l.trim().startsWith("#") && l.includes(": \"")
        )
        const hasPendingWork = ratchetLines.length > 0
        const responseLower = output.toLowerCase()
        const soundsComplete = (
          responseLower.includes("all passed") ||
          responseLower.includes("phase") && responseLower.includes("complete") ||
          responseLower.includes("key accomplishments") ||
          responseLower.includes("what was implemented") ||
          responseLower.includes("task |") ||
          responseLower.includes("| what was")
        )
        if (hasPendingWork && soundsComplete) {
          output = [
            "⛔ STOP-PATTERN DETECTED — INCOMPLETE WORK ⛔",
            "",
            `config/ratchet.yml has ${ratchetLines.length} known-failure entries.`,
            "The project is NOT complete. Your completion claim is BLOCKED.",
            "",
            "You MUST continue working. Call your tools NOW.",
            "",
          ].join("\n")
          return output
        }
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

      // Surface the preflight-gate notice when a test-and-commit was just
      // attempted (flag set in tool.execute.before). Reset after surfacing.
      if (_pendingPreflightGate) {
        const preflight = _pendingPreflightGate
        _pendingPreflightGate = ""
        output = output + "\n\n" + preflight
      }

      // Surface the commit reminder when a prior test run passed (flag set in
      // tool.execute.after). Reset after surfacing so it fires once.
      if (_pendingCommitReminder) {
        _pendingCommitReminder = false
        output = output + "\n\n" + COMMIT_REMINDER
      }

      return output
    },
  }
}) satisfies Plugin
