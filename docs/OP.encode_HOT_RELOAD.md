## Hot-Reload Proxy Pattern Architecture

**Problem:** OpenCode loads and compiles plugins ONCE at startup. Editing `.opencode/plugin/*.ts` does not take effect without restarting opencode. This means a guardrail fix committed mid-session is invisible until the human intervenes.

**Solution:** Each enforcement plugin is a thin proxy that delegates hook calls to a standalone JS module at `/tmp/gludd-hot-<plugin>.js`, re-read whenever its mtime changes, falling back to compiled-in defaults on any failure.

### 1. Architecture (three layers)

```
┌─────────────────────────────────────────────────────┐
│ Plugin .ts (compiled once at opencode startup)      │
│ ┌─────────────────────────────────────────────────┐ │
│ │ export default ({ }) => {                       │ │
│ │   return {                                      │ │
│ │     "tool.execute.before": async (input, out) =>│ │
│ │       loadHotModule("floor", defaultImpl)       │ │  ← proxy
│ │       ["tool.execute.before"](input, out),      │ │
│ │   }                                             │ │
│ │ }                                               │ │
│ └─────────────────────────────────────────────────┘ │
│          │ loadHotModule() checks mtime             │
│          ▼                                          │
│ ┌─────────────────────────────────────────────────┐ │
│ │ /tmp/gludd-hot-floor.js  (dynamic, re-read on   │ │
│ │   mtime change — no restart needed)              │ │
│ └─────────────────────────────────────────────────┘ │
│          │ if missing/broken → fallback              │
│          ▼                                          │
│ ┌─────────────────────────────────────────────────┐ │
│ │ defaultImpl (compiled-in, always available)      │ │
│ └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

**Proxy wrapper** (in each enforcement plugin, e.g. `enforce-floor.ts:36`):

```typescript
import { loadHotModule } from "./hot_reload.ts"

const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => { /* compiled-in logic */ },
  "text.complete":      async (output) => { /* compiled-in logic */ },
}

export default ({ }) => ({
  "tool.execute.before": async (input, output) => {
    return (await loadHotModule("floor", defaultImpl))["tool.execute.before"](input, output)
  },
  // ...
})
```

**Hot-reload engine** (`hot_reload.ts`):

| Property | Behavior |
|----------|----------|
| Lookup path | `/tmp/gludd-hot-<name>.js` |
| Invalidation | `mtime`-based per-plugin cache — only re-reads when file changed |
| Fallback | Silent fail-open — any error (missing file, parse error, runtime exception) returns `defaultImpl` |
| Module loading | `createRequire()` after deleting the exact module-cache entry, so CommonJS hook exports are re-evaluated after an mtime change |

### 2. Building hot modules

```
make hot-reload-plugins
```

This runs `scripts/build_hot_modules.js`, which:

1. Reads each `.opencode/plugin/enforce-*.ts` source file
2. **Transpiles TypeScript:** uses esbuild's TypeScript parser and keeps the small fallback transformer only for environments where esbuild cannot transform the source
3. **Uses the proxy's real lookup name:** derives the output filename from the
   single literal passed to `loadHotModule()`. A source file named
   `enforce-verified-claims.ts` therefore publishes
   `/tmp/gludd-hot-verified-claims.js`, exactly where its proxy looks.
4. **Preserves local helper imports:** transpiles the dedicated
   `.opencode/lib/plugin_test_exports.ts` helper module into an isolated scope
   when a fallback imports it, so generated hooks cannot fail open with an
   undefined classifier.
5. **Transforms to CommonJS:** rewrites Node imports into runtime-compatible
   `require()` calls.
6. **Preserves hook methods:** exports each function directly from the transpiled `defaultImpl` runtime object; it does not reconstruct function bodies with regular expressions
7. **Validates before publish:** parses and loads a namespaced candidate, rejects zero-hook or invalid modules, then atomically renames the candidate to `/tmp/gludd-hot-*.js`

Only plugins with a `defaultImpl` object—directly or in their thin proxy's
implementation module—produce hot modules. Plugins without the proxy pattern
are reported as skipped and continue to use their compiled-in behavior.

### 3. Verifying hot modules

```
make hot-reload-status          # lists all /tmp/gludd-hot-*.js with mtime age + size
make check-hot-reload-fresh     # exits 1 if any hot module is stale or broken
```

`check-hot-reload-fresh` (`scripts/check_hot_reload_fresh.py`) checks per plugin:
- Hot module exists at `/tmp/gludd-hot-<name>.js`
- Hot module mtime >= source `.ts` mtime (not stale)
- Node's JavaScript parser accepts the complete module
- No bare ESM exports/imports or captured `ReferenceError` output remains
- The checked artifact name is the proxy's `loadHotModule()` name rather than
  an independently inferred source filename

The parser check matters because esbuild legitimately emits expressions such as
`fn ? await fn(...) : void 0`. A previous regex interpreted `: void` as a
TypeScript return annotation, falsely rejecting ten valid modules. Syntax is
now decided by the JavaScript parser instead of an ambiguous token pattern.

### 4. Limitations

| Limitation | Detail |
|------------|--------|
| **New plugin registration requires restart** | If a NEW `.ts` file is added to `.opencode/plugin/`, opencode must be restarted for it to be compiled into the runtime. The hot-reload mechanism overrides hook *behavior*, not the plugin *registry*. |
| **Only `defaultImpl`-pattern plugins hot-reload** | Plugins that lack a `defaultImpl` object are not extracted by `build_hot_modules.js` and cannot be hot-reloaded. |
| **`/tmp`-resident** | Hot modules live in `/tmp` — they survive reboots only if the OS preserves `/tmp`. Use `make hot-reload-plugins` after a reboot. |
| **Hook signature changes require restart** | If you add a new hook to `defaultImpl` (e.g. a new `"session.idle"` handler), the compiled-in proxy wrapper must also be updated to call `loadHotModule()[newHook]`. That wrapper change requires an opencode restart. |
| **`make restart-opencode` is the only activation path for proxy-wrapper changes** | Per `Makefile:3534`: "Plugin .ts edits do NOT hot-reload. OpenCode compiles plugins once at startup." Hot-reload covers hook *body* changes, not hook *registration* changes. |

### 5. Upstream user reports and design evidence

- OpenCode users continue to report lifecycle cases where custom tooling works
  only after a restart. In
  [anomalyco/opencode#13887](https://github.com/anomalyco/opencode/issues/13887),
  a first-start dependency installation leaves custom tools unusable until the
  second process. This supports keeping the restart warning explicit and
  testing a genuinely fresh TUI process; an in-process proxy cannot repair
  plugin registration or dependency setup performed at startup.
- esbuild's maintainer explains in
  [evanw/esbuild#101](https://github.com/evanw/esbuild/issues/101) that
  TypeScript parsing is ambiguous enough to require a real parser and
  backtracking. That is why Gludd no longer attempts to find the end of
  `defaultImpl` or reconstruct hook bodies with brace/regex heuristics.
- The long-running type-import discussion in
  [evanw/esbuild#1525](https://github.com/evanw/esbuild/issues/1525) shows that
  even parser-backed transpilation has runtime-import edge cases. Gludd
  therefore carries required local runtime helpers into the generated module
  and validates the exact CommonJS candidate with Node before atomically
  publishing it.

### 6. The stale backup problem (`make restore-opencode`)

`make restore-opencode` (`Makefile:3909`) restores `.opencode/` from `.opencode.orig/` (created by `make backup-opencode`). This exists as a recovery mechanism when opencode's cache is corrupted (`~/.cache/opencode`).

**The problem:** `.opencode.orig/` may contain an older snapshot of `.opencode/plugin/*.ts` — one that was taken BEFORE plugins were converted to the proxy pattern, or before `defaultImpl` hook bodies were updated. After `restore-opencode`:

- Plugin `.ts` files revert to the backup version (no proxy wrapper, or stale hook logic)
- Hot modules at `/tmp/gludd-hot-*.js` are NOT touched — they remain as-is
- The compiled-in defaults (from the restored older `.ts`) may be stale
- `make check-hot-reload-fresh` will report hot modules as stale because the restored `.ts` source has an older mtime than the hot module

**Mitigation:**
- Run `make backup-opencode` before every long session so `.opencode.orig/` is current
- Run `make check-opencode-backup` to verify the backup is fresh (exits 1 if older than `.opencode/`)
- After `make restore-opencode`, immediately run `make hot-reload-plugins` to regenerate hot modules from the restored (possibly older) source
- Consider `.opencode.orig/` a "last known good" recovery point, not a live development mirror

### 7. Reference: files and make targets

| Artifact | Path | Purpose |
|----------|------|---------|
| Proxy utility | `.opencode/lib/hot_reload.ts` | `loadHotModule()` — mtime-based cache, fail-open delegation |
| Build script | `scripts/build_hot_modules.js` | TS→JS transpile + validated direct hook export |
| Freshness check | `scripts/check_hot_reload_fresh.py` | Validates hot modules are current and valid JS |
| Hot modules | `/tmp/gludd-hot-*.js` | Runtime overrides (one file per proxy-pattern plugin) |

| Target | Purpose |
|--------|---------|
| `make hot-reload-plugins` | Build all hot modules |
| `make hot-reload-status` | Show per-module age + size |
| `make hot-reload-clean` | Remove all `/tmp/gludd-hot-*.js` |
| `make check-hot-reload-fresh` | Gate: verify freshness (exits 1 on stale) |
| `make backup-opencode` | Snapshot `.opencode/` → `.opencode.orig/` |
| `make restore-opencode` | Restore `.opencode/` from backup (wipes hot-reload source) |
| `make restart-opencode` | Print restart procedure for plugin registration changes |
