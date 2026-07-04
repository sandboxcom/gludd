import type { Plugin } from "@opencode-ai/plugin"
import { spawn } from "node:child_process"
import * as fs from "node:fs"

let watchdogPid: number | null = null

export default (async ({ $ }) => {
  return {
    event: async ({ event }: { event: { type: string } }) => {
      if (event.type === "session.created") {
        try {
          // Kill any existing watchdog first
          try { const oldPid = fs.readFileSync("/tmp/gludd-watchdog.pid", "utf8").trim(); process.kill(parseInt(oldPid)) } catch {}
          // Start new watchdog
          const child = spawn("python3", ["scripts/agent_watchdog.py"], {
            cwd: process.cwd(),
            detached: true,
            stdio: ["ignore", fs.openSync("/tmp/gludd-watchdog.log", "a"), fs.openSync("/tmp/gludd-watchdog.log", "a")]
          })
          watchdogPid = child.pid
          fs.writeFileSync("/tmp/gludd-watchdog.pid", String(child.pid))
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
          const pidFile = "/tmp/gludd-watchdog.pid"
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
