import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { reportAlive } from "../lib/shared.ts"

const PID_FILE = process.env.GLUDD_WATCHDOG_PID_FILE || ".gate-logs/watchdog.pid"
const TASK_PID_FILE =
  process.env.GLUDD_TASK_WATCHDOG_PID || ".gate-logs/task-watchdog.pid"

function writePidFile(): void {
  try {
    fs.mkdirSync(path.dirname(PID_FILE), { recursive: true })
    fs.writeFileSync(PID_FILE, String(process.pid), "utf8")
  } catch {}
}

function removePidFile(): void {
  for (const pidFile of [PID_FILE, TASK_PID_FILE]) {
    try {
      fs.unlinkSync(pidFile)
    } catch {}
  }
}

export default ((_api: any) => {
  if (process.env.GLUDD_WATCHDOG_ENABLED === "0") return {}
  reportAlive("watchdog")
  return {
    "event": async (input: unknown) => {
      reportAlive("watchdog")
      const eventType = String((input as any)?.event?.type || "")
      if (eventType === "session.created") writePidFile()
      if (eventType === "session.deleted") removePidFile()
    },
  }
}) satisfies Plugin
