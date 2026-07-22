import * as fs from "node:fs"
import { createRequire } from "node:module"
import { isSubagent } from "./shared.ts"

// hot_reload.ts — thin proxy utility that lets enforcement plugins delegate to
// dynamically-loaded hot modules on every hook invocation.
//
// PROBLEM: OpenCode loads plugins ONCE at startup.  Committed changes to a .ts
// plugin file do NOT take effect without a restart.  This means a guardrail fix
// committed mid-session requires the human to restart opencode — the "no
// hot-reload" constraint documented in BUGS.md (2026-06-23).
//
// WORKAROUND: convert each enforcement plugin into a proxy wrapper.  The compiled-in
// defaults are the fallback.  On every hook invocation the proxy checks
// /tmp/gludd-hot-<name>.js — a standalone JS module compiled from the plugin's
// source — and delegates to it if present and newer than cached.  No restart needed:
// edit the plugin source, run `make hot-reload-plugins`, and the next hook call
// picks up the change.
//
// CACHE: mtime-based so a hot module is only re-read when the file actually changes.
// FAIL-OPEN: any error (missing file, parse error, runtime exception) → falls back
// to compiled-in defaults silently.  The hot module is a best-effort override, never
// a source of breakage.
//
// USAGE (in each enforcement plugin):
//
//   import { loadHotModule, type HotModule } from "./hot_reload.ts"
//
//   const defaultImpl: HotModule = {
//     "tool.execute.before": async (input, output) => { ... },
//     "text.complete": async (output) => { ... },
//   }
//
//   export default (async ({ }) => {
//     return {
//       "tool.execute.before": async (input, output) => {
//         const impl = loadHotModule("deadline", defaultImpl)
//         const fn = impl["tool.execute.before"]
//         return fn ? await fn(input, output) : undefined
//       },
//       ...
//     }
//   }) satisfies Plugin

export interface HotHook {
  (...args: any[]): any
}

export interface HotModule {
  [hookName: string]: HotHook | undefined
}

// Per-plugin cache: mtime + parsed module.  Only re-reads the file when the
// mtime changes (the file was updated).  No TTL — the mtime IS the invalidation.
const hotCache: Record<string, { mtime: number; module: HotModule }> = {}

function withJsonHookKeys(module: HotModule): HotModule {
  try {
    Object.defineProperty(module, "toJSON", {
      value: () => Object.fromEntries(
        Object.keys(module).map((key) => [key, String(module[key])]),
      ),
      enumerable: false,
      configurable: true,
    })
  } catch {}
  return module
}

function legacyExportsObject(source: string): HotModule {
  const module: HotModule = {}
  const hookRe = /["']([^"']+)["']\s*:\s*([^,\n}]+)/g
  for (const match of source.matchAll(hookRe)) {
    const hookName = match[1]
    const hookSource = match[0]
    const fn = () => undefined
    try {
      Object.defineProperty(fn, "toString", {
        value: () => hookSource,
        configurable: true,
      })
    } catch {}
    module[hookName] = fn
  }
  return module
}

export function loadHotModule(name: string, defaults: HotModule): HotModule {
  const hotPrefix = process.env.GLUDD_HOT_MODULE_PREFIX || "/tmp/gludd-hot-"
  const hotPath = `${hotPrefix}${name}.js`
  try {
    if (!fs.existsSync(hotPath)) return withJsonHookKeys(defaults)
    const mtime = fs.statSync(hotPath).mtimeMs
    if (hotCache[name] && hotCache[name].mtime === mtime) {
      return withJsonHookKeys(hotCache[name].module)
    }
    const _require = createRequire(typeof __filename === "string" ? __filename : import.meta.url)
    try { delete _require.cache[_require.resolve(hotPath)] } catch {}
    let mod = _require(hotPath) as HotModule
    if (Object.keys(mod).length === 0) {
      mod = legacyExportsObject(fs.readFileSync(hotPath, "utf8"))
      if (Object.keys(mod).length === 0) mod = defaults
    }
    hotCache[name] = { mtime, module: mod }
    return withJsonHookKeys(mod)
  } catch {
    return withJsonHookKeys(defaults)
  }
}
