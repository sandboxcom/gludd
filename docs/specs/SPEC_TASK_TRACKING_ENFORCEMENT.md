# SPEC: Task Tracking Enforcement Plugin

**Spec ID:** SPEC_TASK_TRACKING_ENFORCEMENT
**Status:** draft
**Created:** 2026-07-25
**Author:** Session 51 — behavioral guardrail audit
**Depends on:** `lib/shared.ts`, `lib/hot_reload.ts`

---

## 1. Problem Statement

The agent has no mechanical enforcement that it updates TASKS.md after
receiving user prompts or after completing work. The existing plugins
(enforce-stop, enforce-session-start, enforce-multitask) READ TASKS.md to
detect unchecked work, but none verify the agent is WRITING to TASKS.md.

This creates a class of failure where:
- User prompts go unlogged in TASKS.md
- Completed work is never ticked until a batch update at session end
- Subagent results are codified in commits but not in the task ledger
- The agent "forgets" what it was asked to do between waves

---

## 2. Proposed Plugin: `enforce-task-tracking.ts`

### 2.1 Hook Surface

| Hook | Purpose |
|---|---|
| `experimental.chat.system.transform` | Inject task-tracking directive into system prompt |
| `text.complete` | Before agent response, verify TASKS.md was modified since last user prompt |

### 2.2 State File: `/tmp/gludd-task-tracking.json`

```json
{
  "pid": 12345,
  "last_user_prompt_ts": 1721926800000,
  "last_tasks_md_mtime": 1721926700000,
  "missed_update_count": 0,
  "last_prompt_text_hash": "abc123def",
  "tasks_md_path": "/Users/shawnwilson/gludd/TASKS.md"
}
```

### 2.3 Detection Logic (text.complete hook)

```text
On text.complete:
  1. If isSubagent() → return output (skip)
  2. If GLUDD_TASK_TRACKING_ENFORCE=0 → return output
  3. Read state file
  4. If last_user_prompt_ts == 0 → return output (no prompt recorded yet)
  5. Stat TASKS.md → get current mtime
  6. If TASKS.md mtime > last_tasks_md_mtime:
     → TASKS.md was updated since last prompt → reset missed_update_count to 0
     → Update last_tasks_md_mtime in state
     → return output (everything fine)
  7. If TASKS.md mtime <= last_tasks_md_mtime:
     → TASKS.md has NOT been updated since last user prompt
     → Increment missed_update_count
     → On first miss: inject advisory note
     → On third consecutive miss: inject WARNING nag
     → return output with injected advisory text
```

### 2.4 User Prompt Detection

Since there is no `user.message.received` hook surface in OpenCode, the plugin
will use a heuristic:

- **File-watch approach:** Monitor TASKS.md mtime changes. When the agent reads
  TASKS.md (detected via `tool.execute.before` + `isReadTool` OR detected by
  `enforce-session-start.ts` which sets `_lastTasksReadMtime`), compare the
  current mtime to the stored mtime. If the agent read but didn't write to
  TASKS.md after a user prompt, flag it.

- **Session-prompt counter:** The `enforce-session-start.ts` plugin already
  tracks session start. Extend it (or this plugin) to increment a
  `user_prompt_count` each time a user message is sent (detected via
  `text.complete` → the agent is responding, which means a user message
  preceded).

### 2.5 Advisory vs. Blocking

- **Advisory by default.** This plugin NEVER blocks tool calls or responses.
  It injects advisory text into the agent's system prompt or response.
- **Escalation model:**
  - `missed_update_count = 1`: inject "Note: TASKS.md may need updating"
  - `missed_update_count = 3`: inject "WARNING: 3 prompts without TASKS.md update"
  - `missed_update_count >= 5`: inject "CRITICAL: TASKS.md is stale"

### 2.6 Non-Trivial Prompt Filtering

Not every user message requires a TASKS.md update. The plugin should skip
detection for:
- Short acknowledgments: "ok", "yes", "no", "thanks", "continue", "proceed"
- Questions about status that don't request new work
- Messages under 10 characters

This is a heuristic; false positives are acceptable (advisory only).

### 2.7 System Prompt Injection

```text
================ TASK TRACKING DIRECTIVE ================
After every user prompt that requests new work:
  1. Add a new entry to TASKS.md describing the request
  2. After each subagent result is codified (committed), tick
     the corresponding TASKS.md checkbox with evidence
  3. TASKS.md is the single source of truth — never rely on
     memory alone

This directive is advisory. The enforce-task-tracking.ts
plugin monitors TASKS.md mtime and warns when TASKS.md is
stale relative to user activity.
========================================================
```

---

## 3. File Structure

```text
.opencode/plugin/enforce-task-tracking.ts   ← main plugin (proxy pattern)
.opencode/plugin/impl/enforce_task_tracking_impl.ts  ← implementation
/tmp/gludd-task-tracking.json               ← state file
tests/unit/test_task_tracking_plugin.py     ← behavioral tests
tests/unit/test_task_tracking_guardrails.py ← structural tests (exists)
```

---

## 4. Implementation Outline

### 4.1 Plugin skeleton (proxy pattern matching existing plugins)

```typescript
import type { Plugin } from "@opencode-ai/plugin"
import { isSubagent, reportAlive, readJsonFile, writeJsonFile, getProjectRoot } from "../lib/shared.ts"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
import * as fs from "node:fs"
import * as path from "node:path"

const STATE_FILE = "/tmp/gludd-task-tracking.json"
const ENFORCE = process.env.GLUDD_TASK_TRACKING_ENFORCE !== "0"

interface TaskTrackingState {
  pid: number
  last_user_prompt_ts: number
  last_tasks_md_mtime: number
  missed_update_count: number
  last_prompt_text_hash: string
  tasks_md_path: string
}

const TRIVIAL_PROMPTS = /^(ok|yes|no|thanks|thank you|continue|proceed|go|got it|ack|k|kk)$/i

function isNonTrivialPrompt(text: string): boolean {
  if (!text || text.trim().length < 10) return false
  if (TRIVIAL_PROMPTS.test(text.trim())) return false
  return true
}

const defaultImpl: HotModule = {
  "text.complete": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    if (!ENFORCE) return output
    try {
      const state = readJsonFile<TaskTrackingState>(STATE_FILE, {
        pid: process.pid, last_user_prompt_ts: 0,
        last_tasks_md_mtime: 0, missed_update_count: 0,
        last_prompt_text_hash: "", tasks_md_path: "",
      })
      const root = getProjectRoot()
      const tasksPath = path.join(root, "TASKS.md")
      state.tasks_md_path = tasksPath
      if (fs.existsSync(tasksPath)) {
        const currentMtime = fs.statSync(tasksPath).mtimeMs
        if (state.last_tasks_md_mtime === 0) {
          state.last_tasks_md_mtime = currentMtime
          writeJsonFile(STATE_FILE, state)
          return output
        }
        if (currentMtime <= state.last_tasks_md_mtime && state.last_user_prompt_ts > 0) {
          state.missed_update_count++
          writeJsonFile(STATE_FILE, state)
          if (state.missed_update_count >= 5) {
            const text = typeof output === "string" ? output : ""
            return text + "\n\n[TASK TRACKING: CRITICAL — TASKS.md is stale. " +
              String(state.missed_update_count) +
              " prompts without update. Update TASKS.md now.]"
          } else if (state.missed_update_count >= 3) {
            const text = typeof output === "string" ? output : ""
            return text + "\n\n[TASK TRACKING: WARNING — " +
              String(state.missed_update_count) +
              " prompts without TASKS.md update.]"
          }
        } else {
          state.last_tasks_md_mtime = currentMtime
          state.missed_update_count = 0
          writeJsonFile(STATE_FILE, state)
        }
      }
    } catch { /* fail-open */ }
    return output
  },
  "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
    if (isSubagent()) return output
    if (typeof output === "string") {
      const directive = [
        "================ TASK TRACKING DIRECTIVE ================",
        "After every user prompt that requests new work:",
        "  1. Add a new entry to TASKS.md describing the request",
        "  2. After each subagent result is codified, tick the",
        "     corresponding checkbox with evidence",
        "  3. TASKS.md is the single source of truth",
        "This directive is ENFORCED by enforce-task-tracking.ts.",
        "========================================================",
      ].join("\n")
      return directive + "\n\n" + output
    }
    return output
  },
}

export default (() => {
  return {
    "text.complete": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output
      reportAlive("enforce-task-tracking")
      const impl = loadHotModule("task-tracking", defaultImpl)
      const fn = impl["text.complete"]
      return fn ? await fn(_input, output) : output
    },
    "experimental.chat.system.transform": async (_input: unknown, output: unknown) => {
      if (isSubagent()) return output
      const impl = loadHotModule("task-tracking", defaultImpl)
      const fn = impl["experimental.chat.system.transform"]
      return fn ? await fn(_input, output) : output
    },
  }
}) satisfies Plugin
```

### 4.2 Registration in opencode.json

```json
{
  "path": ".opencode/plugin/enforce-task-tracking.ts",
  "config": {}
}
```

### 4.3 Make target

```makefile
task-tracking-state: ## Show task-tracking state file
    @cat /tmp/gludd-task-tracking.json 2>/dev/null || echo '{"status":"no state file"}'

reset-task-tracking: ## Reset task-tracking state
    @rm -f /tmp/gludd-task-tracking.json
    @echo "Task tracking state reset."
```

---

## 5. Limitations & Future Work

### 5.1 No user.message.received hook surface

OpenCode does not expose a hook that fires on user message receipt. The
proposed plugin uses heuristics:
- TASKS.md mtime comparison (detects whether agent WROTE to TASKS.md)
- text.complete hook (fires after agent response, implying a user message
  preceded)

A proper `user.message.received` hook would eliminate the heuristic and allow:
- Direct comparison of "what the user asked" to "what TASKS.md contains"
- Prompt-text hashing for deduplication
- Non-trivial prompt classification at the hook level

### 5.2 Advisory only

The plugin does NOT block responses or tool calls. Blocking would require:
- A user.message.received hook to definitively know a prompt was received
- A content-comparison mechanism (NLP/NER) to classify prompts as "requests
  new work" vs "status question"
- This is infeasible without an LLM call, which the plugin cannot make

### 5.3 Content-level verification

The plugin checks TASKS.md mtime, not content. An agent could `touch TASKS.md`
without adding new entries. Content-level verification requires parsing both
the user prompt and TASKS.md, which is beyond plugin scope.

---

## 6. Testing Strategy

### 6.1 Structural tests (test_task_tracking_guardrails.py — exists)

- Plugin file existence
- State file path constant
- ENFORCE env var check
- System.transform + text.complete hook registration
- Subagent guard presence
- TRIVIAL_PROMPTS pattern existence

### 6.2 Behavioral tests (test_task_tracking_plugin.py — to create)

- text.complete returns output unchanged when TASKS.md mtime is recent
- text.complete injects advisory after 3 consecutive missed updates
- text.complete injects CRITICAL after 5 consecutive missed updates
- ENFORCE=0 disables all injection
- Subagent context returns output unchanged
- Corrupt state file → fail-open (return output)
- Missing TASKS.md → no-op
- Missed update counter resets on actual TASKS.md modification

---

## 7. Acceptance Criteria

- [ ] `enforce-task-tracking.ts` exists and follows proxy pattern
- [ ] Registered in `opencode.json` with `text.complete` + `system.transform` hooks
- [ ] State file `/tmp/gludd-task-tracking.json` maintained with correct schema
- [ ] Advisory injection works at 1, 3, and 5+ missed updates
- [ ] `GLUDD_TASK_TRACKING_ENFORCE=0` disables entirely
- [ ] Subagent guard prevents injection into subagent output
- [ ] Structural tests (11+ cases) pass
- [ ] Behavioral tests (8+ cases) pass
- [ ] `make check-node-v26-compat` passes on the new file
- [ ] AGENTS.md references the new plugin
