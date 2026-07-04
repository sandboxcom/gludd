import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

// enforce-stop.ts — opencode-native port of the Claude turn-lifecycle hooks
// that prevent premature stops, deferrals, and question-as-blocking.
//
// Ports (consolidated by function):
//   .claude/hooks/no_blocking_questions_pretool.sh
//     -> tool.execute.before (question)  [deny the question tool]
//   .claude/hooks/no_wait_stop.sh
//     -> experimental.chat.response.transform  [deferral-pattern block]
//   .claude/hooks/multitasking_backlog_stop.sh
//     -> experimental.chat.response.transform  [open-backlog block]
//   .claude/hooks/session_start_orchestrate.sh
//     -> experimental.chat.system.transform   [orchestration injection]
//
// FAIL-OPEN: every check returns silently on any internal error.
//
// IMPORTANT — this plugin is SEPARATE from enforce-make.ts so a bug here cannot
// break the make-only Bash enforcement. Keep the files split.
//
// ADVISORY vs ENFORCING:
//   The claude hooks `no_wait_stop.sh` and `agent_floor_stop.sh` became
//   ADVISORY by default on 2026-06-21 (user directive). Blocking restored via
//   GLUDD_NO_WAIT_ENFORCE=1 / GLUDD_FLOOR_ENFORCE=1. This plugin honors the
//   same env-var gates so the behavior matches the claude layer exactly.

// ============================================================================
// CONFIG (mirrors the claude env var names so the same knobs work in opencode)
// ============================================================================
const FLOOR = parseInt(process.env.CLAUDE_AGENT_FLOOR || "10", 10)
const NO_WAIT_ENFORCE = process.env.GLUDD_NO_WAIT_ENFORCE !== "0"  // DEFAULT: blocking (2026-06-22)

const STATE_FILE =
  process.env.GLUDD_STOP_STATE_FILE ||
  "/tmp/gludd-stop-state.json"

interface StopStateCache {
  ts: number
  ratchetEntries: number
  tasksMdUnchecked: boolean
  gateStatusRed: boolean
  repoPending: boolean
  backlogOpen: number
  backlogItems: string[]
  hasPendingWork: boolean
  ciVerdictPendingOrRed: boolean
}

const turnState: { accumulatedText: string; blocked: boolean } = {
  accumulatedText: "",
  blocked: false,
}

// CI verdict cache — TTL 60s (Deficiency A+B: shell out to make ci-verdict,
// parse GREEN/RED/PENDING, cache to avoid excessive queries)
interface CiVerdictCache {
  ts: number
  isPendingOrRed: boolean
}

let ciVerdictCache: CiVerdictCache | null = null

// ============================================================================
// NO-BLOCKING-QUESTIONS (port of no_blocking_questions_pretool.sh)
// User directive (2026-06-18): "never interrupt work to ask; default to action."
// A passive memory did not stop the relapse, so the claude layer denied the
// AskUserQuestion tool. In opencode the equivalent tool is `question`.
// This hook denies every question call — the agent must decide, state its
// assumption, and proceed.
// ============================================================================
const QUESTION_DENY_REASON = [
  "BLOCKING QUESTION DENIED — user standing directive: never interrupt work to",
  "ask. DEFAULT TO ACTION: choose the most reasonable option yourself, state in",
  "one line the assumption you are making, and PROCEED. Do NOT re-attempt the",
  "question. For a genuinely destructive/irreversible external action, state",
  "the plan + the risk and proceed (or note it and continue with the safe",
  "default) rather than blocking — the user will redirect you if needed.",
  "Keep moving.",
].join(" ")

// ============================================================================
// STOP-LIKE TOOL DENY (BUGS.md structural fix #2 — 2026-06-30)
// When the agent tries to commit/push/release/merge ("I'm done") but
// TASKS.md has unchecked items or config/ratchet.yml still has entries,
// the tool call is denied. This catches premature completion attempts
// that the response.transform path (text-analysis only) may miss.
// ============================================================================
const STOP_LIKE_TARGETS_RE = /^make\s+(git-commit|commit-no-verify|ship-commit|git-push-branch|git-push-branch-nv|git-push-sandboxcom|git-push-sandboxcom-main|git-push-master|git-tag-push|release-cut|release-promote|test-and-commit|repo-commit|feature-done|release-recut|release-branch-new|git-merge)(\s|$)/

function stopLikeDenyMessage(taskMd: boolean, ratchetEntries: number): string {
  return [
    "⛔ STOP-LIKE TOOL BLOCKED — PENDING WORK EXISTS:",
    `TASKS.md unchecked items: ${taskMd ? "yes" : "no"}`,
    `config/ratchet.yml entries: ${ratchetEntries}`,
    "",
    "You are trying to commit/push/release while the project still has",
    "known-unfinished work. This is the exact premature-stop pattern",
    "that BUGS.md records 20+ times — the agent declares completion",
    "while TASKS.md items remain unchecked or ratchet entries are active.",
    "",
    "Fix the pending work FIRST before committing/pushing:",
    "  1. Complete all unchecked TASKS.md items (implement, test, verify)",
    "  2. Burn all ratchet.yml entries (fix the test failures, re-run make gate)",
    "  3. Re-run this tool call after the pending work is addressed.",
    "",
    "Do NOT bypass this. Do NOT use repo-commit or commit-no-verify to",
    "dodge it — those are still stop-like. The work itself is the",
    "deliverable; the commit is just the recording of completed work.",
  ].join("\n")
}

// ============================================================================
// NO-WAIT-STOP (port of no_wait_stop.sh)
// Blocks turn-end when the final message DEFERS to the user (permission-seek,
// hold, "want me to...", "your call", etc.) OR uses constraint-as-stopsign
// phrasings ("isn't possible", "limitation", "no way") without a workaround.
// BLOCKING by default (2026-06-22 user directive); make advisory via
// GLUDD_NO_WAIT_ENFORCE=0.
// Returns: null = allow response, string = replacement response (when blocking).
// ============================================================================
const NO_WAIT_PATTERNS: RegExp[] = [
  // Permission-seek / deferral
  /\bsay so\b/i, /\bsay the word\b/i,
  /\btell me to\b/i, /\bif you want me to\b/i, /\bif you'?d (?:like|prefer|rather)\b/i,
  /\bwant me to\b/i, /\bwould you like me to\b/i, /\bdo you want me to\b/i,
  /\bshould i\b/i, /\bshall i\b/i,
  /\blet me know\b/i, /\bjust let me know\b/i,
  /\bon your go\b/i, /\byour go[- ]?ahead\b/i, /\bgive me the go\b/i,
  /\bwhen you'?re ready\b/i, /\bwhenever you'?re ready\b/i, /\bready when you are\b/i,
  /\bi'?ll hold\b/i, /\bholding (?:here|for|off)\b/i, /\bi'?ll wait\b/i, /\bi'?ll pause\b/i,
  /\bstanding by\b/i, /\bawait(?:ing)? your\b/i, /\bwaiting (?:for|on) (?:your|you|the)\b/i,
  /\beither way\b.{0,40}?\?/i, /\bpoint a fresh session\b/i, /\bfresh (?:context|session)\b/i,
  /\bif you'?d rather i proceed\b/i, /\bwant me to proceed\b/i, /\bproceed\?\s*$/i,
  /\blet me know (?:if|when|whether|which|what)\b/i,
  /\byour call\b/i, /\bup to you\b/i, /\bleave (?:it|that|this|the\b.*) to you\b/i,
  /\bor hold\b/i, /\bcommit or hold\b/i, /\bi'?ll leave (?:it|that|this)?\s*to (?:your|you)\b/i,
  /\bi can (?:commit|push|apply|proceed|hold)\b.{0,30}?\bor\b/i,
  /\bwhich (?:would you|do you want|one)\b/i, /\bprefer (?:that )?i\b/i,
  // Constraint-as-stopsign (policy: "Constraints Are To Engineer Around")
  // A naked "can't / isn't possible / we have to wait" without a paired
  // workaround is parking the problem on the user.
  /\bisn'?t possible\b/i,
  /\bis not possible\b/i,
  /\bnot possible to\b/i,
  /\bno way to\b/i,
  /there'?s no way\b/i,
  /\bit'?s a limitation\b/i,
  /\bis a limitation\b/i,
  /\bwe have to wait\b/i,
  /\bhave to wait for\b/i,
  /\bnothing (?:i|we) can do\b/i,
  /\bcan'?t be done\b/i,
  /\bthe api does(?:n'?t| not) (?:support|expose|provide)\b/i,
  // Status-report-as-handoff: describing the next step instead of executing it
  /\bnext (?:step|steps|action|concrete step)\b/i,
  /\bthe next (?:step|thing|action|move|concrete)\b/i,
  /\bremaining (?:work|step|steps|item|items|action|task)\b/i,
  /\b(?:still need to|yet to|left to|remains? to|the remaining)\b/i,
  /\brequires?\b.{0,24}?\b(?:pr\b|pull request|push|merge|manual)\b/i,
  /\bwould (?:need|require) (?:a |an |to )?\b(?:pr|push|merge|manual|session|release)\b/i,
  /\bi have not (?:pushed|opened|taken|merged|run|applied|done|created)\b/i,
  /\bi haven'?t (?:pushed|opened|taken|merged|run|applied|done|created)\b/i,
  /\bnot yet (?:pushed|opened|taken|merged|run|applied|done|created)\b/i,
  /\bhave not taken\b/i, /\boutward action i have not\b/i,
  /\bcaptured (?:for|as)\b.{0,40}?\bfollow-?up\b/i, /\bfor a future pr\b/i,
  // Status-report-as-deliverable (2026-06-24 incident): agent presented a
  // markdown status table + "The ONLY remaining blocker" + "You can help:
  // check the Actions UI" + "The moment CI goes green, I execute" — a status
  // report that ended the turn without a tool call. These patterns catch that.
  /\bthe only (?:remaining|open|outstanding)\b/i,   // "the ONLY remaining blocker"
  /\byou can (?:help|check|verify|look at)\b/i,      // "You can help: check..."
  /\bstatus of (?:your|the|all|this)\b/i,            // "Status of your requests"
  /\bthe moment\b/i,                                  // "The moment CI goes green" (standalone)
  /\ball code work is (?:done|complete|committed)\b/i, // "all code work is DONE"
  // Past-tense completion framing (2026-06-28 incident): agent said
  // "## Done — answer to your question" and ended the turn with uncommitted
  // work. The state-based check missed it (ratchet empty), and these phrases
  // were absent from the vocabulary list. They catch Q&A-recap-as-finale:
  // "done — answer", "answer to your question", "## done", "what i changed",
  // "here's what i did", "i landed/pushed/shipped N commits", etc.
  /\bdone — answer\b/i,
  /\banswer to your question\b/i,
  /^##\s+done\b/im,
  /\bwhat i changed\b/i,
  /\bsingle canonical\b/i,
  /\bwhat i (?:did|changed|implemented|delivered)\b/i,
  /\b(?:here'?s|here is) what (?:i|we) (?:did|changed|shipped|delivered)\b/i,
  /\bi (?:made|landed|pushed|committed|shipped|applied)\s+(?:\d+|several|three|two|four)\b/i,
  // Q&A-recap bolded question headers (BUGS.md #2026-06-28): a completion report
  // wearing a different coat — bolded markdown headers phrased as questions.
  /\*\*What changed\?\*\*/i,
  /\*\*Why\?\*\*/i,
  /\*\*What'?s (?:left|next|remaining)\?\*\*/i,
  /\*\*What (?:did|was|have) (?:you|i|we)\b[^*]*\?\*\*/i,
  /\*\*How\b[^*]*\?\*\*/i,
  // Item-count-as-completion (BUGS.md #2026-06-30): agent reports "16 items
  // completed" with all checkboxes ticked and stops — an evidence ledger
  // worn as a completion claim. See also the all-checked checkbox table
  // detector in responseLooksTerminal.
  /\b\d+\s+items?\s+(?:completed|done|ticked|checked)\b/i,
  /\b\d+\s+tasks?\s+(?:completed|done|ticked|checked)\b/i,
  /\ball\s+items?\s+(?:completed|done|ticked|checked)\b/i,
  /\bevidence (?:ledger|table)\b/i,
  // CI-red-as-stop (BUGS.md structural fix #3): the agent mentions CI is red
  // and then stops. "Done" while CI is red is never legitimate — it ignores
  // the project's real validation state. These patterns catch the agent
  // reporting CI red/failing and still presenting a completion.
  /\bCI\s+is\s+red\b/i,
  /\bCI\s+is\s+failing\b/i,
  /\bCI\s+is\s+not green\b/i,
  /\bCI\s+run\s+failed\b/i,
  /\bthe\s+(?:CI|pipeline|gate)\s+is\s+(?:red|failing)\b/i,
  /\b(?:CI|pipeline|gate)\s+(?:still|remains)\s+(?:red|failing)\b/i,
]

// ============================================================================
// STATE-BASED TERMINAL-RESPONSE DETECTOR (BUGS.md audit #1 anti-stop fix)
// Instead of matching specific completion phrases (Whac-A-Mole), this asks two
// state questions: (a) does the repo have pending work? (b) does this response
// LOOK like a finale? If both are true, block regardless of wording.
// Signals: markdown table, uppercase DONE/COMPLETE/SHIPPED, long non-question
// body, commit-hash pattern (7-40 hex chars).
// ============================================================================
function responseLooksTerminal(text: string): boolean {
  // Markdown table: a line with | ... | ... | (at least two pipes, real content)
  if (/\|[^\n|]+\|[^\n|]+\|/.test(text)) return true
  // Uppercase completion banner
  if (/\b(?:DONE|COMPLETE|SHIPPED)\b/.test(text)) return true
  // Long body that doesn't end with a question mark
  if (text.length > 200 && !/\?\s*$/.test(text)) return true
  // Commit hash pattern: 7-40 hex chars on a word boundary
  if (/\b[0-9a-f]{7,40}\b/.test(text)) return true
  // Q&A recap headers: bolded question-style markdown headers (**What changed?**, **Why?**, etc.)
  // A response structured as a Q&A recap with bolded question headers is a completion
  // report wearing a different coat (BUGS.md #2026-06-28 incident).
  const qaHeaders = text.match(/^\*\*[^*]+\?\*\*$/gm)
  if (qaHeaders && qaHeaders.length >= 3) return true
  // All-checked checkbox table (BUGS.md #2026-06-30): 3+ [x] rows and 0
  // unchecked [ ] rows = a completion evidence ledger worn as a stop claim.
  // This is the exact pattern the agent used: 16-row table of completed
  // items with all checkboxes ticked, presented as a terminal summary.
  const checkboxesChecked = (text.match(/- \[x\]/gi) || []).length
  const checkboxesUnchecked = (text.match(/- \[ \]/gi) || []).length
  if (checkboxesChecked >= 3 && checkboxesUnchecked === 0) return true
  // Item-count completion claim (BUGS.md #2026-06-30): "\d+ items completed"
  // or "\d+ items done" — counting completed items as a stop signal.
  if (/\b\d+\s+items?\s+(?:completed|done|ticked|checked)\b/i.test(text)) return true
  // "Session summary:" plain-text pattern (Deficiency C)
  if (/\b[Ss]ession\s+summary:?\b/i.test(text)) return true
  // Bold "**Session summary:**" markdown pattern (Deficiency C)
  if (/\*\*[Ss]ession\s+[Ss]ummary:?\*\*/i.test(text)) return true
  // Lines starting with "Session summary" followed by bullet points (Deficiency C)
  if (/^[Ss]ession\s+[Ss]ummary/im.test(text.trim()) && /^[-*]\s/m.test(text)) return true
  // Multi-line bold-header + bullet-point combo: >=3 bold headers AND >=3 bullet
  // points in the same response = a summary report wearing markdown formatting
  // (Deficiency C)
  const boldHeaders = text.match(/^\*\*[^*]+\*\*$/gm)
  const bulletPoints = text.match(/^\s*[-*]\s+/gm)
  if (boldHeaders && boldHeaders.length >= 3 && bulletPoints && bulletPoints.length >= 3) return true
  return false
}

function detectNoWaitPattern(text: string): boolean {
  return NO_WAIT_PATTERNS.some(p => p.test(text))
}

// ============================================================================
// REPO PENDING-WORK DETECTOR (2026-06-28 incident fix)
// The ratchet-only proxy was broken: it tracks TEST failures, not commit/push
// state. An agent that did work locally (uncommitted or unpushed) then stopped
// with a "## Done — answer to your question" finale bypassed the state-based
// check because ratchet.yml was empty. This function closes that hole by asking
// the actual git state: unpushed commits on a tracked branch OR uncommitted
// changes in the working tree = pending work.
// FAIL-OPEN: returns false on any error (not a git repo, no upstream, etc.).
// ============================================================================
function repoHasPendingWork(): boolean {
  try {
    const { execSync } = require("node:child_process")
    const cwd = process.cwd()
    try {
      const unpushed = execSync("git log --oneline @{u}..HEAD", {
        cwd,
        encoding: "utf8",
        timeout: 3000,
        stdio: ["pipe", "pipe", "pipe"],
      })
      if (unpushed.trim().length > 0) return true
    } catch {
      // no upstream / not a git repo / other — fall through to working-tree check
    }
    try {
      const status = execSync("git status --porcelain", {
        cwd,
        encoding: "utf8",
        timeout: 3000,
        stdio: ["pipe", "pipe", "pipe"],
      })
      if (status.trim().length > 0) return true
    } catch {
      // not a git repo or git unavailable — fail open
    }
    return false
  } catch {
    return false
  }
}

// ============================================================================
// CI-VERDICT QUERY via make (Deficiency A+B)
// Shells out to `make ci-verdict BRANCH=master`, parses the CI_RED_PATTERNS
// output for GREEN/RED/PENDING, caches result for 60s. Treats anything other
// than GREEN (failure, pending, in_progress, queued, cancelled) as pending work.
// FAIL-OPEN on any error — a missing gh/network returns false (don't block).
// ============================================================================
function ciIsPendingOrRed(): boolean {
  const now = Date.now()
  if (ciVerdictCache && (now - ciVerdictCache.ts) < 60_000) {
    return ciVerdictCache.isPendingOrRed
  }
  try {
    const { execSync } = require("node:child_process")
    const cwd = process.cwd()
    try {
      const output = execSync("make ci-verdict BRANCH=master", {
        cwd,
        encoding: "utf8",
        timeout: 15000,
        stdio: ["pipe", "pipe", "pipe"],
      }).trim()
      const isGreen = /^CI GREEN:/m.test(output) && !/STALE RUN WARNING/i.test(output)
      ciVerdictCache = { ts: now, isPendingOrRed: !isGreen }
      return !isGreen
    } catch (e: any) {
      // execSync throws on non-zero exit (RED exit 1, PENDING exit 2).
      // The stdout is captured on the error object.
      const output = (e?.stdout || e?.stderr || "").trim()
      if (output) {
        const isGreen = /^CI GREEN:/m.test(output) && !/STALE RUN WARNING/i.test(output)
        ciVerdictCache = { ts: now, isPendingOrRed: !isGreen }
        return !isGreen
      }
      // No output captured — treat as pending (fail-safe: assume non-green)
      ciVerdictCache = { ts: now, isPendingOrRed: true }
      return true
    }
  } catch {
    ciVerdictCache = null
    return false  // fail open
  }
}

function tasksMdHasUnchecked(): boolean {
  try {
    const tasksPath = path.join(process.cwd(), "TASKS.md")
    if (!fs.existsSync(tasksPath)) return false
    const content = fs.readFileSync(tasksPath, "utf8")
    // Unchecked markdown checkbox: `- [ ]` or `* [ ]` (the box is empty,
    // i.e. NOT `- [x]` or `- [X]`). A single unchecked item means the
    // project has known-unfinished work the agent acknowledged but did not
    // complete.
    return /-\s+\[\s*\]/.test(content) || /\*\s+\[\s*\][^xX]/i.test(content) || /\*\s+\[\s*\]/i.test(content)
  } catch {
    return false
  }
}

function ratchetHasEntries(): number {
  try {
    const ratchetPath = path.join(process.cwd(), "config", "ratchet.yml")
    if (!fs.existsSync(ratchetPath)) return 0
    const content = fs.readFileSync(ratchetPath, "utf8")
    const entries = content.split("\n").filter(
      l => l.trim() && !l.trim().startsWith("#") && l.includes(":")
    )
    return entries.length
  } catch {
    return 0
  }
}

// ============================================================================
// GATE-STATUS + CI-RED DETECTOR (BUGS.md structural fix #3)
// "Make the gate-status / CI integration visible to the stop detector: if CI
// is RED, a 'done' response should ALWAYS be blocked regardless of phrasing."
//
// Two signals: (a) the .gate-status file (written by `make gate`) has FAIL
// lines — the project's own local gate knows it is not green; (b) the agent's
// outgoing response text mentions CI being red/failing — the agent is aware
// CI is broken and is trying to stop anyway. Either signal: the response must
// NOT be treated as a legitimate completion.
//
// FAIL-OPEN on any error — a missing gate-status file is not a blocker.
// ============================================================================
function gateStatusIsRed(): boolean {
  try {
    const gatePath = path.join(process.cwd(), ".gate-status")
    if (!fs.existsSync(gatePath)) return false
    const content = fs.readFileSync(gatePath, "utf8")
    const lines = content.split("\n")
    for (const line of lines) {
      // Skip the header line (starts with ===)
      if (line.startsWith("===")) continue
      if (/FAIL/.test(line)) return true
    }
    return false
  } catch {
    return false  // fail open
  }
}

const CI_RED_PATTERNS: RegExp[] = [
  /\bCI\s+is\s+(?:red|failing|broken|not green|down)\b/i,
  /\bCI\s+(?:run|job|pipeline|workflow)\s+(?:failed|is red|is failing)\b/i,
  /\bGitHub\s+Actions?\s+(?:is\s+)?(?:red|failing|failed)\b/i,
  /\bbuild\s+(?:is\s+)?(?:red|failing|failed)\b.{0,30}?\b(?:CI|pipeline|actions?)\b/i,
  /\b(?:gate|sandboxcom|Actions)\s+(?:is\s+)?(?:still\s+)?(?:red|failing|not green)\b/i,
  /\bCI\s+(?:still|remains?)\s+(?:red|failing)\b/i,
  /\b(?:release|build.and.release)\s+(?:job\s+)?(?:failed|is red)\b/i,
  /\b(?:ci|pipeline)\s+is\s+not\s+(?:green|passing)\b/i,
  /\b(?:ci|pipeline)\s+(?:never|hasn'?t)\s+(?:run|passed|gone)\s+green\b/i,
]

function responseMentionsCiRed(text: string): boolean {
  return CI_RED_PATTERNS.some(p => p.test(text))
}

// ============================================================================
// CONSTRAINT-AS-STOP (self-heal guardrail — 2026-06-23)
// A dedicated pattern group for the "naked constraint" stop pattern: the agent
// hits a limitation and tells the user "restart opencode", "we have to wait",
// "can't be done without X", etc. — parking the problem instead of engineering
// a workaround. The incident (2026-06-23): the agent responded "restart
// opencode one more time" to a recoverable state instead of fixing it.
//
// Policy: AGENTS.md "Constraints Are To Engineer Around" — a constraint is a
// design prompt, NEVER a terminal answer. Detection here triggers a distinct
// directive injection (constraintBlockResponse) that tells the agent to
// engineer a workaround NOW or dispatch a research task, NOT park it on the
// user.
//
// These patterns are ADDITIVE to NO_WAIT_PATTERNS (which already has a first
// generation of constraint phrases). This group captures the specific
// restart/defer phrasings the incident surfaced. Overlap is intentional and
// harmless — detection short-circuits on first match.
// ============================================================================
const CONSTRAINT_AS_STOP_PATTERNS: RegExp[] = [
  // Telling the user to restart the tool/session as a "fix"
  /\brestart (?:opencode|required|needed)\b/i,
  // "can't / cannot / not possible" + "without / unless" — naked precondition
  /\b(?:can'?t|cannot|not possible) (?:without|unless)\b/i,
  // Generic wait-deferral without a paired action
  /\bhave to wait\b/i,
  // "(limitation|constraint) of <tool>" — attributing a dead-end to the environment
  /\b(?:limitation|constraint) of\b/i,
  // "(no way|there's no way) to" — explicit dead-end framing
  /\b(?:no way|there'?s no way) to\b/i,
  // "isn't possible" — naked impossibility claim
  /\bisn'?t possible\b/i,
  // "we need/must (to) restart/wait/stop" — collective deferred-action framing.
  // "to" is optional: "we must wait" and "we need to wait" are both constraints.
  /\bwe (?:need|must)(?: to)? (?:restart|wait|stop)\b/i,
  // PASSIVE-WAIT patterns (2026-06-24 incident): agent declares it will wait
  // for an external event instead of actively working. The agent said "I'm
  // monitoring CI and will cut the release the moment it goes green. Nothing
  // else can be committed meanwhile." — a textbook passive-wait stop that none
  // of the above patterns caught.
  /\bi('?m| am) monitoring\b/i,                    // "I'm monitoring"
  /\bmonitoring (?:ci|the (?:ci|pipeline|status))\b/i, // "monitoring CI/pipeline"
  /\bwill .{0,20}?\b(?:wait|monitor|check back|hold|stand by)\b.{0,20}?\b(?:when|once|until|the moment|as soon as)\b/i, // "will wait/monitor X when/once/until Y"
  /\bnothing else can be\b/i,                      // "nothing else can be committed/done"
  /\bi'?ll (?:wait|check back|poll)\b/i,           // "I'll wait/check back/poll"
  /\bwaiting for (?:ci|green|the pipeline)\b/i,    // "waiting for CI"
  /\bstill (?:waiting|pending)\b/i,                // "still waiting/pending"
  /\bblocked on\b/i,                               // "blocked on CI/gate"
]

function detectConstraintAsStop(text: string): boolean {
  return CONSTRAINT_AS_STOP_PATTERNS.some(p => p.test(text))
}

function constraintBlockResponse(): string {
  return [
    "⛔ CONSTRAINT / PASSIVE-WAIT DETECTED: your final message frames a constraint",
    "(restart, wait, can't-be-done, limitation, no-way) OR declares passive waiting",
    "(monitoring, will-do-X-when-Y, nothing-else-can-be-done, blocked-on). Policy",
    "(AGENTS.md 'Constraints Are To Engineer Around' + 'No Unseen Events'): a",
    "constraint is a design prompt, NEVER a dead end. Passive waiting is NOT work.",
    "",
    "Engineer a workaround NOW or dispatch a research task to find one.",
    "Do NOT park this on the user. Do NOT instruct the user to restart / wait /",
    "reconfigure. Do NOT declare 'I'm monitoring' or 'will do X when Y' — that is",
    "a STOP disguised as activity. If CI is pending, DISPATCH a task to investigate",
    "failures, burn ratchet entries, fix D-backlog items, or do ANY other useful",
    "work in parallel. The wait itself is the bug.",
    "",
    "Examples of correct responses: shard the job + poll annotations; add the",
    "missing make target; back off and retry; run the slow test in CI instead",
    "of locally; dispatch a subagent to fix an unrelated issue while waiting.",
    "Pick one and EXECUTE.",
  ].join("\n")
}

function noWaitBlockResponse(): string {
  return [
    "⛔ STOP BLOCKED (no-wait): your final message DEFERS to the user / waits",
    "for go-ahead / offers to proceed 'if they want' instead of just doing it.",
    "Policy: NEVER wait on the user. Take the default/recommended action NOW,",
    "state the assumption in one line, and execute it. The work is reversible",
    "(branches/RC, not master) — proceed. Only a genuinely IRREVERSIBLE",
    "+destructive action needs consent, raised explicitly, not a prose sign-off.",
    "",
    "BLOCKING by default (2026-06-22) — make advisory via GLUDD_NO_WAIT_ENFORCE=0.",
  ].join("\n")
}

// ============================================================================
// MULTITASKING BACKLOG (port of multitasking_backlog_stop.sh)
// Blocks turn-end while scripts/multitasking_backlog.json has items not marked
// done-with-evidence. The checker (scripts/multitasking_backlog_check.py) is
// the source of truth — we shell out to it (same as the claude hook) so the
// two layers never disagree on "is the backlog done?".
// Returns: null = allow, string = replacement response (when backlog open).
// ============================================================================
function multitaskingBacklogOpen(): { open: string[] } | null {
  try {
    const { spawnSync } = require("node:child_process")
    const repoDir = process.cwd()
    const checker = path.join(repoDir, "scripts", "multitasking_backlog_check.py")
    if (!fs.existsSync(checker)) return null  // script not present -> fail open

    // --assert-done: exit 0 = all done, 1 = work remains, 2 = file error
    const result = spawnSync("python3", [checker, "--assert-done"], {
      cwd: repoDir,
      encoding: "utf8",
      timeout: 5000,
    })
    if (result.status === 0) return { open: [] }  // all done
    if (result.status === 2) return null  // file error -> fail open
    // status === 1 -> work remains; list it
    const listResult = spawnSync("python3", [checker, "--list-open"], {
      cwd: repoDir,
      encoding: "utf8",
      timeout: 5000,
    })
    const open = (listResult.stdout || "").trim().split("\n").filter(Boolean)
    return { open }
  } catch {
    return null  // fail open
  }
}

function backlogBlockResponse(openItems: string[]): string {
  return [
    "⛔ STOP BLOCKED (multitasking backlog): the tracked multitasking backlog",
    "has items that are not effectively-done (status==done WITH non-empty",
    "evidence). Do NOT stop. Keep an agent assigned to the open/unverified",
    "items until each is genuinely complete with evidence.",
    "",
    "Open items: " + openItems.join("; "),
  ].join("\n")
}

// ============================================================================
// SESSION-START ORCHESTRATE (port of session_start_orchestrate.sh)
// Injects orchestration context on every session (re)start. In opencode we
// hook system.transform (which fires per-conversation, not per-turn) — the
// context is appended to the system prompt, where the model reads it once at
// session start and acts on it.
//
// NOTE (2026-06-28): the agent-floor + dispatch-wave directives were the
// SESSION START PROTOCOL banner's domain and are now owned ENTIRELY by
// enforce-session-start.ts. This function retains ONLY the standing policy
// items that enforce-session-start.ts does NOT cover: workflow preference,
// pending-work resumption (unmerged branches + security backlog), and the
// make-only commit rule. Trimmed to avoid dual-emitter confusion.
// ============================================================================
function buildOrchestrationContext(): string {
  return [
    "[orchestration auto-start] Session (re)started. Standing orchestration",
    "policy is ACTIVE (agent-floor + dispatch-wave directives are owned by",
    "the SESSION START PROTOCOL banner in enforce-session-start.ts; the items",
    "below cover the gaps it does not):",
    "1. WORKFLOW: for any multi-step batch (backlog drain, feature build),",
    "   prefer the deterministic workflow tool over manual bursts — it holds",
    "   the pool steady.",
    "2. PENDING WORK: verified-but-unmerged feature branches await a",
    "   consolidated gated merge; the security backlog is in",
    "   docs/audit/NEW_FINDINGS*.md. Resume both.",
    "3. COMMITS are make-only: feature branches use 'make commit-bootstrap'",
    "   (no-gate); never make ship/gate mid-fleet (basetemp stampede).",
  ].join("\n")
}

// PENDING-WORK AUDIT BLOCK — generates detailed audit message showing all
// pending-work state for display in stop-block directive and separately.
function pendingWorkAuditText(cache: StopStateCache): string {
  return [
    "HARD STOP — PENDING-WORK AUDIT:",
    "TASKS.md unchecked: " + (cache.tasksMdUnchecked ? "yes" : "no"),
    "gate-status red: " + (cache.gateStatusRed ? "yes" : "no"),
    "ratchet entries: " + cache.ratchetEntries,
    "repo pending: " + (cache.repoPending ? "yes" : "no"),
  ].join("\n")
}

// ============================================================================
// PLUGIN
// ============================================================================
export default (async ({ }) => {
  return {
    // --- Session idle — reset turn state and cache expensive checks ----------
    // PERSISTENT BLOCK (2026-07-03): turnState.blocked is NOT reset here.
    // If the previous turn was blocked for a stop pattern, the block persists
    // across turns until a tool call clears it. This prevents the pattern where
    // the agent sees the block message and then sends ANOTHER text-only response
    // explaining its analysis — every text-only response after a block is also
    // suppressed. The block is cleared in tool.execute.before (any tool call).
    //
    // Uses the `event` + `event.type === "session.idle"` pattern (proven to
    // work in enforce-false-done.ts). The top-level `"session.idle"` key does
    // NOT work (/tmp/gludd-stop-state.json was never written under that key).
    event: async ({ event }: { event: { type: string } }) => {
      if (event.type === "session.idle") {
        try {
          turnState.accumulatedText = ""

          // Warm the CI verdict cache (Deficiency A+B)
          ciIsPendingOrRed()

          const ratchetCount = ratchetHasEntries()
          const tasksMdUnchecked = tasksMdHasUnchecked()
          const gateRed = gateStatusIsRed()
          const repoPending = repoHasPendingWork()
          const ciVerdictPendingOrRed = ciIsPendingOrRed()
          const hasPendingWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || gateRed || ciVerdictPendingOrRed

          const backlog = multitaskingBacklogOpen()

          const state: StopStateCache = {
            ts: Date.now(),
            ratchetEntries: ratchetCount,
            tasksMdUnchecked,
            gateStatusRed: gateRed,
            repoPending,
            backlogOpen: backlog ? backlog.open.length : 0,
            backlogItems: backlog ? backlog.open : [],
            hasPendingWork,
            ciVerdictPendingOrRed,
          }

          fs.writeFileSync(STATE_FILE, JSON.stringify(state), "utf8")
        } catch {
          // fail open — skip cache write
        }
      }
    },

    // --- Deny the question tool (no-blocking-questions) ----------------------
    "tool.execute.before": async (input, output) => {
      // --- PERSISTENT BLOCK CLEAR (2026-07-03) --------------------------
      // Any tool call clears the cross-turn persistent block. This is the
      // antidote to the text-only-response-after-block pattern: once blocked,
      // the agent MUST make a tool call to resume normal text output.
      // Read-only tools (read, grep, glob) and dispatch tools (task) are
      // included — the point is to force an action, not a specific kind.
      if (turnState.blocked) {
        turnState.blocked = false
      }

      if (input.tool === "question") {
        throw new Error(QUESTION_DENY_REASON)
      }

      // --- STOP-LIKE TOOL BLOCK (BUGS.md structural fix #2 — 2026-06-30) --
      // When the agent tries to commit/push/release ("I'm done") but
      // TASKS.md still has unchecked items or config/ratchet.yml has
      // entries, deny the tool call. This catches the premature-completion
      // pattern that response.transform (text analysis) may miss.
      if (input.tool === "bash") {
        const cmd = (output as Record<string, unknown> | undefined)?.args
        const args = cmd as { command?: string } | undefined
        const command = typeof args?.command === "string" ? args.command.trim() : ""
        if (command.startsWith("make ") && STOP_LIKE_TARGETS_RE.test(command)) {
          let taskMd: boolean
          let ratchetCount: number
          try {
            if (fs.existsSync(STATE_FILE)) {
              const raw = fs.readFileSync(STATE_FILE, "utf8")
              const cache = JSON.parse(raw)
              taskMd = cache.tasksMdUnchecked ?? tasksMdHasUnchecked()
              ratchetCount = cache.ratchetEntries ?? ratchetHasEntries()
            } else {
              taskMd = tasksMdHasUnchecked()
              ratchetCount = ratchetHasEntries()
            }
          } catch {
            taskMd = tasksMdHasUnchecked()
            ratchetCount = ratchetHasEntries()
          }
          if (taskMd || ratchetCount > 0) {
            throw new Error(stopLikeDenyMessage(taskMd, ratchetCount))
          }
        }
      }
    },

    // --- Orchestration context injection (session-start) ---------------------
    "experimental.chat.system.transform": async (_input, output) => {
      try {
        if (typeof output === "string") {
          const ctx = buildOrchestrationContext()
          return ctx + "\n\n" + output
        }
        return output
      } catch {
        return output  // fail open
      }
    },

    // --- Stop-pattern BLOCKING (RESTORED 2026-07-03) ---
    // The 2026-07-03 removal replaced ALL text-blocking with pass-through,
    // claiming mechanical enforcement by enforce-floor/delegate was sufficient.
    // It was NOT — the agent continues to send "done" / "complete" / status
    // summaries while work remains. This handler RESTORES the state-based
    // blocking: when pending work exists AND the text looks terminal, the
    // response text is REPLACED with a block directive. The persistent-block
    // mechanism then suppresses ALL subsequent text responses until a tool
    // call clears it (see tool.execute.before above).
    //
    // State is read from the file written by the event handler (session.idle).
    // If the state file is missing or unreadable, all checks are computed
    // fresh inline so the block can never be bypassed by a missing cache.
    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      try {
        const text = output.text
        if (!text || text.trim().length === 0) return
        if (text.trim().length < 60) return

        turnState.accumulatedText += text

        let cache: StopStateCache | null = null
        try {
          if (fs.existsSync(STATE_FILE)) {
            const raw = fs.readFileSync(STATE_FILE, "utf8")
            cache = JSON.parse(raw)
          }
        } catch {
          // fail open
        }

        if (!cache) {
          const ratchetCount = ratchetHasEntries()
          const tasksMdUnchecked = tasksMdHasUnchecked()
          const gateRed = gateStatusIsRed()
          const repoPending = repoHasPendingWork()
          const ciVerdictPendingOrRed = ciIsPendingOrRed()
          const hasPendingWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || gateRed || ciVerdictPendingOrRed
          cache = {
            ts: Date.now(),
            ratchetEntries: ratchetCount,
            tasksMdUnchecked,
            gateStatusRed: gateRed,
            repoPending,
            backlogOpen: 0,
            backlogItems: [],
            hasPendingWork,
            ciVerdictPendingOrRed,
          }
        }

        const repoPending = cache?.repoPending ?? repoHasPendingWork()
        const ratchetCount = cache.ratchetEntries
        const tasksMdUnchecked = cache?.tasksMdUnchecked ?? tasksMdHasUnchecked()
        const gateRed = cache?.gateStatusRed ?? gateStatusIsRed()
        const ciRed = gateRed || responseMentionsCiRed(turnState.accumulatedText)
        const ciVerdictPendingOrRed = cache?.ciVerdictPendingOrRed ?? ciIsPendingOrRed()
        const hasPendingWork = repoPending || ratchetCount > 0 || tasksMdUnchecked || ciRed || ciVerdictPendingOrRed

        if (turnState.blocked) {
          output.text = ""
          return
        }

        if (hasPendingWork && responseLooksTerminal(turnState.accumulatedText)) {
          output.text = [
            "HARD STOP — STATE-BASED BLOCK: your final response looks terminal",
            "(markdown table / DONE banner / long body / commit hash / Q&A recap /",
            "session summary / item-count / all-checked checkboxes) while the",
            "project state reports known-unfinished work.",
            "",
            "TASKS.md unchecked: " + (cache.tasksMdUnchecked ? "yes" : "no"),
            "gate-status red: " + (gateRed ? "yes" : "no"),
            "CI mentioned in response: yes",
            "CI verdict: pending or red",
            "CI pending or red: " + (ciVerdictPendingOrRed ? "yes" : "no"),
            "ratchet entries remaining: " + cache.ratchetEntries,
            "repo pending (unpushed/uncommitted): " + (cache.repoPending ? "yes" : "no"),
            "",
            "YOU ARE STOPPING PREMATURELY. Dispatch ≥5 subagents to continue",
            "fixing the pending items above. Do NOT rephrase — work.",
          ].join("\n")
          turnState.blocked = true
          return
        }

        if (hasPendingWork) {
          const noWait = detectNoWaitPattern(turnState.accumulatedText)
          const constraint = detectConstraintAsStop(turnState.accumulatedText)
          if (noWait || constraint || responseMentionsCiRed(turnState.accumulatedText)) {
            const auditDetail = pendingWorkAuditText(cache)
            output.text = "HARD STOP — STATE-BASED BLOCK\n\n" + auditDetail + "\n\nSTOP BLOCKED — pending work + deferral/constraint/CI-red detected"
            turnState.blocked = true
            return
          }
        }
      } catch {
        return
      }
    },
  }
}) satisfies Plugin
