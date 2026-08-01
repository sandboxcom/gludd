import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent } from "../lib/shared.ts"
import impl from "./impl/enforce_stop_impl.ts"

export default (async () => {
  void isSubagent()
  return impl({})
}) satisfies Plugin
