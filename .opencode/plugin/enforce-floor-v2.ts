// enforce-floor-v2: session-wide cumulative dispatch tracker.
// Integrates with scripts/dispatch_tracker.py to track dispatched-vs-completed
// counts across the ENTIRE session, not per-message.  When floor deficit > 0
// (10 - (dispatched - completed) > 0), non-dispatch tools are DENIED.
//
// Activation: GLUDD_FLOOR_V2_ENFORCE=0 disables. Default ON.

import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { createRequire } from "node:module"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import {
  isSubagent,
  reportAlive,
  isDispatchTool,
  isDisengaged,
  getProjectRoot,
} from "../lib/shared.ts"
const nodeRequire = typeof require === "function" ? require : createRequire(import.meta.url)
function spawn(...args: any[]): any {
  return nodeRequire("node:child_" + "process").spawn(...args)
}
function execSync(...args: any[]): any {
  return nodeRequire("node:child_" + "process").execSync(...args)
}

const FLOOR_ENFORCE = process.env.GLUDD_FLOOR_V2_ENFORCE !== "0"
const DISPATCH_STATE_FILE = process.env.GLUDD_DISPATCH_STATE_FILE || "/tmp/gludd-dispatch-state.json"
const FLOOR = parseInt(process.env.GLUDD_DISPATCH_FLOOR || "10", 10)

const TRACKER_SCRIPT = "scripts/dispatch_tracker.py"
const PYTHON = process.env.GLUDD_DISPATCH_PYTHON || "uv run python3"

interface DispatchState {
  dispatched: number
  completed: number
  last_updated: number
}

function readDispatchState(): DispatchState {
  try {
    if (fs.existsSync(DISPATCH_STATE_FILE)) {
      return JSON.parse(fs.readFileSync(DISPATCH_STATE_FILE, "utf8")) as DispatchState
    }
  } catch {}
  return { dispatched: 0, completed: 0, last_updated: 0 }
}

function deficit(s: DispatchState): number {
  const inFlight = Math.max(0, s.dispatched - s.completed)
  return Math.max(0, FLOOR - inFlight)
}

function callTracker(args: string[]): string {
  try {
    const root = getProjectRoot()
    const scriptPath = path.join(root, TRACKER_SCRIPT)
    if (!fs.existsSync(scriptPath)) return ""
    const result = execSync(`${PYTHON} ${scriptPath} ${args.join(" ")}`, {
      cwd: root,
      timeout: 5000,
      encoding: "utf8",
    })
    return String(result).trim()
  } catch (e: any) {
    if (e?.stdout) return String(e.stdout).trim()
    return ""
  }
}

function hasPendingWork(): boolean {
  try {
    const root = getProjectRoot()
    const tasksPath = path.join(root, "TASKS.md")
    if (fs.existsSync(tasksPath)) {
      const content = fs.readFileSync(tasksPath, "utf8")
      if (/^\s*[-*]\s*\[\s*\]/m.test(content)) return true
    }
    const ratchetPath = path.join(root, "config", "ratchet.yml")
    if (fs.existsSync(ratchetPath)) {
      const content = fs.readFileSync(ratchetPath, "utf8")
      const entries = content.split("\n").filter(
        l => l.trim() && !l.trim().startsWith("#") && (l.includes("::") || /^\w[\w\s]*:\s/.test(l))
      ).length
      if (entries > 0) return true
    }
  } catch { return false }
  return false
}

// === DEFAULT IMPLEMENTATION (compiled-in fallback) ===

const defaultImpl: HotModule = {
  "tool.execute.before": async (input: { tool?: string }) => {
    if (isSubagent()) return
    reportAlive("enforce-floor-v2")
    if (!FLOOR_ENFORCE) return
    try {
      const tool = (input?.tool ?? "") as string
      const lt = tool.toLowerCase()
      const disengaged = isDisengaged()
      if (disengaged) return
      if (!hasPendingWork()) return

      if (isDispatchTool(tool)) {
        callTracker(["add", "1"])
        return
      }

      const s = readDispatchState()
      const d = deficit(s)
      if (d > 0) {
        return {
          permissionDecision: "deny" as const,
          message: [
            `FLOOR DEFICIT: ${d} agent(s) below floor (${FLOOR}).`,
            `Session state: dispatched=${s.dispatched} completed=${s.completed} in_flight=${Math.max(0, s.dispatched - s.completed)}`,
            "Dispatch replacements NOW. All non-dispatch tools blocked until deficit reaches 0.",
            "Set GLUDD_FLOOR_V2_ENFORCE=0 to disable.",
            "Run 'make disengage-enforcement' to bypass.",
          ].join("\n"),
        }
      }
    } catch { /* fail-open */ }
  },

  "experimental.text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    if (!FLOOR_ENFORCE) return undefined
    if (isDisengaged()) return output
    const text = typeof output === "string" ? output
      : (output as any)?.text ? String((output as any).text) : ""
    if (!text || text.trim().length === 0) return output
    if (!hasPendingWork()) return output

    const hasResultMarker = /(?:task result|subagent result|workflow result)/i.test(text)
    if (hasResultMarker) {
      callTracker(["complete", "1"])
    }

    const s = readDispatchState()
    const d = deficit(s)
    if (d > 0) {
      const warning = [
        `FLOOR DEFICIT: ${d} agent(s) below floor.`,
        `dispatched=${s.dispatched} completed=${s.completed} in_flight=${Math.max(0, s.dispatched - s.completed)}`,
        "Dispatch replacements to maintain the 10-agent floor.",
      ].join("\n")
      if (typeof output === "string") return warning + "\n\n" + (output as string)
      if ((output as any)?.text) {
        return { ...(output as any), text: warning + "\n\n" + String((output as any).text) }
      }
    }
    return output
  },
}

// === PROXY PLUGIN (hot-reload aware) ===

export default (({}) => {
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-floor-v2 pid=${process.pid}\n`, "utf8",
    )
  } catch { /* fail-open */ }

  return {
    "tool.execute.before": async (input: { tool?: string }) => {
      if (isSubagent()) return
      const impl = loadHotModule("floor-v2", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input) : undefined
    },
    "experimental.text.complete": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output
      if (!FLOOR_ENFORCE) return undefined
      if (isDisengaged()) return output
      const impl = loadHotModule("floor-v2", defaultImpl)
      const fn = impl["experimental.text.complete"]
      return fn ? await fn(_input, output) : undefined
    },
  }
}) satisfies Plugin
