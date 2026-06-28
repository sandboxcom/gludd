import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"

// enforce-session-start.ts — guarantees the FIRST actions of every session are:
//   1. LOCATE work: read TASKS.md, BUGS.md, config/ratchet.yml, SESSION.md
//   2. FAN OUT: dispatch >= MIN_DISPATCHES parallel task/agent subagents on
//      disjoint work BEFORE any inline mutation or terminal response.
//
// Prevents two failure modes:
//   (a) "Q&A-style session start" — agent replies with prose instead of
//       resuming work. Fixed by the SESSION START PROTOCOL banner injection.
//   (b) "Grind-inline start" — agent does the work serially on the main
//       thread with 0 subagents live, looking hung. Fixed by the
//       dispatch-count gate.
//
// Three-layer guardrail:
//   - Prompt: AGENTS.md "Session Start Protocol" section.
//   - Plugin: this file (system.transform + tool.execute.before).
//   - Test: tests/unit/test_session_start_protocol.py.
//
// FAIL-OPEN: every hook is wrapped in try/catch that returns the original
// output/undefined. A plugin bug never wedges the session.

// --- Config -----------------------------------------------------------------

// Minimum parallel dispatches before inline mutations are allowed in a fresh
// session. Default 5 (the message-shape wave floor from AGENTS.md). Override
// via GLUDD_SESSION_START_MIN_DISPATCHES. If CLAUDE_AGENT_FLOOR is set, the
// effective min is raised toward it (capped at 10 so a high floor env does
// not make the gate unreachable).
const MIN_DISPATCHES = parseInt(
  process.env.GLUDD_SESSION_START_MIN_DISPATCHES || "5",
  10,
)
const FLOOR = parseInt(process.env.CLAUDE_AGENT_FLOOR || "10", 10)
const EFFECTIVE_MIN = Math.max(MIN_DISPATCHES, Math.min(FLOOR, 10))

// Hard-deny mode (mirrors GLUDD_FLOOR_ENFORCE / GLUDD_NO_WAIT_ENFORCE).
// Default is ON (hard deny on premature mutations). Set
// GLUDD_SESSION_START_ENFORCE=0 to fall back to advisory (directive-only)
// mode — useful for Q&A-only sessions.
const ENFORCE = process.env.GLUDD_SESSION_START_ENFORCE !== "0"

// Per-cwd state file. Overridable for tests.
const STATE_FILE =
  process.env.GLUDD_SESSION_STATE || "/tmp/gludd-session-start.json"

// A session is "fresh" for this many seconds after the first tool call. After
// that the gate turns off (the agent may have legitimately onboarded).
const FRESH_SECS = parseInt(
  process.env.GLUDD_SESSION_START_FRESH_SECS || "600",
  10,
)

const TASK_FILES = ["TASKS.md", "BUGS.md", "config/ratchet.yml", "SESSION.md"]

// --- System prompt banner ---------------------------------------------------

const SESSION_START_DIRECTIVE = [
  "================ SESSION START PROTOCOL ================",
  "The FIRST actions of this session, in strict order:",
  "  STEP 1 — LOCATE work: in ONE tool-call message, read TASKS.md,",
  "           BUGS.md, config/ratchet.yml, SESSION.md. Never serial.",
  `  STEP 2 — FAN OUT: dispatch >= ${EFFECTIVE_MIN} parallel task/agent`,
  "           subagents on disjoint work BEFORE any inline mutation, status",
  "           report, or terminal response. Reads are allowed; serial work",
  "           is not.",
  "DO NOT WRITE any prose between session start and the first dispatch wave.",
  "Do not answer the user's prompt first and dispatch second — dispatch first.",
  "No prose, no summaries, no status reports, no planning before the wave.",
  "Why: a session that boots and then grinds inline (0 subagents live) looks",
  "hung to the user. The fix is structural — locate work, then fan out.",
  "Enforced by .opencode/plugin/enforce-session-start.ts (hard-deny by",
  "default). Set GLUDD_SESSION_START_ENFORCE=0 for advisory mode.",
  "========================================================",
].join("\n")

// --- State helpers ----------------------------------------------------------

interface SessionState {
  started_at: number
  readsDone: boolean
  dispatches: number
}

function loadState(): SessionState {
  try {
    if (!fs.existsSync(STATE_FILE)) {
      const initial: SessionState = {
        started_at: Date.now(),
        readsDone: false,
        dispatches: 0,
      }
      fs.writeFileSync(STATE_FILE, JSON.stringify(initial))
      return initial
    }
    const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
    return {
      started_at: Number(raw.started_at) || Date.now(),
      readsDone: Boolean(raw.readsDone),
      dispatches: Number(raw.dispatches) || 0,
    }
  } catch {
    return { started_at: Date.now(), readsDone: false, dispatches: 0 }
  }
}

function saveState(state: SessionState): void {
  try {
    fs.writeFileSync(STATE_FILE, JSON.stringify(state))
  } catch {
    // fail open
  }
}

function sessionIsFresh(s: SessionState): boolean {
  return (Date.now() - s.started_at) / 1000 < FRESH_SECS
}

// --- Tool classification ----------------------------------------------------

function isDispatchTool(tool: string): boolean {
  return tool === "task" || tool === "agent" || tool === "workflow"
}

function isReadTool(tool: string): boolean {
  return tool === "read" || tool === "glob" || tool === "grep"
}

function isTaskFileRead(tool: string, input: unknown): boolean {
  if (!isReadTool(tool)) return false
  try {
    const blob = JSON.stringify(input ?? {}).toLowerCase()
    return TASK_FILES.some(f => blob.includes(f.toLowerCase()))
  } catch {
    return false
  }
}

// --- Plugin -----------------------------------------------------------------

export default (async () => {
  return {
    // Inject the SESSION START PROTOCOL banner at the top of the system prompt.
    "experimental.chat.system.transform": async (
      _input: unknown,
      output: unknown,
    ) => {
      try {
        // Initialize per-session state so the tool.execute.before gate knows
        // this is a fresh session.
        loadState()
        if (typeof output === "string") {
          return SESSION_START_DIRECTIVE + "\n\n" + output
        }
        return output
      } catch {
        return output
      }
    },

    // Track reads + dispatches; gate premature mutations in a fresh session.
    "tool.execute.before": async (
      input: { tool?: string } & Record<string, unknown>,
      _output: unknown,
    ) => {
      let denyMessage: string | null = null
      try {
        const tool = String((input as { tool?: string }).tool ?? "")
        const state = loadState()

        // Count + record dispatches — they are what we want more of.
        if (isDispatchTool(tool)) {
          state.dispatches += 1
          saveState(state)
          return
        }

        // Reads of task-tracking files mark the "located work" flag.
        if (isTaskFileRead(tool, input)) {
          if (!state.readsDone) {
            state.readsDone = true
            saveState(state)
          }
          return
        }

        // Other reads are always allowed (the protocol WANTS investigation).
        if (isReadTool(tool)) return

        // For any other tool (edit/write/bash/etc.) in a fresh, unprimed
        // session: emit a loud reminder, and in ENFORCE mode hard-deny.
        const primed = state.readsDone && state.dispatches >= EFFECTIVE_MIN
        if (sessionIsFresh(state) && !primed) {
          const msg = [
            `[SESSION START PROTOCOL] readsDone=${state.readsDone},`,
            `${state.dispatches}/${EFFECTIVE_MIN} dispatches so far.`,
            `Locate work (TASKS.md, BUGS.md, config/ratchet.yml, SESSION.md)`,
            `then dispatch >= ${EFFECTIVE_MIN} parallel task/agent subagents`,
            `BEFORE inline mutations. Reads and dispatches are allowed; this`,
            `${tool} call is premature.`,
          ].join(" ")
          console.warn(msg)
          if (ENFORCE) {
            denyMessage = (
              "SESSION START PROTOCOL: readsDone=" + state.readsDone +
              ", " + state.dispatches + "/" + EFFECTIVE_MIN + " dispatches. " +
              "Locate work then dispatch >= " + EFFECTIVE_MIN + " parallel " +
              "subagents before doing inline mutations. Reads and dispatches " +
              "are allowed. Set GLUDD_SESSION_START_ENFORCE=0 to make this " +
              "advisory."
            )
          }
        }
      } catch {
        // fail-open — never wedge the session on a plugin bug
      }
      if (denyMessage) throw new Error(denyMessage)
    },
  }
}) as Plugin
