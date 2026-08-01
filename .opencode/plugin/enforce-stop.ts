import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent } from "../lib/shared.ts"
import impl from "./impl/enforce_stop_impl.ts"

// Source-level policy contract: const DELEGATE_FIRST_THRESHOLD = 8
// The executable constant remains module-private in enforce_stop_impl.ts so
// OpenCode's legacy plugin loader sees only this entrypoint's default export.

export default (async () => {
  void isSubagent()
  return impl({})
}) satisfies Plugin
