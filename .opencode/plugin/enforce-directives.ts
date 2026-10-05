import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent } from "../lib/shared.ts"
import impl from "./impl/enforce_directives_impl.ts"

export default (async () => {
  if (isSubagent()) return {}
  const hooks = await impl({})
  return {
    "experimental.chat.messages.transform": hooks["experimental.chat.messages.transform"],
    "tool.execute.before": hooks["tool.execute.before"],
    "experimental.text.complete": hooks["experimental.text.complete"],
  }
}) satisfies Plugin
