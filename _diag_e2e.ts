import * as fs from "node:fs"
import * as path from "node:path"
import * as os from "node:os"

const ws = path.join(os.tmpdir(), "gludd-e2e-diag-" + process.pid)
fs.mkdirSync(ws, {recursive: true})
fs.writeFileSync(path.join(ws, "TASKS.md"), "- [ ] test item\n")

// Simulate exactly what _run_plugin does
process.env.OPENCODE_SUBAGENT = ""
process.env.GLUDD_MSG_GAP_MS = "500"
process.chdir(ws)

console.error("CWD:", process.cwd())
console.error("TASKS.md exists:", fs.existsSync(path.join(process.cwd(), "TASKS.md")))
console.error("SUBAGENT env:", process.env.OPENCODE_SUBAGENT)
console.error("FLOOR_ENFORCE env:", process.env.GLUDD_MULTITASK_FLOOR_ENFORCE)
console.error("MSG_GAP env:", process.env.GLUDD_MSG_GAP_MS)

try {
  const mod = await import("/Users/shawnwilson/gludd/.opencode/plugin/enforce-multitask.ts")
  const plugin = await mod.default({})
  const r = await plugin["tool.execute.before"]({tool: "write"}, undefined)
  console.log(JSON.stringify(r ?? {allowed: true}))
} catch (e) {
  console.error("IMPORT/INVOKE ERROR:", e.message, e.stack)
  console.log(JSON.stringify({allowed: true, error: e.message}))
}
