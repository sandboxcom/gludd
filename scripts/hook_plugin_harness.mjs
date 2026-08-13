#!/usr/bin/env node
// hook_plugin_harness.mjs — CI-runnable liveness harness for .opencode/plugin/*.ts
// hooks (Wave E of the hook-liveness spec).
//
// Every plugin under .opencode/plugin/*.ts and .opencode/plugins/*.ts has ONLY
// a type-only `import type { Plugin } from "@opencode-ai/plugin"` (or
// "@opencode/core") at the top — no runtime dependency — so Node's native
// TypeScript type-stripping (`--experimental-strip-types`, Node >= 22.6) can
// import them directly with zero npm install.
//
// This harness loads exactly one plugin file, resolves its exported Plugin
// (which is EITHER a factory function `(ctx) => Promise<PluginHooks>` used by
// most plugins in this repo, OR a plain object `{ name, version, hooks }` as
// used by enforce-deletion-gate.ts), invokes one named hook with JSON-decoded
// input/output arguments, and prints the final hook return value as JSON (or
// the literal `null` if the hook returned undefined/void). An optional repeat
// count exercises multiple calls in the same loaded plugin process.
//
// Usage:
//   node --experimental-strip-types scripts/hook_plugin_harness.mjs \
//     <plugin-path> <hook-name> <input-json> <output-json> [repeat-count]
//
// <plugin-path>   absolute, or resolved relative to CWD.
// <hook-name>     e.g. "tool.execute.before", "tool.execute.after", "event",
//                 "experimental.text.complete", "experimental.chat.system.transform".
// <input-json>    JSON object passed as the hook's first positional arg.
// <output-json>   JSON object passed as the hook's second positional arg.
//                 (Optional — handlers that only destructure their first
//                 argument, e.g. enforce-deletion-gate.ts, simply ignore it.)
// [repeat-count]  Positive integer; defaults to 1. The plugin is loaded once,
//                 then the same hook/input/output are invoked this many times.
//
// Exit codes:
//   0   hook invoked successfully; stdout is the JSON-encoded return value.
//   1   the hook itself threw (this IS how several plugins implement a
//       "deny" — e.g. enforce-stop.ts's stop-like-tool block, enforce-
//       delegate.ts's force-delegate block, enforce-deletion-gate.ts's
//       threshold-exceeded block). stderr carries the thrown message.
//   2   usage error (missing required argv).
//   3   the named hook does not exist on the resolved plugin.

import { pathToFileURL } from "node:url";
import path from "node:path";

async function main() {
  const [pluginPathArg, hookName, inputJson, outputJson, repeatArg] =
    process.argv.slice(2);
  if (!pluginPathArg || !hookName) {
    console.error(
      "Usage: hook_plugin_harness.mjs <plugin-path> <hook-name> <input-json> <output-json> [repeat-count]",
    );
    process.exit(2);
  }

  const absPath = path.isAbsolute(pluginPathArg)
    ? pluginPathArg
    : path.resolve(process.cwd(), pluginPathArg);

  let input;
  let output;
  try {
    input = inputJson ? JSON.parse(inputJson) : {};
  } catch (e) {
    console.error(`Invalid <input-json>: ${e}`);
    process.exit(2);
  }
  try {
    output = outputJson ? JSON.parse(outputJson) : {};
  } catch (e) {
    console.error(`Invalid <output-json>: ${e}`);
    process.exit(2);
  }
  const repeatCount = repeatArg === undefined ? 1 : Number.parseInt(repeatArg, 10);
  if (!Number.isSafeInteger(repeatCount) || repeatCount < 1 || repeatCount > 1000) {
    console.error("Invalid [repeat-count]: expected an integer from 1 through 1000");
    process.exit(2);
  }

  const mod = await import(pathToFileURL(absPath).href);
  let plugin = mod.default;
  // Most plugins export a factory: `export default (async (ctx) => ({ ... })) satisfies Plugin`.
  // enforce-deletion-gate.ts exports a plain object: `const plugin: Plugin = { hooks: {...} }`.
  if (typeof plugin === "function") {
    plugin = await plugin({});
  }
  const hooks = plugin && plugin.hooks ? plugin.hooks : plugin;

  if (!hooks || typeof hooks[hookName] !== "function") {
    const available = hooks ? Object.keys(hooks).join(", ") : "(none)";
    console.error(
      `Hook '${hookName}' not found on plugin ${absPath}. Available hooks: ${available}`,
    );
    process.exit(3);
  }

  let result;
  for (let invocation = 0; invocation < repeatCount; invocation += 1) {
    result = await hooks[hookName](input, output);
  }
  process.stdout.write(JSON.stringify(result === undefined ? null : result));
}

main().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
