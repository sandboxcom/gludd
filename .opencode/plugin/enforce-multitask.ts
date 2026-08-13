// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import {
  CONSECUTIVE_NON_DISPATCH_THRESHOLD,
  CONSECUTIVE_NON_DISPATCH_WINDOW_MS,
  HARD_MAX_DISPATCHES,
  MAX_DISPATCHES,
  MAX_ZERO_STREAK,
  MIN_DISPATCHES,
  MSG_GAP_MS,
  MULTITASK_STATE_FILE,
} from "../lib/multitask_config.ts"
// Source-level state contract: the shared resolver defaults to
// /tmp/gludd-multitask-state.json and honors GLUDD_MULTITASK_STATE_FILE.
// Keep the resolver centralized in multitask_config.ts so the plugin and its
// runtime tests cannot drift onto different state files.
import {
  isSubagent,
  reportAlive,
  isDispatchTool,
  isReadTool,
  isDisengaged,
  readJsonFile,
  writeJsonFile,
  getProjectRoot,
  hasTasksMdPendingWork,
  isStateFileMtimeStale,
  isInPressureRelease,
  isInInlineRecovery,
  getPressureReleaseFloor,
  decrementPressureReleaseTurns,
  recordEmptyDispatch,
  recordSuccessfulDispatch,
  readDispatchOutcomes,
  writeDispatchOutcomes,
} from "../lib/shared.ts"
const MULTITASK_DISPATCH_COUNT_FILE = process.env.GLUDD_MULTITASK_DISPATCH_COUNT_FILE ||
  (process.env.GLUDD_MULTITASK_STATE_FILE
    ? `${process.env.GLUDD_MULTITASK_STATE_FILE}.dispatch-count`
    : "/tmp/gludd-multitask-dispatch-count.json")

interface DispatchCountFile {
  count: number
  ts: number
}

function readAndClearDispatchCountFile(): number {
  const data = readJsonFile<DispatchCountFile>(MULTITASK_DISPATCH_COUNT_FILE, { count: 0, ts: 0 })
  writeJsonFile(MULTITASK_DISPATCH_COUNT_FILE, { count: 0, ts: Date.now() })
  return typeof data.count === "number" ? data.count : 0
}

function incrementDispatchCountFile(): void {
  const data = readJsonFile<DispatchCountFile>(MULTITASK_DISPATCH_COUNT_FILE, { count: 0, ts: 0 })
  writeJsonFile(MULTITASK_DISPATCH_COUNT_FILE, { count: data.count + 1, ts: Date.now() })
}

const FLOOR_ENFORCE = process.env.GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"
const DIVERSITY_ENFORCE = process.env.GLUDD_MULTITASK_DIVERSITY_ENFORCE !== "0"
const HAS_CONFIGURED_MIN_DISPATCHES =
  process.env.GLUDD_MIN_DISPATCHES !== undefined ||
  process.env.GLUDD_MULTITASK_MIN_DISPATCHES !== undefined
// MIN_DISPATCHES is resolved by multitask_config.ts with a recommended
// default of 10. A mandatory minimum is active only when an environment
// variable explicitly opts in; ten remains the hard ceiling, and zero disables.
const REQUIRED_DISPATCHES = HAS_CONFIGURED_MIN_DISPATCHES
  ? Math.max(0, Math.min(MAX_DISPATCHES, Number.isFinite(MIN_DISPATCHES) ? MIN_DISPATCHES : 0))
  : 0
const WAVE_HISTORY_SIZE = 10
const DIVERSITY_THRESHOLD = 0.8
const TOPIC_CLUSTERS: Record<string, string[]> = {
  "guardrails/enforcement": ["guardrail", "enforcement", "plugin", "hook", "enforce", "multitask", "delegate", "session-start", "stop"],
  "testing/tdd": ["test", "tdd", "pytest", "coverage", "test file", "write test", "add test", "test-unit", "test-integration", "test-e2e"],
  "ci/release/pipeline": ["ci", "pipeline", "release", "deploy", "build", "push", "batch-push", "ship-commit", "tag"],
  "type system": ["type", "mypy", "typecheck", "annotation", "any ", "typing", "type-safety", "check-types"],
  "security": ["security", "secrets", "sast", "secret", "vulnerability", "bandit", "sbom", "pip-audit"],
  "docs": ["docs", "readme", "documentation", "changelog", "session.md"],
  "git/infrastructure": ["git", "commit", "makefile", "branch", "merge", "worktree", "make target"],
  "config/setup": ["config", "setup", "init", "bootstrap", "install", "sync"],
  "code quality": ["refactor", "lint", "ruff", "dead code", "unused", "clean", "format", "suppression"],
  "feature/implementation": ["feature", "implement", "add feature", "new", "create", "module", "role", "playbook", "ansible"],
}
function extractTopicPrompt(args: any): string {
  if (!args) return ""
  if (typeof args.prompt === "string") return args.prompt
  if (typeof args.description === "string") return args.description
  if (typeof args.message === "string") return args.message
  try { return JSON.stringify(args).substring(0, 500) } catch { return "" }
}
function classifyTopic(prompt: string): string {
  const lower = prompt.toLowerCase()
  for (const [cluster, keywords] of Object.entries(TOPIC_CLUSTERS)) {
    for (const kw of keywords) {
      if (lower.includes(kw)) return cluster
    }
  }
  return "uncategorized"
}
function countInProgressItems(): number {
  try {
    const root = getProjectRoot()
    const tasksPath = path.join(root, "TASKS.md")
    if (!fs.existsSync(tasksPath)) return 0
    const content = fs.readFileSync(tasksPath, "utf8")
    const matches = content.match(/^-\s*\[ \]/gm)
    return matches ? matches.length : 0
  } catch {
    return 0
  }
}
interface MultitaskState {
  pid: number
  thisMessageDispatches: number
  prevMessageDispatches: number
  zeroStreak: number
  estimatedInFlight: number
  lastTs: number
  lastToolCallTs: number
  waveHistory: number[]
  consecutiveNonDispatch: number
  consecutiveNonDispatchStartTs: number
  sawNonDispatchSinceDispatch: boolean
  underFloorCount: number
  lastDispatchTs: number
  singleDispatchWaves: number
  sessionDispatchTotal: number
  waveTopicCounts: Record<string, number>
}
function freshState(): MultitaskState {
  return {
    pid: process.pid,
    thisMessageDispatches: 0,
    prevMessageDispatches: 0,
    zeroStreak: 0,
    estimatedInFlight: 0,
    lastTs: 0,
    lastToolCallTs: 0,
    waveHistory: [],
    consecutiveNonDispatch: 0,
    consecutiveNonDispatchStartTs: 0,
    sawNonDispatchSinceDispatch: false,
    underFloorCount: 0,
    lastDispatchTs: 0,
    singleDispatchWaves: 0,
    sessionDispatchTotal: 0,
    waveTopicCounts: {},
  }
}
function readState(): MultitaskState {
  if (isStateFileMtimeStale(MULTITASK_STATE_FILE)) {
    return freshState()
  }
  return readJsonFile<MultitaskState>(MULTITASK_STATE_FILE, freshState())
}
function writeState(s: MultitaskState): void {
  s.lastTs = Date.now()
  writeJsonFile(MULTITASK_STATE_FILE, s)
}
function hasPendingWork(): boolean {
  try {
    const root = getProjectRoot()
    const now = Date.now()

    const tasksPath = path.join(root, "TASKS.md")
    if (hasTasksMdPendingWork(tasksPath)) return true

    const ratchetPath = path.join(root, "config", "ratchet.yml")
    if (fs.existsSync(ratchetPath)) {
      const content = fs.readFileSync(ratchetPath, "utf8")
      const entries = content.split("\n").filter(
        l => l.trim() && !l.trim().startsWith("#") && (l.includes("::") || /^\w[\w\s]*:\s/.test(l))
      ).length
      if (entries > 0) return true
    }

    const bugsPath = path.join(root, "BUGS.md")
    if (fs.existsSync(bugsPath)) {
      const content = fs.readFileSync(bugsPath, "utf8")
      const hasOpen = content.split("\n").some(
        l => /^###\s+\d{4}-\d{2}-\d{2}\s+[-—]/.test(l) && !l.includes("(resolved)")
      )
      if (hasOpen) return true
    }

    const gatePath = path.join(root, ".gate-status")
    if (fs.existsSync(gatePath)) {
      const content = fs.readFileSync(gatePath, "utf8")
      if (/=== GATE:\s*FAILED/.test(content)) return true
      for (const line of content.split("\n")) {
        if (line.startsWith("===")) continue
        if (/^(lint |typecheck |collect |test |smoke |env-writes |dead-code |hook-runtime |verify-enforcement |coverage-gaps |check-duplicate-targets )/.test(line)) {
          if (/FAIL/.test(line)) return true
        }
      }
    }

    const gateLitePath = path.join(root, ".gate-lite-status")
    if (fs.existsSync(gateLitePath)) {
      const content = fs.readFileSync(gateLitePath, "utf8")
      if (/=== GATE-LITE:\s*FAILED/.test(content)) return true
      for (const line of content.split("\n")) {
        if (line.startsWith("===")) continue
        if (/^(lint |typecheck |collect |test |smoke |env-writes |dead-code |hook-runtime |coverage-gaps |tdd-compliance |plugin-hook-invoke |skills-frontmatter |lint-specs |spec-enforcement-coverage )/.test(line)) {
          if (/FAIL/.test(line)) return true
        }
      }
    }

    const ciCachePath = process.env.GLUDD_CI_CACHE_PATH || "/tmp/gludd-watchdog-ci.json"
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const rawLastCheck: number = ciData.last_ci_check || 0
      const lastCheck: number = rawLastCheck < 1e11 ? rawLastCheck * 1000 : rawLastCheck
      const lastStatus = ciData.last_ci_status || ""
      if (now - lastCheck < 600_000 && lastStatus && lastStatus !== "SUCCESS") return true
    }

    const stopStatePath = process.env.GLUDD_STOP_STATE_PATH || "/tmp/gludd-stop-state.json"
    if (fs.existsSync(stopStatePath)) {
      const state = JSON.parse(fs.readFileSync(stopStatePath, "utf8"))
      if (state.hasPendingWork) return true
    }

    const releasePath = process.env.GLUDD_RELEASE_COMPLETENESS_FILE || "/tmp/gludd-release-completeness.json"
    if (fs.existsSync(releasePath)) {
      const rd = JSON.parse(fs.readFileSync(releasePath, "utf8"))
      if (now - (rd.ts || 0) < 300_000 && rd.incomplete) return true
    }

    try {
      const todowritePath = process.env.GLUDD_TODOWRITE_STATE_PATH || "/tmp/gludd-todowrite-state.json"
      if (fs.existsSync(todowritePath)) {
        const tdData = JSON.parse(fs.readFileSync(todowritePath, "utf8"))
        const items: any[] = Array.isArray(tdData.items) ? tdData.items : []
        if (items.some((it: any) => it && (it.status === "pending" || it.status === "in_progress"))) return true
      }
    } catch {}
  } catch {
    return false
  }
  return false
}
function handleMessageBoundary(s: MultitaskState): void {
  const now = Date.now()
  // Idempotency guard: prevent double-processing within 500ms. When
  // text.complete calls the boundary handler first (canonical signal),
  // the heuristic detection in tool.execute.before may fire again on
  // the same boundary within the same process. Without this guard,
  // zeroStreak double-increments and waveHistory gets duplicate entries.
  const lastB = (s as any)._lastBoundaryTs
  if (lastB && now - lastB < 500) return
  (s as any)._lastBoundaryTs = now
  s.prevMessageDispatches = s.thisMessageDispatches
  // MT.2: single-dispatch wave escalation — 3 consecutive 1-dispatch waves triggers escalation.
  // Do NOT increment on 0-dispatch waves — that is the zero-streak violation.
  if (s.prevMessageDispatches === 1) { s.singleDispatchWaves++ } else if (s.prevMessageDispatches >= 2) { s.singleDispatchWaves = 0 }
  if (s.thisMessageDispatches === 0) {
    s.zeroStreak++
  } else {
    s.zeroStreak = 0
  }
  s.waveHistory.push(s.prevMessageDispatches)
  if (s.waveHistory.length > WAVE_HISTORY_SIZE) {
    s.waveHistory = s.waveHistory.slice(-WAVE_HISTORY_SIZE)
  }
  // REQUIRED_DISPATCHES is active only for an explicitly configured minimum.
  // Ten remains the recommendation and hard ceiling; explicit zero disables.
  if (REQUIRED_DISPATCHES > 0 && s.prevMessageDispatches < REQUIRED_DISPATCHES) {
    s.underFloorCount++
  } else {
    s.underFloorCount = 0
  }
  s.thisMessageDispatches = 0
  s.waveTopicCounts = {}
}
let _state: MultitaskState = (() => {
  const s = readState()
  s.pid = process.pid
  s.zeroStreak = 0
  s.thisMessageDispatches = 0
  s.prevMessageDispatches = 0
  s.estimatedInFlight = 0
  s.lastToolCallTs = 0
  s.consecutiveNonDispatch = 0
  s.consecutiveNonDispatchStartTs = 0
  s.sawNonDispatchSinceDispatch = false
  s.underFloorCount = 0
  s.singleDispatchWaves = 0
  s.lastDispatchTs = 0
  s.waveTopicCounts = {}
  // Preserve sessionDispatchTotal across restarts so the cumulative counter survives
  writeState(s)
  return s
})()
// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// Module-private because named exports crash OpenCode's legacy plugin loader.
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: { tool?: string }) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return
    reportAlive("enforce-multitask")
    // PID-based staleness detection: if the in-memory state was initialized by
    // a different process (prior session / crashed plugin), reset it. This
    // prevents stale thisMessageDispatches from bypassing the under-floor block.
    if (_state.pid !== process.pid) {
      _state = freshState()
    }
    // Fail-open: an enforcement implementation error must not break editor execution.
    try {
      const tool = (input?.tool ?? "") as string
      const lt = tool.toLowerCase()
      const now = Date.now()
      // Computed once; the lowercase `disengaged` variable is referenced by the
      // grinding / zero-streak / under-floor gates below so each block is
      // trivially auditable for the escape hatch.
      const disengaged = isDisengaged()
      // --- Message boundary detection: multi-signal ---
      // Signal 0 (canonical): text.complete hook calls handleMessageBoundary
      // at message end. The 500ms idempotency guard in handleMessageBoundary
      // prevents double-processing if the heuristic signals below also fire.
      // Signal 1: time gap > MSG_GAP_MS since last tool call
      let boundaryDetected = false
      if (_state.lastToolCallTs > 0 && (now - _state.lastToolCallTs) > MSG_GAP_MS) {
        boundaryDetected = true
      }
      // Signal 2: first dispatch after any non-dispatch tool call (pattern change)
      if (!boundaryDetected && isDispatchTool(tool) && _state.sawNonDispatchSinceDispatch) {
        boundaryDetected = true
      }
      // Signal 3: high-water-mark safety — counter inflated beyond sane bounds
      if (!boundaryDetected && _state.thisMessageDispatches > MAX_DISPATCHES * 3) {
        boundaryDetected = true
      }
      if (boundaryDetected) {
        handleMessageBoundary(_state)
        _state.sawNonDispatchSinceDispatch = false
      }
      _state.lastToolCallTs = now
      // --- Non-dispatch tools: mark that we've seen non-dispatch activity ---
      if (!isDispatchTool(tool)) {
        _state.sawNonDispatchSinceDispatch = true
      }
      // --- Dispatch tools: count and allow (with ceiling) ---
      if (isDispatchTool(tool)) {
        // Reset the consecutive-non-dispatch streak FIRST, before the ceiling
        // check, so the reset is unconditionally inside the dispatch branch.
        _state.consecutiveNonDispatch = 0
        _state.consecutiveNonDispatchStartTs = 0
        if (_state.thisMessageDispatches >= MAX_DISPATCHES) {
          writeState(_state)
          return {
            permissionDecision: "deny" as const,
            message: [
              "DISPATCH CEILING BREACH: already " + String(_state.thisMessageDispatches) + " dispatch(es) in this message.",
              "Maximum allowed per wave: " + String(MAX_DISPATCHES) +
                " (absolute project ceiling: " + String(HARD_MAX_DISPATCHES) + ").",
              "Wait for an in-flight agent or continue appropriate inline work.",
            ].join("\n"),
          }
        }
        _state.thisMessageDispatches++
        _state.sessionDispatchTotal++
        incrementDispatchCountFile()
        _state.estimatedInFlight++
        _state.lastDispatchTs = now
        // --- Topic diversity check ---
        // When >=2 in_progress items exist in TASKS.md and >=80% of dispatches
        // in the current wave share one topic cluster, deny with guidance to
        // add continuation slots for existing work.
        if (DIVERSITY_ENFORCE && !disengaged) {
          const prompt = extractTopicPrompt((input as any).args)
          if (prompt) {
            const topic = classifyTopic(prompt)
            _state.waveTopicCounts[topic] = (_state.waveTopicCounts[topic] || 0) + 1
            const waveSize = _state.thisMessageDispatches
            if (waveSize >= 2) {
              const maxCount = Math.max(...Object.values(_state.waveTopicCounts))
              const share = maxCount / waveSize
              if (share >= DIVERSITY_THRESHOLD) {
                const inProgressCount = countInProgressItems()
                if (inProgressCount >= 2) {
                  const dominantTopic = Object.entries(_state.waveTopicCounts)
                    .sort((a, b) => b[1] - a[1])[0][0]
                  writeState(_state)
                  return {
                    permissionDecision: "deny" as const,
                    message: [
                      "TOPIC DIVERSITY VIOLATION: " + String((share * 100).toFixed(0)) + "% of dispatches (" + String(maxCount) + "/" + String(waveSize) + ") are topic '" + dominantTopic + "'.",
                      "You have " + String(inProgressCount) + " in_progress items in TASKS.md.",
                      "Add >=2 continuation slots for existing tasks across different topic clusters.",
                      "Set GLUDD_MULTITASK_DIVERSITY_ENFORCE=0 to disable.",
                    ].join("\n"),
                  }
                }
              }
            }
          }
        }
        writeState(_state)
        return
      }
      // Disabling floor/grinding policy never disables the hard dispatch cap.
      if (!FLOOR_ENFORCE) {
        writeState(_state)
        return
      }
      // --- Consecutive non-dispatch counter (grinding detection) ---
      // Counts mutation tool calls. After THRESHOLD calls within the time
      // window, blocks further non-dispatch mutations until a dispatch resets.
      // Read/grep/glob are excluded so investigation bursts remain cheap; an
      // explicitly configured minimum may still gate reads after a dispatch.
      //
      // Runs before the configured-minimum fallback so sustained mutation
      // grinding receives the specific streak diagnostic. Reads never advance
      // this counter and therefore cannot cause the mutation-streak block.
      //
      // PRESSURE-RELEASE: skip grinding block when in pressure-release or
      // inline-recovery mode. The agent needs inline tools to recover from
      // empty/failed dispatches.
      const pressureActive = isInPressureRelease() || isInInlineRecovery()
      if (!disengaged && !pressureActive) {
        if (!isReadTool(tool)) {
          if (_state.consecutiveNonDispatchStartTs === 0) {
            _state.consecutiveNonDispatchStartTs = now
          }
          if ((now - _state.consecutiveNonDispatchStartTs) < CONSECUTIVE_NON_DISPATCH_WINDOW_MS) {
            _state.consecutiveNonDispatch++
          } else {
            _state.consecutiveNonDispatch = 0
            _state.consecutiveNonDispatchStartTs = now
            _state.consecutiveNonDispatch++
          }
        }
        // === CONSECUTIVE NON-DISPATCH BLOCK ===
        if (
          _state.consecutiveNonDispatch >= CONSECUTIVE_NON_DISPATCH_THRESHOLD &&
          hasPendingWork()
        ) {
          writeState(_state)
          return {
            permissionDecision: "deny" as const,
            message: [
              "CONSECUTIVE NON-DISPATCH STREAK: " + String(_state.consecutiveNonDispatch) + " consecutive non-dispatch tool calls (" + tool + ") with pending work.",
              "Dispatch one suitable independent work item via task/agent/workflow, or use the configured recovery path.",
              "The dispatch resets this counter; never create agents merely to fill a quota.",
              "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable. Run 'make disengage-enforcement' to bypass.",
            ].join("\n"),
          }
        }
      }
      // === ZERO-DISPATCH STREAK (specialized diagnostic before fallback) ===
      if (
        !disengaged &&
        REQUIRED_DISPATCHES > 0 &&
        _state.thisMessageDispatches === 0 &&
        _state.zeroStreak >= MAX_ZERO_STREAK
      ) {
        writeState(_state)
        return {
          permissionDecision: "deny" as const,
          message: [
            "ZERO-DISPATCH STREAK: " + String(MAX_ZERO_STREAK) + " consecutive responses with 0 subagent dispatches.",
            "An operator-configured minimum is active: " + String(REQUIRED_DISPATCHES) + ".",
            "Dispatch suitable independent work; the hard ceiling remains " + String(MAX_DISPATCHES) + ".",
            "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable minimum enforcement.",
            "Run 'make disengage-enforcement' to bypass.",
          ].join("\n"),
        }
      }
      // === UNDER-FLOOR HARD BLOCK ===
      // Per AGENTS.md "UNDER-FLOOR HARD BLOCK (2026-07-15)": EVERY non-dispatch
      // tool call — including read/glob/grep — is blocked until the wave
      // reaches the floor. This closes the "dispatch 1, then grind reads"
      // bypass.
      //
      // 2026-07-25 FIX: previously only blocked edit/write/bash. The agent
      // dispatched 2 agents then used unlimited reads between waves —
      // underFloorCount reached 2066 without being mechanically stopped.
      // Now blocks ALL non-dispatch tools (includes read/glob/grep) when ANY
      // dispatches have been made this session. Session-start (0 dispatches)
      // still allows reads for the initial backlog survey.
      //
      // Fallback for the first-edit-with-zero-dispatches case where the streak
      // counter (above) is still below threshold. When the streak has already
      // hit threshold, the streak block wins.
      //
      // The configured minimum is active only when its environment variable is present.
      // Pressure release may temporarily lower the requirement.
      const _isUnderFloorRead = lt === "read" || lt === "grep" || lt === "glob"
      const _isUnderFloorMutation = lt === "edit" || lt === "write" || lt === "bash"
      const _effectiveFloor = REQUIRED_DISPATCHES > 0
        ? getPressureReleaseFloor(REQUIRED_DISPATCHES)
        : 0
      if (
        !disengaged &&
        REQUIRED_DISPATCHES > 0 &&
        hasPendingWork() &&
        _state.thisMessageDispatches < _effectiveFloor &&
        (_isUnderFloorMutation || (_isUnderFloorRead && _state.sessionDispatchTotal > 0))
      ) {
        writeState(_state)
        return {
          permissionDecision: "deny" as const,
          message: [
            "CONFIGURED MINIMUM BLOCK: ONLY " + String(_state.thisMessageDispatches) + " DISPATCHES.",
            "Configured minimum is " + String(_effectiveFloor) + ". Dispatch only the missing suitable work items.",
            "You have " + String(_state.thisMessageDispatches) + "; need " + String(_effectiveFloor) + ". ALL tools (read/grep/glob/edit/write/bash) are blocked when below floor and dispatches have been made this session.",
            "consecutive non-dispatch calls: " + String(_state.consecutiveNonDispatch),
            "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
            "Run 'make disengage-enforcement' to bypass.",
          ].join("\n"),
        }
      }
      // === SANITY CHECK: verify dispatch count before blocking ===
      // If the counter exceeds sane bounds after boundary detection,
      // the count is unreliable — log a warning and force-reset.
      if (
        !disengaged &&
        hasPendingWork() &&
        _state.thisMessageDispatches > 0 &&
        _state.thisMessageDispatches > MAX_DISPATCHES * 2
      ) {
        console.warn(
          "MULTITASK SANITY: thisMessageDispatches=" + String(_state.thisMessageDispatches) +
          " exceeds MAX_DISPATCHES*2=" + String(MAX_DISPATCHES * 2) +
          " — count is unreliable. Force-resetting to 1."
        )
        _state.thisMessageDispatches = 1
        _state.sawNonDispatchSinceDispatch = false
        writeState(_state)
        return
      }
      writeState(_state)
    } catch {
      return
    }
  },
  "text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    return await handleTextComplete(_input, output)
  },
  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    return await handleTextComplete(_input, output)
  },
}

async function handleTextComplete(_input: unknown, output: unknown): Promise<unknown> {
    const tx = typeof output === "string" ? output : (output as any)?.text ? String((output as any).text) : ""
    if (isSubagent()) { return output }
    const currentDispatchCount = readAndClearDispatchCountFile()
    if (!FLOOR_ENFORCE) { return undefined }
    const text = tx
    if (!text || text.trim().length === 0) { return output }
    if (isDisengaged()) { return output }
    // RESEARCH FINDING: opencode text.complete never receives tool output.
    // Result markers here are assistant text, so they must feed the same
    // message-boundary logic as any other assistant response. The next
    // handleMessageBoundary(_state) updates _state.prevMessageDispatches and
    // applies zeroStreak++ when no dispatches occurred.
    const hasResultMarker = /(?:task result|subagent result|workflow result)/i.test(text)
    if (hasResultMarker) {
      _state.estimatedInFlight = Math.max(0, _state.estimatedInFlight - 1)
      // PRESSURE-RELEASE detection: when subagent results arrive, check if
      // they indicate empty/failed outcomes. Short text after a result marker
      // OR failure/error/empty keywords signal a dispatch that produced no
      // useful work. After 3 consecutive empty dispatches, pressure-release
      // mode activates automatically.
      const isShortResult = text.length < 200
      const isEmptyPattern = /(?:failed|error|empty|no result|nothing|unable|cannot|could not|unsuccessful)/i.test(text)
      const isSummaryWithNoDispatches = _state.thisMessageDispatches === 0 && hasResultMarker
      if (isEmptyPattern || isShortResult || isSummaryWithNoDispatches) {
        recordEmptyDispatch()
      } else {
        recordSuccessfulDispatch()
      }
    }
    // PRESSURE-RELEASE: decrement turn counters at every message boundary.
    // This is the canonical boundary signal — text.complete fires at the
    // end of every assistant response.
    decrementPressureReleaseTurns()
    const _tef = REQUIRED_DISPATCHES > 0
      ? getPressureReleaseFloor(REQUIRED_DISPATCHES)
      : 0
    _state.thisMessageDispatches = currentDispatchCount
    const _observedDispatches = _state.thisMessageDispatches
    if (
      _state.thisMessageDispatches < _tef && _state.sessionDispatchTotal > 0 &&
      hasPendingWork()
    ) {
      handleMessageBoundary(_state)
      const _blockKind = _observedDispatches === 0
        ? "ZERO-DISPATCH TEXT BLOCKED"
        : "THIN WAVE BLOCKED"
      const _lines = [
        _blockKind + " - only " + String(_observedDispatches) + " dispatch(es) in this message.",
        "The configured minimum requires " + String(_tef) + " per wave.",
        "Dispatch only suitable independent work; never create agents merely to fill a quota.",
        "Your text has been blanked.",
        "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
      ]
      if (_state.underFloorCount >= 3) {
        _lines.push(
          "ESCALATION: DISPATCH FLOOR VIOLATION - " +
          String(_state.underFloorCount) +
          " consecutive waves with fewer than " +
          String(_tef) +
          " dispatches."
        )
      }
      writeState(_state)
      return { text: _lines.join("\n") }
    }
    handleMessageBoundary(_state)
    const hasWork = hasPendingWork()
    if (!hasWork) {
      writeState(_state)
      return output
    }
    const warnings: string[] = []
    if (_state.underFloorCount >= 3) {
      warnings.push([
        "DISPATCH FLOOR VIOLATION: " + String(_state.underFloorCount) + " consecutive waves with fewer than " + String(REQUIRED_DISPATCHES) + " dispatches.",
        "Set GLUDD_MULTITASK_FLOOR_ENFORCE=0 to disable.",
      ].join("\n"))
    }
    if (warnings.length > 0) {
      const warning = warnings.join("\n\n")
      const wrappedOutput = typeof output === "string"
        ? warning + "\n\n" + output
        : (output as any)?.text
          ? { ...(output as any), text: warning + "\n\n" + String((output as any).text) }
          : output
      writeState(_state)
      return wrappedOutput
    }
    writeState(_state)
    return output
}
// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  return {
    "tool.execute.before": async (input: { tool?: string }) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return
      const impl = loadHotModule("multitask", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
  },
  // NOTE: opencode 1.17.9 rejects the bare "text.complete" hook key in the
  // Plugin return object (crashes Plugin.add with TypeError evaluating
  // 'N.event'). Only "experimental.text.complete" is valid. The alias in
  // defaultImpl is retained for hot-reload back-compat but must NOT appear
  // in the proxy's returned Hooks object.
  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    const impl = loadHotModule("multitask", defaultImpl)
    const fn = impl["experimental.text.complete"] ?? impl["text.complete"]
    return fn ? await fn(_input, output) : undefined
  },
  }
}) satisfies Plugin
