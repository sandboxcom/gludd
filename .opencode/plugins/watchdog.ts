import type { Plugin } from "@opencode-ai/plugin"
import { spawn } from "node:child_process"
import * as fs from "node:fs"

let watchdogPid: number | null = null

// Env-overridable so pytest-xdist tests redirect it to a per-test tmp file
// (GLUDD_WATCHDOG_PID_FILE). The literal /tmp fallback is preserved for prod
// and for structural tests that assert the path string is present. Sharing the
// hardcoded path across xdist workers flaked CI (unit-2 FileNotFoundError when a
// sibling worker's fixture _restore() unlinked it mid-read).
const PID_FILE = process.env.GLUDD_WATCHDOG_PID_FILE || "/tmp/gludd-watchdog.pid"

function _reportAlive(): void {
  try {
    const alive: Record<string, any> = {}
    try { if (fs.existsSync("/tmp/gludd-plugin-alive.json")) { const d = JSON.parse(fs.readFileSync("/tmp/gludd-plugin-alive.json", "utf8")); if (typeof d === "object" && d !== null) Object.assign(alive, d) } } catch {}
    alive["watchdog"] = { last_seen: Date.now() }
    fs.writeFileSync("/tmp/gludd-plugin-alive.json", JSON.stringify(alive), "utf8")
  } catch {}
}

export default (async ({ $ }) => {
  return {
    event: async ({ event }: { event: { type: string } }) => {
      _reportAlive()
      if (event.type === "session.created") {
        try {
          // Kill any existing watchdog first
          try { const oldPid = fs.readFileSync(PID_FILE, "utf8").trim(); process.kill(parseInt(oldPid)) } catch {}
          // Start new watchdog
          const child = spawn("python3", ["scripts/agent_watchdog.py"], {
            cwd: process.cwd(),
            detached: true,
            stdio: ["ignore", fs.openSync("/tmp/gludd-watchdog.log", "a"), fs.openSync("/tmp/gludd-watchdog.log", "a")]
          })
          watchdogPid = child.pid
          fs.writeFileSync(PID_FILE, String(child.pid))
          child.unref()
        } catch (e) {
          // fail open — watchdog is optional
        }
      }
      if (event.type === "server.connected") {
        // Ensure watchdog is running on server connect too
        try {
          const result = await $`python3 scripts/agent_watchdog.py --once 2>/dev/null || true`
        } catch {}
      }
      if (event.type === "session.deleted") {
        try {
          const pidFile = PID_FILE
          if (fs.existsSync(pidFile)) {
            const pid = parseInt(fs.readFileSync(pidFile, "utf8").trim())
            process.kill(pid, "SIGTERM")
            fs.unlinkSync(pidFile)
          }
        } catch {}
      }
    },
  }
}) satisfies Plugin
