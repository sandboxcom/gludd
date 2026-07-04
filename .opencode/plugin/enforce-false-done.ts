import type { Plugin } from "@opencode-ai/plugin"
export default (async () => {
  return {
    event: async ({ event }: { event: { type: string } }) => {
      if (event.type === "session.idle") { }
    },
    "experimental.text.complete": async (_input: unknown, output: { text: string }) => {
      return
    },
  }
}) satisfies Plugin
