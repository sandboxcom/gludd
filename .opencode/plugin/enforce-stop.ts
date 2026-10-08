import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent } from "../lib/shared.ts"
import impl from "./impl/enforce_stop_impl.ts"

// Source-level policy contract: const DELEGATE_FIRST_THRESHOLD = 8
// The executable constant remains module-private in enforce_stop_impl.ts so
// OpenCode's legacy plugin loader sees only this entrypoint's default export.

export default (async () => {
  void isSubagent()
  const hooks = await impl({})
  return {
    "tool.execute.before": hooks["tool.execute.before"],
    "experimental.chat.system.transform": hooks["experimental.chat.system.transform"],
    "experimental.text.complete": hooks["experimental.text.complete"],
    "event": hooks["event"],
  }
}) satisfies Plugin
