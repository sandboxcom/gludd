import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"

const PID_FILE = process.env.GLUDD_WATCHDOG_PID_FILE || ".gate-logs/watchdog.pid"
const TASK_PID_FILE = ".gate-logs/task-watchdog.pid"

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
        try { await $`make watchdog-auto` } catch {}
        // make watchdog-auto writes PID to .gate-logs/watchdog.pid (literal);
        // sync to PID_FILE when redirected (test-mode isolation)
        const literalPid = ".gate-logs/watchdog.pid"
        try {
          if (fs.existsSync(literalPid)) {
            fs.writeFileSync(PID_FILE, fs.readFileSync(literalPid, "utf8").trim())
          }
        } catch {}
      }
      if (event.type === "server.connected") {
        try { await $`make watchdog-auto` } catch {}
      }
      if (event.type === "session.deleted") {
        try {
          for (const pf of [PID_FILE, TASK_PID_FILE]) {
            if (fs.existsSync(pf)) {
              try { process.kill(parseInt(fs.readFileSync(pf, "utf8").trim()), "SIGTERM") } catch {}
              try { fs.unlinkSync(pf) } catch {}
            }
          }
        } catch {}
      }
    },
  }
}) satisfies Plugin
