import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"
import { reportAlive } from "../lib/shared.ts"

const PID_FILE = process.env.GLUDD_WATCHDOG_PID_FILE || ".gate-logs/watchdog.pid"

function writePidFile(): void {
  try {
    fs.mkdirSync(path.dirname(PID_FILE), { recursive: true })
    fs.writeFileSync(PID_FILE, String(process.pid), "utf8")
  } catch {}
}

function removePidFile(): void {
  try {
    fs.unlinkSync(PID_FILE)
  } catch {}
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
