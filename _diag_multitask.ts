import * as fs from "node:fs"
import * as path from "node:path"

// Dump all the relevant env vars
const vars = [
  "GLUDD_MULTITASK_FLOOR_ENFORCE",
  "GLUDD_MIN_DISPATCHES",
  "GLUDD_MULTITASK_MIN_DISPATCHES",
  "GLUDD_MULTITASK_MAX_DISPATCHES",
  "GLUDD_MSG_GAP_MS",
  "OPENCODE_SUBAGENT",
  "GLUDD_DISENGAGE_PATH",
  "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD",
  "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS",
]
const envDump: Record<string, string|undefined> = {}
for (const v of vars) {
  envDump[v] = process.env[v]
}

// Check state files
const stateFiles = [
  "/tmp/gludd-watchdog-disengage.json",
  "/tmp/gludd-subagent-" + process.pid + ".json",
  "/tmp/gludd-multitask-state.json",
]
const fileContents: Record<string, string|null> = {}
for (const f of stateFiles) {
  try {
    fileContents[f] = fs.readFileSync(f, "utf8")
  } catch {
    fileContents[f] = null
  }
}

// Check hasPendingWork
const hasPendingWork = (() => {
  try {
    const p = path.join(process.cwd(), "TASKS.md")
    if (!fs.existsSync(p)) return {exists: false, path: p}
    const content = fs.readFileSync(p, "utf8")
    const match = /^\s*[-*]\s*\[\s*\]/m.test(content)
    return {exists: true, path: p, content, match}
  } catch (e: any) {
    return {error: e.message}
  }
})()

// Actually try the plugin
let pluginResult: any = {error: "not loaded"}
try {
  const mod = await import("/Users/shawnwilson/gludd/.opencode/plugin/enforce-multitask.ts")
  const plugin = await mod.default({})
  const r = await plugin['tool.execute.before']({tool: 'write'}, undefined)
  pluginResult = r ?? {allowed: true}
} catch (e: any) {
  pluginResult = {error: e.message, stack: e.stack?.split('\n').slice(0,5).join('\n')}
}

console.log(JSON.stringify({
  env: envDump,
  stateFiles: fileContents,
  hasPendingWork,
  pluginResult,
  cwd: process.cwd(),
  pid: process.pid,
}))
