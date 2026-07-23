// verify-plugins.mjs — Comprehensive .opencode/ plugin e2e verification.
// Run: node --experimental-strip-types .opencode/scripts/verify-plugins.mjs
// Tests: (1) every .ts/.js file in plugin/ + plugins/ loads without errors,
// (2) every file exports a valid plugin factory (old or new API),
// (3) factories produce valid hooks,
// (4) hooks don't crash abnormally when invoked with typical inputs,
// (5) no dangerous non-plugin files in plugin directories,
// (6) opencode.json consistency checks.
// Exits 0 on pass, 1 on failure. Outputs JSON results.

import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = path.resolve(__dirname, "../..")
const PLUGIN_DIR = path.join(PROJECT_ROOT, ".opencode/plugin")
const PLUGINS_DIR = path.join(PROJECT_ROOT, ".opencode/plugins")
const LIB_DIR = path.join(PROJECT_ROOT, ".opencode/lib")
const OPENCODE_JSON = path.join(PROJECT_ROOT, "opencode.json")

const results = { pass: true, tests: [], failures: [], summaries: {} }

function fail(test, msg) {
  results.pass = false
  results.failures.push({ test, message: msg })
}

function warn(test, msg) {
  results.tests.push({ test, detail: msg, severity: "warning" })
}

function pass(test, detail) {
  results.tests.push({ test, detail })
}

function info(key, data) {
  results.summaries[key] = data
}

// ── Mock PluginInput for new API ──────────────────────────────────────────

function makePluginInput() {
  return {
    client: {
      execute: async (cmd) => {
        if (cmd?.includes?.("git status --porcelain")) return { stdout: "", stderr: "", ok: true }
        if (cmd?.includes?.("git diff")) return { stdout: "", stderr: "", ok: true }
        if (cmd?.includes?.("make ci-verdict")) return { stdout: "conclusion: success\nheadSha master: abcdef1", stderr: "", ok: true }
        if (cmd?.includes?.("ci-verdict-safe")) return { stdout: "conclusion: success", stderr: "", ok: true }
        if (cmd?.includes?.("gh release view")) return { stdout: '{"isDraft":false,"assets":[]}', stderr: "", ok: true }
        return { stdout: "", stderr: "", ok: true }
      },
      notify: () => {},
      read: async (p) => {
        if (p?.includes?.("TASKS.md")) return "- [x] All done\n"
        if (p?.includes?.("config/ratchet.yml")) return "# empty\n"
        return ""
      },
      write: async () => {},
    },
    project: { path: PROJECT_ROOT },
    directory: PROJECT_ROOT,
    $: {},
  }
}

// ── Mock Plugin API for old API (enforce-commit-lock.ts pattern) ──────────

function makeOldApi() {
  const hooks = {}
  const api = {
    tool: { execute: { before: (fn) => { hooks["tool.execute.before"] = fn }, after: (fn) => { hooks["tool.execute.after"] = fn } } },
    experimental: { chat: { system: { transform: (fn) => { hooks["experimental.chat.system.transform"] = fn } } }, text: { complete: (fn) => { hooks["experimental.text.complete"] = fn } }, session: { compacting: (fn) => { hooks["experimental.session.compacting"] = fn } } },
    event: (fn) => { hooks["event"] = fn },
    config: (fn) => { hooks["config"] = fn },
    shell: { env: (fn) => { hooks["shell.env"] = fn } },
  }
  return { api, hooks }
}

// ── Test 1: Plugin directory inventory ────────────────────────────────────

function testPluginDirectory() {
  for (const dir of [PLUGIN_DIR, PLUGINS_DIR]) {
    if (!fs.existsSync(dir)) {
      fail("dir-exists", `${path.relative(PROJECT_ROOT, dir)} does not exist`)
      continue
    }
    const files = fs.readdirSync(dir)
    const tsFiles = files.filter(f => f.endsWith(".ts"))
    const nonSource = files.filter(f => {
      const ext = path.extname(f)
      return ext !== ".ts" && ext !== ".mjs" && ext !== ".js"
    })
    const mjsFiles = files.filter(f => f.endsWith(".mjs"))

    const dirLabel = path.relative(PROJECT_ROOT, dir)

    // Underscore-prefixed .ts files: companion files that WILL crash opencode
    for (const f of tsFiles) {
      if (f.startsWith("_")) {
        fail(`dangerous-file-${dirLabel}/${f}`, `Underscore-prefixed .ts file in plugin dir. opencode WILL auto-discover and crash on this as a plugin.`)
      }
      if (f.includes(".orig.") || f.includes(".backup.") || f.includes(".bak.") || f.includes("_exports")) {
        fail(`dangerous-file-${dirLabel}/${f}`, `Non-plugin file in plugin dir — WILL crash opencode auto-discovery.`)
      }
    }

    // Non-.ts/.mjs files shouldn't be in plugin dirs
    for (const f of nonSource) {
      if (f !== "node_modules" && !f.startsWith(".")) {
        warn(`non-source-static-${dirLabel}/${f}`, `Non-source file '${f}' in plugin directory — may interfere with auto-discovery`)
      }
    }

    // .mjs test files: flag but don't hard-fail (they're needed for runtime tests)
    for (const f of mjsFiles) {
      warn(`test-file-${dirLabel}/${f}`, `.mjs test file in plugin dir — verify opencode does not auto-discover .mjs files`)
    }

    pass(`dir-inventory-${dirLabel}`, `${tsFiles.length} .ts plugins, ${mjsFiles.length} .mjs test files, ${nonSource.length} other`)
    info(`${dirLabel}-ts-count`, tsFiles.length)
  }
}

// ── Test 2: Plugin load test — import every .ts/.js file ──────────────────

async function testPluginLoads() {
  const dirs = []
  if (fs.existsSync(PLUGIN_DIR)) dirs.push({ dir: PLUGIN_DIR, label: ".opencode/plugin" })
  if (fs.existsSync(PLUGINS_DIR)) dirs.push({ dir: PLUGINS_DIR, label: ".opencode/plugins" })

  // Read registered plugins from opencode.json
  const registeredPaths = new Set()
  if (fs.existsSync(OPENCODE_JSON)) {
    try {
      const cfg = JSON.parse(fs.readFileSync(OPENCODE_JSON, "utf-8"))
      if (cfg.plugin && Array.isArray(cfg.plugin)) {
        for (const entry of cfg.plugin) {
          const p = typeof entry === "string" ? entry : (Array.isArray(entry) ? entry[0] : null)
          if (p && p.startsWith("./")) registeredPaths.add(path.resolve(PROJECT_ROOT, p))
        }
      }
    } catch (e) {
      fail("opencode-json-parse", `Cannot parse opencode.json: ${e.message}`)
      return
    }
  }

  const allLoaded = []
  const allFailed = []

  for (const { dir, label } of dirs) {
    const files = fs.readdirSync(dir).filter(f => f.endsWith(".ts") || f.endsWith(".js"))
    if (files.length === 0) {
      fail(`no-plugin-files-${label}`, `No .ts or .js files in ${label}`)
      continue
    }

    for (const file of files) {
      const absPath = path.join(dir, file)
      const relPath = `${label}/${file}`
      const isRegistered = registeredPaths.has(absPath)

      try {
        const mod = await import(absPath)
        const factory = mod.default || mod

        if (typeof factory !== "function") {
          fail(`plugin-factory-${relPath}`, `Default export is ${typeof factory}. This WILL crash opencode auto-discovery.`)
          allFailed.push({ file: relPath, reason: `default export is ${typeof factory}, not a function` })
          continue
        }

        // Try NEW API first: factory(PluginInput) → Hooks
        let hooks = null
        let usedOldApi = false

        try {
          const result = await Promise.resolve(factory(makePluginInput()))
          if (result && typeof result === "object" && !Array.isArray(result)) {
            const fnKeys = Object.keys(result).filter(k => typeof result[k] === "function")
            if (fnKeys.length > 0) {
              hooks = result
            }
          }
        } catch (_newApiErr) {
          // Factory may use old API: factory(PluginAPI) → void
        }

        // Fall back to OLD API: factory(PluginAPI) → void, hooks registered on api
        if (!hooks) {
          try {
            const { api: mockApi, hooks: registeredHooks } = makeOldApi()
            await Promise.resolve(factory(mockApi))
            const fnKeys = Object.keys(registeredHooks).filter(k => typeof registeredHooks[k] === "function")
            if (fnKeys.length > 0) {
              hooks = registeredHooks
              usedOldApi = true
            }
          } catch (oldApiErr) {
            fail(`plugin-factory-call-${relPath}`, `Both APIs failed. New API: creates hooks object. Old API: registers on api. Error: ${oldApiErr.message.split('\n')[0]}`)
            allFailed.push({ file: relPath, reason: `factory threw: ${oldApiErr.message}` })
            continue
          }
        }

        if (!hooks || Object.keys(hooks).filter(k => typeof hooks[k] === "function").length === 0) {
          fail(`plugin-no-hooks-${relPath}`, `Factory produced no hooks (newApi:${!usedOldApi})`)
          allFailed.push({ file: relPath, reason: "no hooks produced" })
          continue
        }

        const hookNames = Object.keys(hooks).filter(k => typeof hooks[k] === "function")
        allLoaded.push({
          file: relPath,
          hooks: hookNames,
          registered: isRegistered,
          oldApi: usedOldApi,
          warning: !isRegistered ? "NOT registered in opencode.json — auto-discovered" : null
        })

        pass(`plugin-load-${relPath}`, `${hookNames.length} hooks [${usedOldApi ? "old api" : "new api"}]: ${hookNames.join(", ")}${isRegistered ? "" : " [auto-discovered]"}`)

      } catch (loadError) {
        fail(`plugin-import-${relPath}`, `Import failed: ${loadError.message.split('\n')[0]}`)
        allFailed.push({ file: relPath, reason: `import failed: ${loadError.message}` })
      }
    }
  }

  info("loaded-plugins", allLoaded)
  info("failed-plugins", allFailed)

  if (allFailed.length > 0) {
    fail("plugin-load-summary", `${allFailed.length} plugin(s) failed to load. opencode WILL crash on these.`)
  } else {
    pass("plugin-load-summary", `All ${allLoaded.length} plugins loaded successfully`)
  }

  return allLoaded
}

// ── Test 3: Hook invocation — verify hooks don't crash ────────────────────

async function testHookInvocations(loadedPlugins) {
  for (const { file, hooks } of loadedPlugins) {
    const absPath = path.join(PROJECT_ROOT, file)
    const relPath = file

    try {
      const mod = await import(absPath)
      const factory = mod.default || mod
      let hooksObj = null

      // New API
      try {
        const result = await Promise.resolve(factory(makePluginInput()))
        if (result && typeof result === "object" && !Array.isArray(result)) {
          hooksObj = result
        }
      } catch (_) {}

      // Old API fallback
      if (!hooksObj) {
        try {
          const { api: mockApi, hooks: registered } = makeOldApi()
          await Promise.resolve(factory(mockApi))
          hooksObj = registered
        } catch (_) {}
      }

      if (!hooksObj) continue

      // ── tool.execute.before ──
      const execBefore = hooksObj["tool.execute.before"]
      if (typeof execBefore === "function") {
        try {
          const input = { tool_call: { tool: "edit", tool_input: { path: `${PROJECT_ROOT}/test.py`, new_text: "# test" } } }
          const output = { args: { path: `${PROJECT_ROOT}/test.py`, new_text: "# test" } }
          const result = execBefore.length >= 2 ? await execBefore(input, output) : await execBefore(input)
          if (result && typeof result.permissionDecision === "string") {
            pass(`hook-execute-before-${relPath}`, `enforced: ${result.permissionDecision}`)
          } else {
            pass(`hook-execute-before-${relPath}`, "ok")
          }
        } catch (hookErr) {
          const msg = hookErr.message || ""
          // Expected enforcement throws (not real crashes)
          if (msg.includes("SESSION START PROTOCOL") || msg.includes("MULTITASKING") ||
              msg.includes("permissionDecision") || msg.includes("DISPATCH") || msg.includes("AGENTS.md")) {
            pass(`hook-execute-before-${relPath}`, `enforced (throw): ${msg.split('\n')[0].substring(0, 80)}`)
          } else if (msg.includes("Cannot read properties") || msg.includes("is not a function") || msg.includes("undefined")) {
            fail(`hook-execute-before-${relPath}`, `CRASH: ${msg.split('\n')[0]}`)
          } else {
            warn(`hook-execute-before-${relPath}`, `unexpected throw: ${msg.split('\n')[0]}`)
          }
        }
      }

      // ── experimental.text.complete ──
      const textComplete = hooksObj["experimental.text.complete"]
      if (typeof textComplete === "function") {
        try {
          const input = { text: "test message" }
          const output = { text: "test message" }
          textComplete.length >= 2 ? await textComplete(input, output) : await textComplete(input)
          pass(`hook-text-complete-${relPath}`, "ok")
        } catch (hookErr) {
          const msg = hookErr.message || ""
          if (msg.includes("Cannot read properties") || msg.includes("is not a function")) {
            fail(`hook-text-complete-${relPath}`, `CRASH: ${msg.split('\n')[0]}`)
          } else {
            pass(`hook-text-complete-${relPath}`, `expected behavior: ${msg.split('\n')[0].substring(0, 80)}`)
          }
        }
      }

      // ── event ──
      const eventHook = hooksObj["event"]
      if (typeof eventHook === "function") {
        try { await eventHook({ type: "test", data: {} }); pass(`hook-event-${relPath}`, "ok") }
        catch (e) { fail(`hook-event-${relPath}`, `CRASH: ${e.message.split('\n')[0]}`) }
      }

      // ── config ──
      const configHook = hooksObj["config"]
      if (typeof configHook === "function") {
        try { await configHook({}); pass(`hook-config-${relPath}`, "ok") }
        catch (e) { fail(`hook-config-${relPath}`, `CRASH: ${e.message.split('\n')[0]}`) }
      }

      // ── chat.message ──
      const chatMsg = hooksObj["chat.message"]
      if (typeof chatMsg === "function") {
        try { await chatMsg({ role: "user", content: "test" }); pass(`hook-chat-message-${relPath}`, "ok") }
        catch (e) { fail(`hook-chat-message-${relPath}`, `CRASH: ${e.message.split('\n')[0]}`) }
      }

      // ── experimental.chat.system.transform ──
      const sysTransform = hooksObj["experimental.chat.system.transform"]
      if (typeof sysTransform === "function") {
        try { const r = await sysTransform({ system: "test" }); pass(`hook-system-transform-${relPath}`, r ? "modified" : "ok") }
        catch (e) { fail(`hook-system-transform-${relPath}`, `CRASH: ${e.message.split('\n')[0]}`) }
      }

    } catch (_e) { /* already counted in load test */ }
  }
}

// ── Test 4: Source hygiene — guards + Node v26 compat ─────────────────────

function testSourceHygiene() {
  const dirs = []
  if (fs.existsSync(PLUGIN_DIR)) dirs.push({ dir: PLUGIN_DIR, label: ".opencode/plugin", isPlugin: true })
  if (fs.existsSync(PLUGINS_DIR)) dirs.push({ dir: PLUGINS_DIR, label: ".opencode/plugins", isPlugin: true })
  if (fs.existsSync(LIB_DIR)) dirs.push({ dir: LIB_DIR, label: ".opencode/lib", isPlugin: false })

  let missingGuardCount = 0
  let withGuardCount = 0

  for (const { dir, label, isPlugin } of dirs) {
    const files = fs.readdirSync(dir).filter(f => f.endsWith(".ts") || f.endsWith(".js"))

    for (const file of files) {
      const content = fs.readFileSync(path.join(dir, file), "utf-8")
      const relPath = `${label}/${file}`

      // Subagent guard check
      if (content.includes("OPENCODE_SUBAGENT")) {
        withGuardCount++
      } else if (isPlugin && !file.startsWith("_")) {
        missingGuardCount++
        warn(`subagent-guard-${relPath}`, "No OPENCODE_SUBAGENT guard — enforcement may fire inside subagents")
      }

      // try-inside-catch (Node v26 incompatibility)
      if (content.match(/\bcatch\s*(?:\([^)]*\))\s*\{[^}]*\btry\b/s)) {
        fail(`node-v26-incompat-${relPath}`, "try inside catch block — incompatible with Node v26 --experimental-strip-types")
      } else if (content.match(/\bcatch\s*\{[^}]*\btry\b/s)) {
        fail(`node-v26-incompat-${relPath}`, "bare catch containing try — incompatible with Node v26 --experimental-strip-types")
      }

      // type-annotated catch variable
      if (content.match(/\bcatch\s*\(\s*\w+\s*:\s*(?!any\b|unknown\b)\w+/)) {
        fail(`node-v26-incompat-${relPath}`, "Type-annotated catch variable — incompatible with Node v26 --experimental-strip-types")
      }
    }
  }

  info("subagent-guards", { withGuard: withGuardCount, missing: missingGuardCount })
}

// ── Test 5: opencode.json consistency ─────────────────────────────────────

function testOpencodeConfig() {
  if (!fs.existsSync(OPENCODE_JSON)) {
    fail("opencode-json-exists", "opencode.json not found")
    return
  }

  let cfg
  try {
    cfg = JSON.parse(fs.readFileSync(OPENCODE_JSON, "utf-8"))
  } catch (e) {
    fail("opencode-json-parse", `Cannot parse opencode.json: ${e.message}`)
    return
  }

  // $schema
  if (cfg["$schema"] === "https://opencode.ai/config.json") {
    pass("opencode-schema", "$schema present and correct")
  } else {
    fail("opencode-schema", `$schema is ${cfg["$schema"] || "MISSING"} — must be https://opencode.ai/config.json`)
  }

  // Check top-level keys against known schema
  const KNOWN_KEYS = ["$schema", "username", "model", "small_model", "default_agent", "shell", "logLevel",
    "share", "autoupdate", "snapshot", "instructions", "skills", "references", "agent", "command",
    "provider", "disabled_providers", "enabled_providers", "mcp", "plugin", "permission",
    "formatter", "lsp", "experimental", "tool_output", "compaction"]
  const unknownKeys = Object.keys(cfg).filter(k => !KNOWN_KEYS.includes(k))
  if (unknownKeys.length > 0) {
    fail("opencode-unknown-keys", `Unknown top-level keys in opencode.json: ${unknownKeys.join(", ")}. These WILL be rejected by opencode schema validation.`)
  } else {
    pass("opencode-keys", "All top-level keys are known schema keys")
  }

  // Plugin entries point to existing files
  if (!cfg.plugin || !Array.isArray(cfg.plugin)) {
    pass("opencode-plugin", "No explicit plugin array (all auto-discovered)")
  } else {
    let missingCount = 0
    for (const entry of cfg.plugin) {
      const p = typeof entry === "string" ? entry : (Array.isArray(entry) ? entry[0] : null)
      if (!p) continue
      if (p.startsWith("./")) {
        const absPath = path.resolve(PROJECT_ROOT, p)
        if (!fs.existsSync(absPath)) {
          fail("opencode-plugin-missing", `${p} in plugin array does not exist on disk`)
          missingCount++
        }
      }
    }
    if (missingCount === 0) {
      pass("opencode-plugin", `All ${cfg.plugin.length} registered plugins exist on disk`)
    }
  }

  // Auto-discovered files not in plugin array
  if (cfg.plugin && Array.isArray(cfg.plugin)) {
    const registeredAbs = new Set()
    for (const entry of cfg.plugin) {
      const p = typeof entry === "string" ? entry : (Array.isArray(entry) ? entry[0] : null)
      if (p && p.startsWith("./")) registeredAbs.add(path.resolve(PROJECT_ROOT, p))
    }
    for (const dir of [PLUGIN_DIR, PLUGINS_DIR]) {
      if (!fs.existsSync(dir)) continue
      const tsFiles = fs.readdirSync(dir).filter(f => f.endsWith(".ts") || f.endsWith(".js"))
      for (const f of tsFiles) {
        const abs = path.join(dir, f)
        if (!registeredAbs.has(abs)) {
          warn(`auto-discovered-${path.relative(PROJECT_ROOT, dir)}/${f}`,
            `Auto-discovered (not in opencode.json plugin array). Verify exports valid plugin.`)
        }
      }
    }
  }

  // Permission ordering: * deny FIRST, then narrow allow
  if (cfg.permission && cfg.permission.bash) {
    const bashPerms = cfg.permission.bash
    if (typeof bashPerms === "object") {
      const keys = Object.keys(bashPerms)
      const denyIdx = keys.findIndex(k => k === "*")
      const allowIdx = keys.findIndex(k => k.startsWith("make"))
      if (denyIdx !== -1 && allowIdx !== -1 && denyIdx < allowIdx) {
        pass("opencode-bash-perm-order", "* deny before make * allow (correct)")
      } else if (denyIdx !== -1 && allowIdx !== -1 && denyIdx > allowIdx) {
        fail("opencode-bash-perm-order", "* deny AFTER make * allow — make commands will be denied (last-match-wins)")
      }
    }
  }
}

// ── Test 6: Library dependency integrity ─────────────────────────────────

async function testLibraries() {
  if (!fs.existsSync(LIB_DIR)) {
    warn("lib-dir", ".opencode/lib does not exist")
    return
  }

  const libFiles = fs.readdirSync(LIB_DIR).filter(f => f.endsWith(".ts") || f.endsWith(".js"))
  for (const file of libFiles) {
    const absPath = path.join(LIB_DIR, file)
    try {
      const mod = await import(absPath)
      const exported = Object.keys(mod).filter(k => k !== "default")
      pass(`lib-import-${file}`, `imported OK${exported.length ? ` (${exported.length} exports)` : ""}`)
    } catch (e) {
      fail(`lib-import-${file}`, `Import failed: ${e.message.split('\n')[0]}`)
    }
  }

  // Verify hot_reload.ts (in plugin dir) is importable by other plugins
  const hotReload = path.join(PLUGIN_DIR, "hot_reload.ts")
  if (fs.existsSync(hotReload)) {
    try {
      const mod = await import(hotReload)
      pass(`hot-reload-import`, "hot_reload.ts importable from plugin dir")
      info("hot-reload-exports", Object.keys(mod).filter(k => k !== "default"))
    } catch (e) {
      fail("hot-reload-import", `hot_reload.ts import failed: ${e.message.split('\n')[0]}`)
    }
  }
}

// ── Test 7: Plugin hashes integrity ───────────────────────────────────────

function testPluginHashes() {
  const hashesPath = path.join(PROJECT_ROOT, ".opencode/plugin-hashes.json")
  if (!fs.existsSync(hashesPath)) {
    return // optional, not a failure
  }
  try {
    const hashes = JSON.parse(fs.readFileSync(hashesPath, "utf-8"))
    info("plugin-hashes-count", Object.keys(hashes).length)
    pass("plugin-hashes", `${Object.keys(hashes).length} hashes recorded`)
  } catch (e) {
    fail("plugin-hashes", `Cannot parse plugin-hashes.json: ${e.message}`)
  }
}

// ── Main ──────────────────────────────────────────────────────────────────

async function main() {
  console.error("=== .opencode/ Plugin E2E Verification ===\n")

  // Phase 1: Static checks (synchronous)
  testPluginDirectory()
  testSourceHygiene()
  testOpencodeConfig()
  testPluginHashes()

  // Phase 2: Dynamic checks (async — actual imports)
  await testLibraries()
  const loaded = await testPluginLoads()

  // Phase 3: Hook invocation (async)
  await testHookInvocations(loaded)

  // ── Final report ──
  const warnings = results.tests.filter(t => t.severity === "warning").length
  const infoCount = results.tests.filter(t => !t.severity || t.severity === "info").length
  const pureWarn = results.tests.filter(t => t.severity === "warning").length

  console.error(`\n=== Results: ${results.tests.length} checks ===`)
  console.error(`  Passes: ${results.tests.filter(t => !t.severity).length}`)
  console.error(`  Warnings: ${pureWarn}`)
  console.error(`  Failures: ${results.failures.length}`)

  if (!results.pass) {
    console.error("\n--- FAILURES ---")
    for (const f of results.failures) {
      console.error(`  [FAIL] ${f.test}: ${f.message}`)
    }
    console.error(`\n${results.failures.length} HARD FAILURES — opencode WILL crash with this .opencode/ directory.`)
  } else {
    console.error("\nALL CHECKS PASSED — .opencode/ is safe to load.")
  }

  console.log(JSON.stringify(results, null, 2))
  process.exit(results.pass ? 0 : 1)
}

main().catch(e => {
  console.error("FATAL:", e.message)
  console.log(JSON.stringify({ pass: false, failures: [{ test: "fatal", message: e.message }] }))
  process.exit(1)
})
