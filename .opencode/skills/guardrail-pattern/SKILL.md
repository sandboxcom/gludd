---
name: guardrail-pattern
description: Use when creating or updating any agent policy, restriction, or enforcement rule. Provides the three-layer pattern for making guardrails that actually stick: config permissions, runtime hooks, and agent prompting.
---

# Guardrail Pattern

When introducing any restriction or policy on agent behavior, you MUST implement
all three layers. A single layer is not sufficient.

## The Three Layers

### Layer 1: Config Permission (hard gate)

In `opencode.json`, set `permission` rules that block the action at the
framework level. This is the hard wall the agent cannot bypass.

```json
{
  "permission": {
    "bash": {
      "make *": "allow",
      "*": "deny"
    }
  }
}
```

### Layer 2: Runtime Hook (contextual feedback)

Create or update a plugin in `.opencode/plugin/` that intercepts the denied
action and throws an error with a **helpful, actionable message** explaining:

- What was blocked and why
- What to do instead (specific, concrete steps)
- Where to find more info (reference AGENTS.md or docs)

The hook must NOT silently block. It must explain. The agent cannot learn from
a silent denial.

```typescript
// .opencode/plugin/enforce-make.ts
import type { Plugin } from "@opencode-ai/plugin"

export default (async ({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "bash") return
      const command = output?.args?.command ?? ""
      const trimmed = typeof command === "string" ? command.trim() : ""
      if (!trimmed.startsWith("make ") && trimmed !== "make") {
        throw new Error([
          "BLOCKED: Direct bash commands are not allowed in this project.",
          "",
          "Rule: You MUST only run `make <target>` commands.",
          "",
          "What to do instead:",
          "  1. Add or update a target in the Makefile",
          "  2. Run `make <targetname>`",
          "",
          "See AGENTS.md for existing targets and the full policy.",
        ].join("\n"))
      }
    },
  }
}) satisfies Plugin
```

Register the plugin in `opencode.json`:

```json
{
  "plugin": ["./.opencode/plugin/enforce-make.ts"]
}
```

### Layer 3: Agent Prompting (proactive guidance)

In `AGENTS.md`, add a prominent section that tells the agent the rule BEFORE it
tries to violate it. This is the prophylactic layer.

```markdown
## CRITICAL: Bash Command Policy

**You MUST only run `make <target>` commands in bash. Never run any other command directly.**

- ALLOWED: `make test`, `make lint`, `make init`, `make sync`, etc.
- DENIED: `uv run ...`, `python3 ...`, `pip install ...`, `git ...`, `which ...`,
  `ls ...`, `cat ...`, `find ...`, `rm ...`, or any other direct command.

If you need to do something at the command line, add or update a Makefile target
first, then run `make <target>`.

When you have working Python you can dogfood, migrate make targets into that system.
```

## Checklist for Every Guardrail

When adding ANY new guardrail, verify all three:

- [ ] `opencode.json` permission rule exists that hard-blocks the action
- [ ] `.opencode/plugin/` has a hook that catches the violation and returns a
      helpful error message with concrete next steps
- [ ] `AGENTS.md` has a prominent section explaining the rule proactively
- [ ] The plugin is registered in `opencode.json` `plugin` array
- [ ] All three layers reference each other (plugin mentions AGENTS.md,
      AGENTS.md mentions the make target pattern)

## Meta-Guardrail

This skill itself is a guardrail about guardrails. When you catch yourself
adding a restriction with only one or two layers, stop and add the missing
layers before continuing.
