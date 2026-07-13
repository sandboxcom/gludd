import * as fs from "node:fs"
import * as path from "node:path"
import { execSync, spawn } from "node:child_process"
import { isSubagent, isDisengaged as isWatchdogDisengaged, reportAlive, writeHeartbeat, isDispatchTool, isReadTool, updateSharedStreak } from "../lib/shared.ts"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"

const FLOOR = parseInt(process.env.CLAUDE_AGENT_FLOOR || "7", 10)

const BLOCK_REASON_FILE = process.env.GLUDD_BLOCK_REASON_FILE || "/tmp/gludd-block-reason.json"
const BLOCK_COUNTER_FILE = process.env.GLUDD_BLOCK_COUNTER_FILE || "/tmp/gludd-block-counter.json"
const PERSIST_BLOCK_FILE = process.env.GLUDD_PERSIST_STOP_BLOCK_FILE || "/tmp/gludd-persist-stop-block.json"

const DELEGATE_FIRST_THRESHOLD = 8
const GRINDING_HARD_DENY_THRESHOLD = 12

// ── SPOT GATE REFRESH (background) ──────────────────────────────────────────

function spawnGateRefresh(): void {
  try {
    const gatePath = path.join(process.cwd(), ".gate-status")
    if (!fs.existsSync(gatePath)) return
    const stat = fs.statSync(gatePath)
    if ((Date.now() - stat.mtimeMs) <= 300_000) return
    const child = spawn("make", ["gate-refresh"], {
      cwd: process.cwd(),
      detached: true,
      stdio: "ignore",
    })
    child.unref()
  } catch {}
}

// ── BLOCK COUNTER (false-positive cascade detection) ────────────────────────

interface BlockCounter {
  consecutiveBlocks: number
  totalBlocks: number
  lastBlockTs: number
  disengageUntil: number
}

function readBlockCounter(): BlockCounter {
  try {
    if (fs.existsSync(BLOCK_COUNTER_FILE)) {
      const c: BlockCounter = JSON.parse(fs.readFileSync(BLOCK_COUNTER_FILE, "utf8"))
      const now = Date.now()
      if (c.lastBlockTs && (now - c.lastBlockTs) > 120_000 && c.consecutiveBlocks > 0) {
        c.consecutiveBlocks = 0
      }
      const MAX_DISENGAGE_HERE = now + 3_600_000
      if (c.disengageUntil > MAX_DISENGAGE_HERE) {
        c.disengageUntil = MAX_DISENGAGE_HERE
        try { fs.writeFileSync(BLOCK_COUNTER_FILE, JSON.stringify(c), "utf8") } catch {}
      }
      return c
    }
  } catch {}
  return { consecutiveBlocks: 0, totalBlocks: 0, lastBlockTs: 0, disengageUntil: 0 }
}

function writeBlockCounter(c: BlockCounter): void {
  try { fs.writeFileSync(BLOCK_COUNTER_FILE, JSON.stringify(c), "utf8") } catch {}
}

// ── PERSISTENT STOP-BLOCK FLAG ─────────────────────────────────────────────

interface PersistBlockFlag {
  blocked: boolean
  timestamp: number
  reason: string
}

function readPersistBlock(): PersistBlockFlag {
  try {
    if (fs.existsSync(PERSIST_BLOCK_FILE)) {
      const raw = JSON.parse(fs.readFileSync(PERSIST_BLOCK_FILE, "utf8"))
      return {
        blocked: !!raw.blocked,
        timestamp: typeof raw.timestamp === "number" ? raw.timestamp : 0,
        reason: typeof raw.reason === "string" ? raw.reason : "",
      }
    }
  } catch {}
  return { blocked: false, timestamp: 0, reason: "" }
}

function writePersistBlock(blocked: boolean, reason: string): void {
  try {
    fs.writeFileSync(PERSIST_BLOCK_FILE, JSON.stringify({
      blocked,
      timestamp: Date.now(),
      reason,
    }), "utf8")
  } catch {}
}

function clearPersistBlock(): void {
  try { fs.unlinkSync(PERSIST_BLOCK_FILE) } catch {}
}

function recordBlock(reason: string): void {
  const c = readBlockCounter()
  const now = Date.now()
  c.totalBlocks++
  const prevTs = c.lastBlockTs
  c.lastBlockTs = now
  if (now - prevTs < 120_000) c.consecutiveBlocks++
  else c.consecutiveBlocks = 1
  if (c.consecutiveBlocks >= 20) {
    c.disengageUntil = now + 120_000
    console.error("FALSE-POSITIVE CASCADE: disengaging for 2 min after 20 consecutive blocks")
  }
  writeBlockCounter(c)
  try {
    fs.writeFileSync(BLOCK_REASON_FILE, JSON.stringify({
      reason,
      consecutive: c.consecutiveBlocks,
      ts: now,
    }), "utf8")
  } catch {}
}

function isDisengaged(): boolean {
  const c = readBlockCounter()
  const now = Date.now()
  const MAX_DISENGAGE = now + 3_600_000
  if (c.disengageUntil > MAX_DISENGAGE) {
    c.disengageUntil = MAX_DISENGAGE
    try { fs.writeFileSync(BLOCK_COUNTER_FILE, JSON.stringify(c), "utf8") } catch {}
  }
  if (c.disengageUntil > now) return true
  return false
}

// ── QUESTION DENY ──────────────────────────────────────────────────────────

const QUESTION_DENY_REASON = [
  "BLOCKING QUESTION DENIED — user standing directive: never interrupt work to",
  "ask. DEFAULT TO ACTION: choose the most reasonable option yourself, state in",
  "one line the assumption you are making, and PROCEED.",
].join(" ")

// ── STOP-LIKE TOOL DENY ────────────────────────────────────────────────────

const STOP_LIKE_TARGETS_RE = /^make\s+(git-commit|commit-no-verify|ship-commit|git-push-branch|git-push-branch-nv|git-push-sandboxcom|git-push-sandboxcom-main|git-push-master|git-tag-push|release-cut|release-promote|test-and-commit|repo-commit|feature-done|release-recut|release-branch-new|git-merge)(\s|$)/

function stopLikeDenyMessage(taskMd: boolean, ratchetEntries: number, extraReasons: string[] = []): string {
  const reasons = [
    `TASKS.md unchecked: ${taskMd ? "yes" : "no"}, ratchet entries: ${ratchetEntries}`,
    ...extraReasons,
  ]
  return [
    "STOP-LIKE TOOL BLOCKED — PENDING WORK EXISTS:",
    ...reasons,
    "Fix pending work first, then retry.",
  ].join("\n")
}

const COMMIT_TARGET_RE = /^make\s+(git-commit|commit-no-verify|git-commit-file|test-and-commit|repo-commit|feature-done|git-merge)(\s|$)/
const PUSH_TARGET_RE = /^make\s+(git-push-branch|git-push-branch-nv|git-push-sandboxcom|git-push-sandboxcom-main|git-push-master|git-tag-push|release-cut|release-promote|ship-commit|release-recut|release-branch-new)(\s|$)/

// ── WORK-STATE CHECKERS (read from filesystem, no caching) ──────────────────

function ratchetHasEntries(): number {
  try {
    const ratchetPath = path.join(process.cwd(), "config", "ratchet.yml")
    if (!fs.existsSync(ratchetPath)) return 0
    const content = fs.readFileSync(ratchetPath, "utf8")
    return content.split("\n").filter(
      l => l.trim() && !l.trim().startsWith("#") && (l.includes("::") || /^\w[\w\s]*:\s/.test(l))
    ).length
  } catch { return 0 }
}

function tasksMdHasUnchecked(): boolean {
  try {
    const tasksPath = path.join(process.cwd(), "TASKS.md")
    if (!fs.existsSync(tasksPath)) return false
    const content = fs.readFileSync(tasksPath, "utf8")
    return /-\s+\[\s*\]/.test(content) || /\*\s+\[\s*\][^xX]/i.test(content)
  } catch { return false }
}

function countTasksMdUnchecked(): number {
  try {
    const tasksPath = path.join(process.cwd(), "TASKS.md")
    if (!fs.existsSync(tasksPath)) return 0
    const content = fs.readFileSync(tasksPath, "utf8")
    const matches = content.match(/^[-*]\s+\[ \]/gm)
    return matches ? matches.length : 0
  } catch { return 0 }
}

function gateStatusIsRed(): boolean {
  try {
    const gatePath = path.join(process.cwd(), ".gate-status")
    if (!fs.existsSync(gatePath)) return false
    const content = fs.readFileSync(gatePath, "utf8")
    for (const line of content.split("\n")) {
      if (line.startsWith("===")) continue
      if (/^(lint |typecheck |collect |test |smoke |env-writes |dead-code |hook-runtime |verify-enforcement |coverage-gaps )/.test(line)) {
        if (/FAIL/.test(line)) return true
      }
    }
    return false
  } catch { return false }
}

function gateStatusIsStale(minAgeMs: number = 300_000): boolean {
  try {
    const gatePath = path.join(process.cwd(), ".gate-status")
    if (!fs.existsSync(gatePath)) return false
    const stat = fs.statSync(gatePath)
    return (Date.now() - stat.mtimeMs) > minAgeMs
  } catch { return false }
}

function bugsMdHasOpenIncidents(): boolean {
  try {
    const bugsPath = path.join(process.cwd(), "BUGS.md")
    if (!fs.existsSync(bugsPath)) return false
    const content = fs.readFileSync(bugsPath, "utf8")
    const openIncidents = content
      .split("\n")
      .filter(l => /^###\s+\d{4}-\d{2}-\d{2}\s+[-—]/.test(l))
      .filter(l => !l.includes("(resolved)"))
    return openIncidents.length > 0
  } catch { return false }
}

let ciVerdictCache: { ts: number; isPendingOrRed: boolean } | null = null

function ciIsPendingOrRed(): boolean {
  const now = Date.now()
  if (ciVerdictCache && (now - ciVerdictCache.ts) < 60_000) {
    return ciVerdictCache.isPendingOrRed
  }
  try {
    const ciCachePath = "/tmp/gludd-watchdog-ci.json"
    if (fs.existsSync(ciCachePath)) {
      const ciData = JSON.parse(fs.readFileSync(ciCachePath, "utf8"))
      const lastCheck = ciData.last_ci_check || 0
      const lastStatus = ciData.last_ci_status || ""
      if (now - lastCheck < 120_000 && lastStatus) {
        const isGreen = lastStatus === "SUCCESS"
        ciVerdictCache = { ts: now, isPendingOrRed: !isGreen }
        return !isGreen
      }
    }
  } catch {}
  return false
}

function repoHasPendingWork(inExecSync: any, mode?: "commit" | "push"): boolean {
  try {
    const cwd = process.cwd()
    try {
      const status = inExecSync("git status --porcelain", {
        cwd, encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"],
      }) as string
      const lines = status.trim().split("\n").filter(l => l.trim().length > 0)
      if (lines.length === 0) return false
      if (mode === "commit") {
        return lines.some(l => {
          const y = l.length > 1 ? l[1] : " "
          return y !== " "
        })
      }
      return true
    } catch {}
    return false
  } catch { return false }
}

// ── TURN STATE (persists across tool calls within a single turn) ────────────

const turnState = { blocked: false, dispatchCount: 0 }

// ── SUBAGENT TEXT MARKER DETECTION ────────────────────────────────────────

const SUBAGENT_TEXT_MARKERS = /task_id|task_result|agent\s+result|subagent\s+result|task\s+completed/i

function containsSubagentMarkers(text: string): boolean {
  return SUBAGENT_TEXT_MARKERS.test(text)
}

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input: any, output: any) => {
    if (isSubagent()) return
    reportAlive("enforce-stop")
    writeHeartbeat("enforce-stop")

    const persistBlock = readPersistBlock()
    if (persistBlock.blocked) {
      const isDispatch = isDispatchTool(input.tool)
      if (isDispatch) {
        clearPersistBlock()
      } else {
        try {
          const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
          let data: Record<string, any> = { allowed: 0, blocked: 0 }
          if (fs.existsSync(cPath)) { try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {} }
          data.blocked = (parseInt(data.blocked, 10) || 0) + 1
          data.last_blocked = { tool: input.tool, reason: `persist-stop-block: ${persistBlock.reason}`, ts: Date.now(), iso: new Date().toISOString() }
          fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
        } catch {}
        return {
          permissionDecision: "deny" as const,
          message: [
            "⛔ BLOCKED: stop-pattern detected in previous response.",
            `Reason: ${persistBlock.reason}`,
            "",
            "The ONLY valid next action is to DISPATCH SUBAGENTS via task/agent/workflow.",
            "All other tool calls are denied until you dispatch.",
          ].join("\n"),
        }
      }
    }

    try {
      try {
        const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
        let data: Record<string, any> = { allowed: 0, blocked: 0, last_fired: null as any, ts: 0 }
        if (fs.existsSync(cPath)) {
          try { const d = JSON.parse(fs.readFileSync(cPath, "utf8")); data = d } catch {}
        }
        data.last_fired = { tool: input.tool, ts: Date.now(), iso: new Date().toISOString() }
        data.ts = Date.now()
        data._outcome = "allowed"
        fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
      } catch {}

      if (turnState.blocked) {
        turnState.blocked = false
        clearPersistBlock()
      }

      if (isDispatchTool(input.tool)) {
        turnState.dispatchCount++
      }

      const streakState = updateSharedStreak(input.tool, "enforce-stop")

      if (input.tool === "question") {
        try {
          const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
          let data: Record<string, any> = { allowed: 0, blocked: 0 }
          if (fs.existsSync(cPath)) {
            try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {}
          }
          data.blocked = (parseInt(data.blocked, 10) || 0) + 1
          data.last_blocked = { tool: input.tool, reason: "question_denied", ts: Date.now(), iso: new Date().toISOString() }
          fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
        } catch {}
        throw new Error(QUESTION_DENY_REASON)
      }

      if (input.tool === "bash") {
        const args = (output as Record<string, unknown> | undefined)?.args as { command?: string } | undefined
        const command = typeof args?.command === "string" ? args.command.trim() : ""
        if (command.startsWith("make ") && STOP_LIKE_TARGETS_RE.test(command)) {
          const taskMd = tasksMdHasUnchecked()
          const ratchetCount = ratchetHasEntries()
          const bugsOpen = bugsMdHasOpenIncidents()
          const gateRed = gateStatusIsRed()
          const ciBad = ciIsPendingOrRed()
          const repoMode: "commit" | "push" | undefined =
            COMMIT_TARGET_RE.test(command) ? "commit" :
            PUSH_TARGET_RE.test(command) ? "push" : undefined
          const repoPending = repoHasPendingWork(es, repoMode)
          const disengaged = isWatchdogDisengaged()
          if (!disengaged && (taskMd || ratchetCount > 0 || bugsOpen || gateRed || ciBad || repoPending)) {
            try {
              const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
              let data: Record<string, any> = { allowed: 0, blocked: 0 }
              if (fs.existsSync(cPath)) {
                try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {}
              }
              data.blocked = (parseInt(data.blocked, 10) || 0) + 1
              data.last_blocked = { tool: "bash", command: command, reason: "stop_like", ts: Date.now(), iso: new Date().toISOString() }
              fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
            } catch {}
            const extraReasons: string[] = []
            if (bugsOpen) extraReasons.push("BUGS.md open incidents")
            if (gateRed) {
              if (gateStatusIsStale()) {
                extraReasons.push("gate stale (>5min); run make gate-refresh to update lint/typecheck/collect")
              } else {
                extraReasons.push("gate RED")
              }
            }
            if (ciBad) extraReasons.push("CI pending/red")
            if (repoPending) extraReasons.push("repo dirty")
            throw new Error(stopLikeDenyMessage(taskMd, ratchetCount, extraReasons))
          }
        }
      }

      const isMutationTool = !isDispatchTool(input.tool)
        && !isReadTool(input.tool)
        && input.tool !== "question"
      if (isMutationTool) {
        const grindingDisengaged = isWatchdogDisengaged()
        if (!grindingDisengaged) {
          if (streakState.streak > GRINDING_HARD_DENY_THRESHOLD) {
            try {
              const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
              let data: Record<string, any> = { allowed: 0, blocked: 0 }
              if (fs.existsSync(cPath)) {
                try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {}
              }
              data.blocked = (parseInt(data.blocked, 10) || 0) + 1
              data.last_blocked = { tool: input.tool, reason: "main-thread-grinding", streak: streakState.streak, ts: Date.now(), iso: new Date().toISOString() }
              fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
            } catch {}
            recordBlock("main-thread-grinding")
            return {
              permissionDecision: "deny" as const,
              message: [
                "⛔ MAIN-THREAD GRINDING DETECTED",
                `${streakState.streak} consecutive non-dispatch calls.`,
                "You are grinding on the main thread with no subagent dispatch.",
                "DISPATCH WORK via task/agent/workflow or justify why this must be inline.",
                "",
                `Streak breakdown: ${streakState.readStreak} reads, ${streakState.editStreak} edits/bash.`,
              ].join("\n"),
            }
          }
          if (streakState.streak > DELEGATE_FIRST_THRESHOLD) {
            console.warn(
              `DELEGATE-FIRST: ${streakState.streak} consecutive non-dispatch calls. ` +
              `You are trending toward main-thread grinding. ` +
              `DISPATCH WORK via task/agent/workflow before continuing inline work. ` +
              `Streak breakdown: ${streakState.readStreak} reads, ${streakState.editStreak} edits/bash.`
            )
          }
        }
      }
    } catch (e) {
      if (e instanceof Error && (e.message.includes("BLOCKED") || e.message.includes("BLOCKING"))) throw e
      console.error("[enforce-stop] tool.execute.before error (fail-open):", e)
    }
    try {
      const cPath = process.env.GLUDD_STOP_TOOL_COUNTS_FILE || "/tmp/gludd-stop-tool-counts.json"
      let data: Record<string, any> = { allowed: 0, blocked: 0 }
      if (fs.existsSync(cPath)) {
        try { data = JSON.parse(fs.readFileSync(cPath, "utf8")) } catch {}
      }
      data.allowed = (parseInt(data.allowed, 10) || 0) + 1
      data.last_allowed = { ts: Date.now(), iso: new Date().toISOString() }
      fs.writeFileSync(cPath, JSON.stringify(data), "utf8")
    } catch {}
  },

  "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    const unchecked = countTasksMdUnchecked()
    const ratchetCount = ratchetHasEntries()
    const bugsOpen = bugsMdHasOpenIncidents()
    const gateRed = gateStatusIsRed()
    const ciBad = ciIsPendingOrRed()
    const { execSync: es } = require("node:child_process") as { execSync: typeof import("node:child_process").execSync }
    const repoPending = repoHasPendingWork(es)
    const hasWork = unchecked > 0 || ratchetCount > 0 || bugsOpen || gateRed || ciBad || repoPending

    if (typeof output === "string") {
      if (containsSubagentMarkers(output)) return output
      if (hasWork) {
        const indicators: string[] = []
        if (unchecked > 0) indicators.push(`${unchecked} unchecked TASKS.md items`)
        if (ratchetCount > 0) indicators.push(`${ratchetCount} ratchet entries`)
        if (bugsOpen) indicators.push("BUGS.md open incidents")
        if (gateRed) indicators.push("gate RED")
        if (ciBad) indicators.push("CI pending/red")
        if (repoPending) indicators.push("repo dirty")
        const block = [
          "",
          "══════════════════════════════════════════════════════════════",
          "⛔⛔⛔ MANDATORY PRE-GENERATION GATE ⛔⛔⛔",
          "══════════════════════════════════════════════════════════════",
          "",
          `PENDING WORK EXISTS: ${indicators.join(", ")}.`,
          "",
          "YOU ARE PHYSICALLY FORBIDDEN FROM GENERATING A TEXT-ONLY RESPONSE.",
          "",
          "Before generating ANY text character, you MUST include a tool call.",
          "Acceptable: dispatch a subagent (Task tool), read a file (Read tool),",
          "edit code (Edit/Write tool), or run a make target (Bash tool).",
          "",
          "UNACCEPTABLE AND WILL BE BLANKED:",
          "- Status reports / summaries / recaps",
          "- Tables of completed work",
          "- 'All done' / 'Everything is complete' / 'Ready for review'",
          "- Any text without an accompanying tool call",
          "- 'Here is what I'll do next' without actually DOING it",
          "",
          "Example of CORRECT response when subagent results arrive:",
          "  [Task tool: dispatch 10 more subagents to continue work]",
          "",
          "Example of INCORRECT response (will be blanked):",
          "  'All 10 subagents completed. Here's a summary of results...'",
          "",
          "YOU HAVE BEEN WARNED. GENERATE A TOOL CALL NOW.",
          "══════════════════════════════════════════════════════════════",
          "",
          output
        ].join("\n")
        return block
      }
      return `[orchestration] No pending work. Normal operation.\n\n${output}`
    }
    return output
  },
}

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  spawnGateRefresh()
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-stop ` +
      `tool.execute.before+experimental.chat.system.transform ` +
      `pid=${process.pid}\n`,
      "utf8",
    )
  } catch {}
  return {
    "tool.execute.before": async (input: any, output: any) => {
      if (isSubagent()) return
      const impl = loadHotModule("enforce-stop", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
    "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output
      const impl = loadHotModule("enforce-stop", defaultImpl)
      const fn = impl["experimental.chat.system.transform"]
      return fn ? await fn(_input, output) : output
    },
  }
}) satisfies Plugin
