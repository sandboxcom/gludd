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
const TARGET = parseInt(process.env.CLAUDE_AGENT_TARGET || "14", 10)
const NO_WAIT_ENFORCE = process.env.GLUDD_NO_WAIT_ENFORCE !== "0"  // DEFAULT: blocking (2026-06-22)

// ============================================================================
// HELPERS
// ============================================================================
function countLiveAgents(): number | null {
  try {
    const { execSync } = require("node:child_process")
    const out = execSync(
      "python3 " + path.join(process.cwd(), "scripts", "agent_liveness.py") + " --count",
      {
        timeout: 5000,
        cwd: process.cwd(),
        encoding: "utf8",
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env, FLOOR_PROBE_SECS: "0.6", FLOOR_TAIL_SECS: "12.0" },
      },
    )
    const n = parseInt(String(out).trim(), 10)
    return Number.isNaN(n) ? null : n
  } catch {
    return null
  }
}

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
  return false
}

function detectNoWaitPattern(text: string): boolean {
  return NO_WAIT_PATTERNS.some(p => p.test(text))
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
// Injects orchestration context on every session (re)start so the model
// resumes the agent-floor refill discipline on the first turn. In opencode we
// hook system.transform (which fires per-conversation, not per-turn) — the
// context is appended to the system prompt, where the model reads it once at
// session start and acts on it.
// ============================================================================
function buildOrchestrationContext(): string {
  const live = countLiveAgents() ?? 0
  return [
    "[orchestration auto-start] Session (re)started. Standing orchestration",
    "policy is ACTIVE:",
    `1. AGENT FLOOR: maintain ${FLOOR}-${TARGET} concurrent subagents on disjoint`,
    `   work. Currently ${live} live -- if below ${FLOOR}, dispatch toward`,
    `   ${TARGET} now (read-only proposers / commit-bootstrap writers; worktree`,
    "   only for concurrent mutation).",
    `2. WORKFLOW: for any multi-step batch (backlog drain, feature build),`,
    "   prefer the deterministic workflow tool over manual bursts — it holds",
    "   the pool steady.",
    "3. PENDING WORK: verified-but-unmerged feature branches await a",
    "   consolidated gated merge; the security backlog is in",
    "   docs/audit/NEW_FINDINGS*.md. Resume both.",
    "4. COMMITS are make-only: feature branches use 'make commit-bootstrap'",
    "   (no-gate); never make ship/gate mid-fleet (basetemp stampede).",
    "Act on item 1 before ending the first turn.",
  ].join("\n")
}

// ============================================================================
// PLUGIN
// ============================================================================
export default (async ({ }) => {
  return {
    // --- Deny the question tool (no-blocking-questions) ----------------------
    "tool.execute.before": async (input, _output) => {
      if (input.tool === "question") {
        throw new Error(QUESTION_DENY_REASON)
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

    // --- Stop-pattern enforcement (no-wait + backlog + ratchet + pending-todos) ---
    // Runs in response.transform — examines the OUTGOING assistant message.
    // The DEFAULT is ENFORCING (blocking) as of 2026-06-22. The advisory era
    // (2026-06-21) allowed the agent to stop and ask "which option do you want?"
    // — that was the bug. Set GLUDD_NO_WAIT_ENFORCE=0 to make it advisory again.
    "experimental.chat.response.transform": async (_input, output) => {
      try {
        if (typeof output !== "string") return output

        // --- STATE-BASED CHECK (BUGS.md audit #1): if ratchet has entries AND
        // response looks terminal, block. This is the Whac-A-Mole fix: instead
        // of matching specific phrases, ask 'does the repo have pending work?'
        // AND 'does this response look like a finale?' If both are true, the
        // response is BLOCKED regardless of wording. ---
        const ratchetPath = path.join(process.cwd(), "config", "ratchet.yml")
        if (fs.existsSync(ratchetPath)) {
          const ratchetContent = fs.readFileSync(ratchetPath, "utf8")
          const ratchetEntries = ratchetContent.split("\n").filter(l => l.trim() && !l.trim().startsWith("#") && l.includes(":"))
          if (ratchetEntries.length > 0 && responseLooksTerminal(output)) {
            return [
              "⛔ STATE-BASED STOP BLOCKED: config/ratchet.yml has",
              `${ratchetEntries.length} entries AND your response looks like a completion`,
              "report (table, hash, DONE/COMPLETE). This is the Whac-A-Mole fix: instead",
              "of matching specific phrases, this check asks 'does the repo have pending",
              "work?' AND 'does this response look like a finale?' If both are true, the",
              "response is BLOCKED regardless of wording.",
              "",
              "MAKE A TOOL CALL. Do NOT end your turn with a status report.",
            ].join("\n")
          }
        }

        // --- RATCHET CHECK (vocabulary-based): if config/ratchet.yml has entries,
        // the project has
        // known-unfixed work. Any response that sounds like a completion report
        // or a stop-to-ask is BLOCKED. This is the state-based check that catches
        // stop patterns vocabulary lists miss. ---
        if (fs.existsSync(ratchetPath)) {
          const ratchetContent = fs.readFileSync(ratchetPath, "utf8")
          const ratchetEntries = ratchetContent.split("\n").filter(
            l => l.trim() && !l.trim().startsWith("#") && l.includes(":")
          )
          if (ratchetEntries.length > 0) {
            const lower = output.toLowerCase()
            const soundsLikeStop = (
              lower.includes("which do you want") ||
              lower.includes("which would you") ||
              lower.includes("which option") ||
              lower.includes("what would you like") ||
              lower.includes("do you want me to") ||
              lower.includes("want me to") ||
              lower.includes("should i") ||
              lower.includes("shall i") ||
              lower.includes("let me know") ||
              lower.includes("your call") ||
              lower.includes("up to you") ||
              lower.includes("prefer") ||
              lower.includes("options:") ||
              lower.includes("option 1") ||
              (lower.includes("options") && lower.includes("?"))
            )
            if (soundsLikeStop) {
              return [
                "⛔ STOP BLOCKED (pending-work audit): config/ratchet.yml has",
                `${ratchetEntries.length} known-failure entries. The project has`,
                "unfixed work. Your response presents options / asks permission",
                "instead of DOING THE WORK. Per the Never-Block-on-Questions",
                "policy: pick the highest-probability-success option, state the",
                "assumption in one line, and EXECUTE. Do NOT present options and",
                "ask which one the user wants. That is the stop-and-ask bug.",
                "",
                "DEFAULT TO ACTION NOW.",
              ].join("\n")
            }
          }
        }

        // Constraint-as-stop (self-heal — 2026-06-23): same enforcement gate as
        // no-wait. Runs BEFORE the no-wait check so the constraint-specific
        // directive wins for overlapping phrases ("isn't possible", "no way to",
        // "have to wait"). When detected, inject the workaround directive so the
        // agent engineers a solution instead of parking it.
        if (NO_WAIT_ENFORCE && detectConstraintAsStop(output)) {
          return constraintBlockResponse()
        }

        // No-wait stop pattern (now ENFORCING by default — 2026-06-22)
        if (NO_WAIT_ENFORCE && detectNoWaitPattern(output)) {
          return noWaitBlockResponse()
        }

        // Multitasking backlog (always enforces — it's a standing user directive
        // that the backlog is worked until done-with-evidence).
        const backlog = multitaskingBacklogOpen()
        if (backlog && backlog.open.length > 0) {
          return backlogBlockResponse(backlog.open)
        }

        return output
      } catch {
        return output  // fail open
      }
    },
  }
}) satisfies Plugin
