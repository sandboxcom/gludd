import type { Plugin } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import { createRequire } from "node:module"

// Compatibility entrypoint for tests and older plugin imports. The canonical
// source lives at .opencode/lib/hot_reload.ts; keep this behavior identical.

export interface HotHook {
  (...args: any[]): any
}

export interface HotModule {
  [hookName: string]: HotHook | undefined
}

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
    if (!fs.existsSync(hotPath)) {
      withJsonHookKeys(defaults)
      return defaults
    }
    const mtime = fs.statSync(hotPath).mtimeMs
    if (hotCache[name] && hotCache[name].mtime === mtime) {
      return withJsonHookKeys(hotCache[name].module)
    }
    const _require = createRequire(import.meta.url)
    try { delete _require.cache[_require.resolve(hotPath)] } catch {}
    let mod = _require(hotPath) as HotModule
    if (Object.keys(mod).length === 0) {
      mod = legacyExportsObject(fs.readFileSync(hotPath, "utf8"))
      if (Object.keys(mod).length === 0) mod = defaults
    }
    hotCache[name] = { mtime, module: mod }
    return withJsonHookKeys(mod)
  } catch {
    withJsonHookKeys(defaults)
    return defaults
  }
}

export default (() => ({
  "event": async () => undefined,
})) satisfies Plugin
