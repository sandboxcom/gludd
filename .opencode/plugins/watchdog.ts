import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import { reportAlive } from "../plugin/shared.ts"

const PID_FILE = process.env.GLUDD_WATCHDOG_PID_FILE || ".gate-logs/watchdog.pid"
const TASK_PID_FILE = ".gate-logs/task-watchdog.pid"

export default (async ({ $ }) => {
  return {
    event: async ({ event }: { event: { type: string } }) => {
      reportAlive("watchdog")
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
