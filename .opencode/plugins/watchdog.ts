import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import { reportAlive } from "../lib/shared.ts"

const PID_FILE = process.env.GLUDD_WATCHDOG_PID_FILE || ".gate-logs/watchdog.pid"
const TASK_PID_FILE = ".gate-logs/task-watchdog.pid"

export default ((_api: any) => {
  // watchdog daemon runs as background process via `make watchdog-auto`
  // event hook removed: opencode 1.17.9 crashes on unknown hook type
  reportAlive("watchdog")
  return {}
}) satisfies Plugin
