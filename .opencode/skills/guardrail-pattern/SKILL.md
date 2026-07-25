---
name: guardrail-pattern
description: "Use when creating or updating any agent policy, restriction, or enforcement rule. Provides the three-layer pattern for making guardrails that actually stick: config permissions, runtime hooks, and agent prompting."
---

# Guardrail Pattern

When introducing any restriction or policy on agent behavior, you MUST implement
all three layers. A single layer is not sufficient.

## The Three Layers

```
┌─────────────────────────────────────────────────┐
│ Layer 1: Config Permission (opencode.json)       │
│ Hard gate — framework denies the action outright  │
├─────────────────────────────────────────────────┤
│ Layer 2: Runtime Hook (.opencode/plugin/*.ts)    │
│ Contextual feedback — explains WHY and WHAT to do │
├─────────────────────────────────────────────────┤
│ Layer 3: Agent Prompting (AGENTS.md)              │
│ Proactive instruction — prevents the attempt      │
└─────────────────────────────────────────────────┘
```

---

## Full Worked Example #1: "No bare bash commands"

This guardrail prevents the agent from running raw shell commands outside of
`make <target>`. It is the most fundamental guardrail in the project.

### Layer 1: Config Permission (`opencode.json`)

```jsonc
{
  "permission": {
    "bash": [
      {
        "pattern": "make *",
        "decision": "allow"
      },
      {
        "pattern": "*",
        "decision": "deny"
      }
    ]
  }
}
```

**Key details:**
- The `"make *"` rule must come BEFORE the `"*"` deny rule. Last-matching-rule
  wins, so `"*": "deny"` first would deny everything including `make` commands.
- The `allow` on `"make *"` matches any command starting with `make ` — including
  `make test`, `make lint`, `make gate-background`, etc.
- This is a hard gate: the framework refuses to execute the command before any
  code runs. The agent cannot bypass it.

### Layer 2: Runtime Hook (`enforce-make.ts`)

```typescript
// .opencode/plugin/enforce-make.ts
// Blocks non-make bash commands and metacharacters.
// Registered in opencode.json "plugin" array.

import type { Plugin } from "@opencode-ai/plugin"

const FORBIDDEN_METACHARS = /[|;&$()`<>{}!\\]/;

const MAKE_ONLY_MESSAGE = [
  "BLOCKED: Direct bash commands and metacharacters are not allowed.",
  "",
  "Rule: You MUST only run `make <target>` commands in bash.",
  "Shell metacharacters (|, ;, &&, ||, $(), ``, >, <, {}, !, \\) are forbidden.",
  "",
  "What to do instead:",
  "  1. Find or create a make target for what you need",
  "  2. Run `make <targetname>`",
  "  3. If no target exists, add one to the Makefile first",
  "",
  "See AGENTS.md 'CRITICAL: Bash Command Policy' for the full policy.",
  "Run `make help` to see available targets.",
].join("\n");

export default (async ({}) => {
  return {
    "tool.execute.before": async (input: any, output: any) => {
      // Subagent guard — never enforce inside a subagent
      if (process.env.OPENCODE_SUBAGENT === "1") {
        return;
      }

      // Only block bash commands
      if (input?.tool !== "bash") {
        return;
      }

      const command: string = output?.args?.command ?? "";
      const trimmed = command.trim();

      if (trimmed === "") {
        return;
      }

      // Allow make commands only
      if (trimmed.startsWith("make ") || trimmed === "make") {
        // Also block metacharacters inside make commands
        if (FORBIDDEN_METACHARS.test(trimmed)) {
          throw new Error(MAKE_ONLY_MESSAGE);
        }
        return; // Pure make command — allowed
      }

      // Anything else — blocked
      throw new Error(MAKE_ONLY_MESSAGE);
    },

    "text.complete": async (input: any) => {
      if (process.env.OPENCODE_SUBAGENT === "1") {
        return;
      }
      // Inject the make-only directive into the system prompt.
      // This runs at generation time so the agent sees it fresh each turn.
      if (input?.systemPrompt) {
        input.systemPrompt =
          "⛔ BASH COMMAND POLICY: Only `make <target>` commands allowed.\n" +
          "Shell metacharacters (|, ;, &&, ||) are FORBIDDEN.\n" +
          "Use `make help` for available targets.\n\n" +
          input.systemPrompt;
      }
    },
  };
}) satisfies Plugin;
```

**Key details:**
- The subagent guard (`OPENCODE_SUBAGENT === "1"`) is **mandatory** — without
  it, the hook blocks tool calls inside subagents, breaking all delegated work.
- The error message tells the agent exactly what to do instead (read AGENTS.md,
  use `make help`). A silent block is unhelpful and leads to loops.
- The `satisfies Plugin` annotation ensures the hook conforms to the expected
  interface at compile time (type-checked but stripped by Node v26).
- The `text.complete` hook injects the policy fresh into every generation,
  acting as a prophylactic reminder.
- Fail-open: if any line throws an unexpected error, the plugin framework
  catches it and allows the tool call. A broken hook is better than a wedged
  session.

### Layer 3: Agent Prompting (`AGENTS.md`)

```markdown
## CRITICAL: Bash Command Policy

**You MUST only run `make <target>` commands in bash. Never run any other command directly.**

- ALLOWED: `make test`, `make lint`, `make init`, `make sync`, etc.
- DENIED: `uv ...`, `python3 ...`, `pip ...`, `git ...`, `which ...`,
  `ls ...`, `cat ...`, `find ...`, `rm ...`, `cp ...`, `mv ...`, or any other direct command.

**Shell metacharacters are FORBIDDEN:**

| Character | Name | Why forbidden |
|-----------|------|---------------|
| `\|` | Pipe | Chains commands, bypasses make |
| `;` | Semicolon | Runs multiple commands |
| `&&` | And | Chains commands conditionally |
| `()` | Subshell | Runs commands in subprocess |
| `$()` | Command substitution | Embeds command output |
| `>` / `<` | Redirect | Pipes output to files |

**If you need ANY of these, create a Makefile target.**

This is enforced by:
- `opencode.json` permission rules (hard deny on non-make bash)
- `.opencode/plugin/enforce-make.ts` (blocks metacharacters + non-make commands)
- This AGENTS.md section (proactive reminder)
```

**Key details:**
- The AGENTS.md section must be **prominent** — use `CRITICAL:` in the heading
  so the agent prioritizes it.
- Include the **exact** enforcement mechanisms so the agent knows it cannot talk
  its way around the block (when the hook fires, it can see the AGENTS.md
  reference and understand the fix is to use a make target).
- Use a table for metacharacters — visual, scannable, impossible to misread.

---

## Full Worked Example #2: "No commits without green gate"

This guardrail prevents the agent from committing code when the local gate
(`.gate-status`) is stale or failing. It prevents the class of failure where
the agent commits with a red gate, bypassing all quality checks.

### Layer 1: Config Permission

```jsonc
{
  "permission": {
    "bash": [
      {
        "pattern": "make git-commit *",
        "decision": "deny"
      }
    ]
  }
}
```

Note: In this project's actual configuration, the block is NOT at the
`opencode.json` config-permission layer — the config layer allows all
`make *` commands. Instead, the enforcement is entirely at the hook layer
and the prompt layer for this particular guardrail. This is valid: the
three layers don't all have to block the SAME way. The config layer can
allow the command to proceed to the hook, and the hook judges the context.

### Layer 2: Runtime Hook (inside `enforce-stop.ts`)

```typescript
// Inside .opencode/plugin/enforce-stop.ts — commit-block logic

import { readFileSync, existsSync } from "fs";

interface GateStatus {
  status: "PASS" | "FAIL" | "RUNNING" | "UNKNOWN";
  timestamp: number;
}

function readGateStatus(): GateStatus {
  const path = ".gate-status";
  if (!existsSync(path)) {
    return { status: "UNKNOWN", timestamp: 0 };
  }
  const raw = readFileSync(path, "utf-8");
  // .gate-status format:
  // === GATE: PASSED ===
  // 2026-07-25T14:30:00Z
  // lint: 0
  // typecheck: passed (<= baseline)
  // collect: 0 errors
  // tests: 847 passed, 2 skipped
  if (raw.includes("=== GATE: PASSED ===")) {
    return { status: "PASS", timestamp: Date.now() };
  }
  if (raw.includes("=== GATE: FAILED ===")) {
    return { status: "FAIL", timestamp: Date.now() };
  }
  if (raw.includes("=== GATE: RUNNING ===") || raw.includes("GATE PHASE:")) {
    return { status: "RUNNING", timestamp: Date.now() };
  }
  return { status: "UNKNOWN", timestamp: 0 };
}

function isCommitShaped(target: string): boolean {
  const COMMIT_TARGETS = [
    "git-commit",
    "commit-no-verify",
    "ship-commit",
    "repo-commit",
    "git-commit-file",
    "test-and-commit",
  ];
  return COMMIT_TARGETS.some((t) => target.startsWith(t));
}

// Inside the tool.execute.before handler:
if (input?.tool === "bash") {
  const command: string = output?.args?.command ?? "";
  const trimmed = command.trim();
  const targetMatch = trimmed.match(/^make\s+(\S+)/);
  const target = targetMatch ? targetMatch[1].split(/\s/)[0] : "";

  if (isCommitShaped(target)) {
    const gate = readGateStatus();
    const gateAgeMs = Date.now() - gate.timestamp;
    const MAX_GATE_AGE_MS = 600_000; // 10 minutes

    if (gate.status === "FAIL" || gate.status === "RUNNING" || gate.status === "UNKNOWN") {
      throw new Error([
        "BLOCKED: Cannot commit without a green gate.",
        "",
        `Gate status: ${gate.status}`,
        gate.status === "UNKNOWN"
          ? "No .gate-status file found. Run `make gate` or `make gate-background` first."
          : "Fix the failing gate before committing.",
        "",
        "What to do:",
        "  1. Run `make gate` (or `make gate-background` + `make gate-status-check`)",
        "  2. Fix any failing phases",
        "  3. Run `make gate` again — confirm PASS",
        "  4. Then commit",
        "",
        "See AGENTS.md 'CRITICAL: Commit-After-Green Policy'.",
      ].join("\n"));
    }

    if (gateAgeMs > MAX_GATE_AGE_MS) {
      throw new Error([
        "BLOCKED: Gate status is stale.",
        "",
        `Last gate was ${Math.round(gateAgeMs / 60000)} minutes ago.`,
        "Maximum allowed age: 10 minutes.",
        "",
        "Run `make gate` to refresh the gate before committing.",
      ].join("\n"));
    }
  }
}
```

**Key details:**
- The gate status is read from `.gate-status` — a file on disk. The hook
  does NOT run `make gate` itself (that would be a 40-minute blocking call
  inside a hook, breaking the session).
- The hook distinguishes between FAIL (never allow), RUNNING (never allow),
  UNKNOWN (no file — guide the agent to create one), and STALE (was green
  but too long ago).
- Specific, actionable guidance for each rejection case.
- Only blocks commit-shaped targets; test-only and push-only targets pass
  through (they have their own guardrails).

### Layer 3: Agent Prompting (`AGENTS.md`)

```markdown
## CRITICAL: Commit-After-Green Policy

**You MUST commit your work only after `make gate` is green.**

Workflow:
1. Tests pass for the change you made.
2. Run `make gate` — confirm all phases green (lint: 0, typecheck ≤ baseline,
   collect: 0 errors, tests: all pass).
3. Commit with `make git-commit MSG="..."` or `make ship-commit MSG="..."`.

Do not commit with a red, running, stale, or missing gate.

This is enforced by:
- `.opencode/plugin/enforce-stop.ts` — commit-shaped make targets blocked
  when .gate-status is missing/red/running/stale
- `tests/unit/test_commit_gate_freshness.py` — structural pin on gate check
  in commit targets
- This AGENTS.md section — proactive instruction
```

---

## Full Worked Example #3: "No done-words without evidence"

This guardrail blocks text output that claims completion without machine-produced
evidence (commit hash, test count, CI verdict, gate output).

### Layer 1: Config Permission

Not applicable — this guardrail operates at the **text output** surface, not
the tool execution surface. There is no tool to deny.

### Layer 2: Runtime Hook (`enforce-verified-claims.ts`)

```typescript
// .opencode/plugin/enforce-verified-claims.ts
// Blocks outgoing text containing "done" words without evidence tokens.

import type { Plugin } from "@opencode-ai/plugin";

const DONE_WORDS = /\b(landed|committed|pushed|fixed|passing|shipped|done|complete|green|resolved|deployed|verified|passed|working)\b/i;

// Evidence tokens that PROVE the claim
const EVIDENCE_TOKENS = [
  /\b[0-9a-f]{7,40}\b/,          // commit hash
  /VERIFIED\s+\S+@[0-9a-f]+/,    // verify-remote output
  /CI\s+(GREEN|RED|PENDING)/,     // CI verdict
  /\d+\s+passed/,                 // test pass count
  /=== GATE:\s*(PASSED|FAILED)/,  // gate output
  /Collection\s+OK/,              // collect-check output
];

function hasEvidence(text: string): boolean {
  return EVIDENCE_TOKENS.some((re) => re.test(text));
}

function countDoneWords(text: string): number {
  const matches = text.match(DONE_WORDS);
  return matches ? matches.length : 0;
}

export default (async ({}) => {
  return {
    "text.complete": async (input: any) => {
      // Subagent guard
      if (process.env.OPENCODE_SUBAGENT === "1") {
        return;
      }

      const text = input?.text ?? "";

      if (countDoneWords(text) > 0 && !hasEvidence(text)) {
        // Blank the text and inject a loud directive
        input.text = [
          "⛔ VERIFIED-CLAIMS BLOCK: Your response uses completion words",
          `   ("done", "fixed", "shipped", "green", etc.) without citing`,
          "   machine-produced evidence.",
          "",
          "REQUIRED EVIDENCE (at least one):",
          "   - Commit hash: abc123def",
          "   - VERIFIED <branch>@<sha>",
          "   - CI GREEN|RED|PENDING",
          "   - N passed (test count)",
          "   - === GATE: PASSED === / FAILED",
          "   - Collection OK",
          "",
          "Rewrite your response to include the verification output.",
          "See AGENTS.md 'CRITICAL: Done Claims Require Observable",
          "Verification Evidence'.",
        ].join("\n");
      }
    },
  };
}) satisfies Plugin;
```

**Key details:**
- Evidence tokens are regex patterns that match actual machine output
  (commit hashes, gate output, test counts). The hex-letter requirement
  (`[a-f]`) prevents false positives on pure-digit strings like timestamps
  and CI run numbers.
- The hook blanks the entire response — partial blanking would leave orphaned
  sentences. The agent must rewrite with evidence.
- Fail-open: if the regex throws from malformed input, the hook allows the
  text through. A false negative (allowing a claim without evidence) is better
  than a false positive (blocking all text output).
- The `text.complete` hook fires after text generation but before it reaches
  the user, making it a last-chance gate.

### Layer 3: Agent Prompting (`AGENTS.md`)

```markdown
## CRITICAL: Done Claims Require Observable Verification Evidence

A feature, fix, commit, push, or release is "done" ONLY when the SAME message
pastes the MEASUREMENT that makes it observable.

| Claim word | Required evidence |
|---|---|
| "committed" | Commit hash from `make git-log` |
| "pushed" | `VERIFIED <branch>@<sha>` from `make verify-remote` |
| "CI green" | `CI GREEN` + headSha from `make ci-verdict` |
| "tests pass" or "passing" | `N passed` from test runner output |
| "gate green" | `=== GATE: PASSED ===` from `.gate-status` |
| "fixed", "done", "shipped", "working" | At least one evidence token above |

An unverified claim is indistinguishable from a false claim.

This is enforced by:
- `.opencode/plugin/enforce-verified-claims.ts` — text.complete hook
- This AGENTS.md section — proactive instruction
```

---

## Hook Pattern Reference

### `tool.execute.before` — block tool calls before execution

```typescript
// Signature
"tool.execute.before": async (input: ToolCallInput, output: ToolCallOutput) => {
  // input.tool  — name of the tool being called ("bash", "read", "write", etc.)
  // input.args  — arguments to the tool
  // output.args — the currently set arguments (for bash: { command: string })

  // Return undefined to allow (pass-through).
  // Throw an Error to deny. The error message IS the feedback to the agent.

  // Always guard subagents:
  if (process.env.OPENCODE_SUBAGENT === "1") return;

  // Always fail-open:
  try {
    // ... your check logic ...
  } catch (e) {
    // Unexpected error — allow the tool call
    return;
  }
}
```

**Use for:** blocking specific tool calls based on context (dirty tree, missing
gate, low floor count, stale session).

### `text.complete` — modify/block text output before it reaches the user

```typescript
// Signature
"text.complete": async (input: TextCompleteInput) => {
  // input.text — the generated text about to be sent to the user

  // Modify input.text to change what the user sees.
  // Set input.text = "" to blank the response entirely.
  // Set input.text = "OVERRIDE MESSAGE" to replace with guidance.

  if (process.env.OPENCODE_SUBAGENT === "1") return;
  // ...
}
```

**Use for:** preventing false completion claims, enforcing response formats,
injecting policy reminders.

### `session.idle` — inject a directive when the session goes idle

```typescript
// Signature
"session.idle": async (input: SessionIdleInput) => {
  // input.idleDurationMs — how long the session has been idle

  // Return a string to inject into the agent's context.
  // Return undefined for no injection.

  if (process.env.OPENCODE_SUBAGENT === "1") return;

  return "⛔ SESSION IDLE — pending work exists. Resume immediately.";
}
```

**Use for:** nudging the agent to resume work after a pause, preventing
the "agent goes silent while waiting" failure mode.

### `experimental.chat.system.transform` — modify the system prompt at boot

```typescript
// Signature
"experimental.chat.system.transform": async (input: SystemTransformInput) => {
  // input.systemPrompt — the full system prompt about to be sent to the model

  // Modify the system prompt by prepending/appending directives.

  if (process.env.OPENCODE_SUBAGENT === "1") return;

  input.systemPrompt = "🚨 PRE-GENERATION DIRECTIVE\n\n" + input.systemPrompt;
}
```

**Use for:** injecting session-start protocols, floor reminders, policy
directives that must be visible at the TOP of every generation.

---

## Error Handling Patterns

### Pattern 1: Fail-open (always)

```typescript
"tool.execute.before": async (input: any, output: any) => {
  if (process.env.OPENCODE_SUBAGENT === "1") return;

  try {
    // Check logic — may throw
    const state = JSON.parse(readFileSync("/tmp/gludd-state.json", "utf-8"));
    if (state.blocked) {
      throw new Error("BLOCKED: ...");
    }
  } catch (e) {
    if (e instanceof Error && e.message.startsWith("BLOCKED:")) {
      throw e; // Intentional block — rethrow
    }
    // Unexpected error (file missing, JSON parse error, etc.) — allow
    return;
  }
}
```

### Pattern 2: Missing state file = allow

```typescript
function readStateFile(path: string): Record<string, unknown> | null {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null; // File missing or corrupt — treat as "no state / no block"
  }
}
```

### Pattern 3: JSON parse error = allow

```typescript
let data: Record<string, unknown>;
try {
  data = JSON.parse(raw);
} catch {
  // Corrupted state file — reset it and allow
  writeFileSync(path, JSON.stringify(defaultState));
  return; // allow
}
```

---

## Testing Guardrails

### pytest: verify a guardrail exists at all 3 layers

```python
# tests/unit/test_guardrail_bash_command_policy.py

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


class TestBashCommandGuardrail:

    # --- Layer 1: opencode.json permission ---
    def test_opencode_has_bash_make_allow_rule(self):
        config = json.loads((ROOT / "opencode.json").read_text())
        bash_rules = config.get("permission", {}).get("bash", [])
        allow_make = any(
            r.get("pattern") == "make *" and r.get("decision") == "allow"
            for r in bash_rules
        )
        assert allow_make, "opencode.json must allow 'make *' in bash permissions"

    def test_opencode_has_bash_catchall_deny(self):
        config = json.loads((ROOT / "opencode.json").read_text())
        bash_rules = config.get("permission", {}).get("bash", [])
        deny_all = any(
            r.get("pattern") == "*" and r.get("decision") == "deny"
            for r in bash_rules
        )
        assert deny_all, "opencode.json must have catch-all deny for bash"

    # --- Layer 2: Plugin hook ---
    def test_enforce_make_plugin_exists(self):
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-make.ts"
        assert plugin_path.exists(), "enforce-make.ts plugin must exist"

    def test_enforce_make_registered_in_opencode(self):
        config = json.loads((ROOT / "opencode.json").read_text())
        plugins = config.get("plugin", [])
        assert any(
            "enforce-make.ts" in p for p in plugins
        ), "enforce-make.ts must be registered in opencode.json plugin array"

    def test_enforce_make_has_subagent_guard(self):
        content = (ROOT / ".opencode" / "plugin" / "enforce-make.ts").read_text()
        assert "OPENCODE_SUBAGENT" in content, (
            "enforce-make.ts must check OPENCODE_SUBAGENT before blocking"
        )

    def test_enforce_make_blocks_non_make_commands(self):
        content = (ROOT / ".opencode" / "plugin" / "enforce-make.ts").read_text()
        assert "startsWith" in content and "make " in content, (
            "enforce-make.ts must check for 'make ' prefix"
        )
        assert "BLOCKED" in content, (
            "enforce-make.ts must emit a BLOCKED message"
        )

    def test_enforce_make_blocks_metacharacters(self):
        content = (ROOT / ".opencode" / "plugin" / "enforce-make.ts").read_text()
        has_metachar_pattern = re.search(
            r"FORBIDDEN.*[|;&$`()<>{}!\\]", content
        )
        assert has_metachar_pattern, (
            "enforce-make.ts must block shell metacharacters"
        )

    # --- Layer 3: AGENTS.md prompting ---
    def test_agents_md_has_bash_command_policy_section(self):
        content = (ROOT / "AGENTS.md").read_text()
        assert "Bash Command Policy" in content, (
            "AGENTS.md must have a Bash Command Policy section"
        )

    def test_agents_md_lists_allowed_and_denied(self):
        content = (ROOT / "AGENTS.md").read_text()
        assert "ALLOWED:" in content
        assert "DENIED:" in content

    def test_agents_md_references_enforcement(self):
        content = (ROOT / "AGENTS.md").read_text()
        assert "enforce-make.ts" in content, (
            "AGENTS.md must reference the enforcement plugin"
        )
        assert "opencode.json" in content, (
            "AGENTS.md must reference the config permission layer"
        )
```

---

## Adding a New Guardrail: Step-by-Step Walkthrough

### Scenario: "No file deletions without explicit user approval"

**Step 1 — Identify the policy.** The agent should never delete a file from the
workspace unless the user explicitly said "delete X." Auto-deletion of temporary
files during cleanup is fine, but deleting source files to "simplify" is not.

**Step 2 — Add Layer 3 (prompt) first.** Write the AGENTS.md section before
the code. This is the spec.

```markdown
## CRITICAL: No File Deletion Without Explicit Instruction

**You MUST NOT delete any file from `src/`, `tests/`, `docs/`, or `config/`
unless the user explicitly requested it in the current conversation.**

Deleting temporary files (`/tmp/gludd-*`, `.gate-logs/*`) is allowed.

This is enforced by `.opencode/plugin/enforce-deletion-gate.ts`.
```

**Step 3 — Add Layer 2 (hook).** Create `.opencode/plugin/enforce-deletion-gate.ts`:

```typescript
import type { Plugin } from "@opencode-ai/plugin";
import { relative, resolve } from "path";

const PROTECTED_DIRS = ["src/", "tests/", "docs/", "config/"];

export default (async ({}) => {
  return {
    "tool.execute.before": async (input: any, output: any) => {
      if (process.env.OPENCODE_SUBAGENT === "1") return;

      // Check write/edit tools for content deletion
      if (input?.tool === "write" || input?.tool === "edit") {
        const filePath = input?.args?.filePath ?? "";
        const abs = resolve(filePath);

        // Only protect project files
        const isProtected = PROTECTED_DIRS.some((d) =>
          abs.includes(resolve(d))
        );
        if (!isProtected) return;

        // Check if content is being set to empty (deletion)
        const content = input?.args?.content ?? "";
        if (content === "") {
          throw new Error([
            "BLOCKED: File deletion is not allowed without explicit user instruction.",
            "",
            `File: ${relative(process.cwd(), abs)}`,
            "",
            "If you need to delete this file, ask the user first.",
            "If the file is temporary output, use /tmp/ instead.",
            "",
            "See AGENTS.md 'No File Deletion Without Explicit Instruction'.",
          ].join("\n"));
        }
      }
    },
  };
}) satisfies Plugin;
```

**Step 4 — Register the plugin.** Add to `opencode.json`:

```jsonc
{
  "plugin": [
    // ... existing plugins ...
    "./.opencode/plugin/enforce-deletion-gate.ts"
  ]
}
```

**Step 5 — Write tests.** Create `tests/unit/test_deletion_gate_plugin.py`:

```python
def test_deletion_gate_plugin_exists():
    assert Path(".opencode/plugin/enforce-deletion-gate.ts").exists()

def test_deletion_gate_registered_in_opencode():
    config = json.loads(Path("opencode.json").read_text())
    plugins = config.get("plugin", [])
    assert any("enforce-deletion-gate.ts" in p for p in plugins)

def test_deletion_gate_has_subagent_guard():
    content = Path(".opencode/plugin/enforce-deletion-gate.ts").read_text()
    assert "OPENCODE_SUBAGENT" in content

def test_agents_md_has_deletion_policy():
    content = Path("AGENTS.md").read_text()
    assert "No File Deletion" in content
    assert "enforce-deletion-gate.ts" in content
```

**Step 6 — Run the gate.** Verify the new tests pass, the plugin compiles,
and the guardrail does not break existing behavior.

```
make test-specific TESTFILE='tests/unit/test_deletion_gate_plugin.py'
make check-node-v26-compat
make check-plugin-hook-invoke
make test  # full suite — ensure no regressions
```

---

## Common Mistakes

### Mistake 1: Forgetting the subagent guard

```typescript
// WRONG — blocks tool calls inside subagents, breaking all delegation
export default (async ({}) => {
  return {
    "tool.execute.before": async (input: any) => {
      if (input.tool === "bash") {
        throw new Error("BLOCKED");
      }
    },
  };
}) satisfies Plugin;
```

```typescript
// RIGHT — subagent guard at the top of every hook
export default (async ({}) => {
  return {
    "tool.execute.before": async (input: any) => {
      if (process.env.OPENCODE_SUBAGENT === "1") return; // <-- MANDATORY

      if (input.tool === "bash") {
        throw new Error("BLOCKED");
      }
    },
  };
}) satisfies Plugin;
```

### Mistake 2: Not registering the plugin

```jsonc
// WRONG — plugin file exists but is never loaded
{
  "plugin": [
    "./.opencode/plugin/enforce-make.ts"
    // enforce-deletion-gate.ts is missing — never fires
  ]
}
```

The plugin array in `opencode.json` is the load list. A file not listed here
is dead code — it will never execute.

### Mistake 3: Silent block

```typescript
// WRONG — agent has no idea why the tool call failed, loops trying variants
throw new Error("");  // empty error = no guidance = agent spins
```

```typescript
// RIGHT — tell the agent what happened, why, and what to do
throw new Error([
  "BLOCKED: <what was blocked>",
  "",
  "Why: <reason for the policy>",
  "",
  "What to do instead:",
  "  1. <concrete step>",
  "  2. <concrete step>",
  "",
  "See AGENTS.md '<section name>' for the full policy.",
].join("\n"));
```

### Mistake 4: Blocking reads

```typescript
// WRONG — blocks Read/Glob/Grep tools, preventing the agent from diagnosing
if (input.tool !== "task" && input.tool !== "agent") {
  throw new Error("BLOCKED: must dispatch");
}
```

The agent MUST be able to READ files to understand what's happening. Only block
mutating tools (write, edit, bash) and dispatch tools (task, agent, workflow).
Read-only tools (read, grep, glob) should always pass through.

### Mistake 5: Checking the wrong input shape

```typescript
// WRONG — input.path works for reads but not for writes/edits
const path = input.args.path;  // undefined for write/edit — they use filePath

// RIGHT — check both forms
const path = input.args?.path ?? input.args?.filePath ?? input.args?.tool_input?.path;
```

Different tools pass file paths under different keys: `path` for reads,
`filePath` for writes/edits, sometimes `tool_input.path` in nested forms.
Check all three to avoid false negatives where the guardrail fails to fire.

---

## Checklist for Every Guardrail

When adding ANY new guardrail, verify all three:

- [ ] `opencode.json` permission rule exists that hard-blocks the action (or
      intentionally leaves the gate to the hook layer)
- [ ] `.opencode/plugin/` has a hook that catches the violation and returns a
      helpful error message with concrete next steps
- [ ] `AGENTS.md` has a prominent section explaining the rule proactively
- [ ] The plugin is registered in `opencode.json` `plugin` array
- [ ] The plugin has a subagent guard (`OPENCODE_SUBAGENT === "1"`)
- [ ] The plugin is fail-open (unexpected errors do not block)
- [ ] All three layers reference each other (plugin mentions AGENTS.md,
      AGENTS.md mentions the plugin and config)
- [ ] Tests exist at `tests/unit/test_<guardrail>_plugin.py` that verify
      existence + subagent guard + registration + AGENTS.md section
- [ ] `make check-node-v26-compat` passes (no forbidden TypeScript patterns)
- [ ] `make check-plugin-hook-invoke` passes (hook function invokes without
      ReferenceError)

---

## Meta-Guardrail

This skill itself is a guardrail about guardrails. When you catch yourself
adding a restriction with only one or two layers, stop and add the missing
layers before continuing.
