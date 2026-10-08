# AG.2 — Lifecycle Hook Expansion (Strands-Style Hooks)

**Design Document**
**Author:** gludd self-improvement
**Date:** 2026-07-12
**Status:** Draft
**Reference:** Amazon Strands Agents Evals SDK hook surface

---

## 1. Current State

gludd's enforcement plugins currently register against only two hook events:

| Hook | Used By | Capability |
|------|---------|------------|
| `tool.execute.before` | All 13 enforcement plugins | Inspect/block tool calls before execution |
| `tool.execute.after` | enforce-make, enforce-deadline, enforce-delegate, enforce-commit-lock | Inspect results after execution |

Additionally, two plugins use auxiliary hook surfaces:
- `experimental.text.complete` — enforce-stop, enforce-floor, enforce-verified-claims, enforce-multitask, enforce-enhancement-ratio (post-response text inspection)
- `experimental.chat.system.transform` — enforce-session-start, enforce-stop, enforce-floor (inject directives into system prompt at boot)

**The gap:** These two interception points (`tool.execute.before/after`) are the only lifecycle hooks. Every enforcement concern — model routing, CoT analysis, task validation, human escalation, context compaction — is forced through the tool-call surface, meaning:
1. **Model calls are invisible to enforcement.** Plugins cannot inspect, modify, or block model API calls.
2. **Agent reasoning (CoT) is opaque.** No hook fires between the tool result arriving and the model generating its next response.
3. **Subagent lifecycle is invisible.** Dispatch and completion events cannot be intercepted.
4. **Human interruptions cannot be prevented.** The `AskUserQuestion` tool is denied post-hoc by enforce-stop, but no hook fires *before* the question surfaces.

The Strands Agents Evals SDK defines a rich lifecycle hook surface that gludd should adopt. This document specifies the expansion.

---

## 2. Proposed Hook Surface

Ten lifecycle hooks organized into five domains. Each hook is a named event that plugins can subscribe to with `(input, output)` signature, returning `void` (allow) or throwing an error with `permissionDecision: "deny"` (block).

### 2.1 Model Call Domain

#### `model.call.before`
Fires **before** a model API call is made.

```text
Input: {
  model: string,            // e.g. "deepseek-v4-pro"
  messages: Message[],      // conversation context being sent
  tools: ToolDef[],         // tool definitions available
  systemPrompt: string,     // the rendered system prompt
  budget?: {                // token budget for this call
    maxTokens: number,
    thinkingBudget?: number,
  },
}

Output (mutable): {
  model?: string,           // override: route to different model
  messages?: Message[],     // override: modify context (add/remove/truncate)
  skip?: boolean,           // skip the call entirely, return synthetic response
  tools?: ToolDef[],        // override: restrict available tools
  budget?: {                // override: enforce token budget
    maxTokens: number,
    thinkingBudget?: number,
  },
}
```

**Use cases:**
- **Budget enforcement** (AG.10): cap maxTokens per call, enforce per-session budgets
- **Model routing**: switch to cheaper model when context window is large
- **Prompt injection guardrail**: scan messages for injection attempts, block call
- **Tool restriction**: remove dangerous tools from model's view for sensitive tasks
- **Skip optimization**: return cached response instead of making API call

#### `model.call.after`
Fires **after** a model API call completes.

```text
Input: {
  model: string,
  messages: Message[],
  request: { model, messages, tools },
  response: {
    content: string,
    toolCalls?: ToolCall[],
    usage: { promptTokens, completionTokens, totalTokens },
    finishReason: string,
    latencyMs: number,
  },
}

Output (mutable): {
  content?: string,          // override: filter/modify model output
  toolCalls?: ToolCall[],    // override: filter/rewrite tool calls
}
```

**Use cases:**
- **Output filtering**: scan for secrets, PII, policy violations; redact or block
- **Structured extraction**: parse model output, extract key decisions for audit
- **Latency/cost metrics**: log usage to persistent metrics store
- **Coherence validation**: detect hallucination markers, contradictory statements
- **Tool call validation**: verify tool calls reference real tools with valid args

### 2.2 Agent Thinking Domain

#### `agent.think.before`
Fires **before** the model generates chain-of-thought (or equivalent reasoning phase).

```text
Input: {
  context: {
    currentTask: string,
    toolResults: ToolResult[],    // results from last tool call batch
    conversationLength: number,
    memoryEntries: MemoryEntry[], // injected memory context
  },
  constraints: {
    maxThinkingTokens?: number,
    reasoningStyle?: "concise" | "verbose" | "structured",
  },
}

Output (mutable): {
  context?: {
    toolResults?: ToolResult[],   // override: filter/preprocess tool results
    memoryEntries?: MemoryEntry[], // override: inject additional memory
  },
  constraints?: {
    maxThinkingTokens?: number,
    reasoningStyle?: string,
  },
}
```

**Use cases:**
- **Context injection**: add relevant memory entries, task history, policy reminders
- **Tool result preprocessing**: summarize large tool outputs before model sees them
- **Thinking budget control**: cap reasoning tokens to prevent runaway CoT
- **Anti-loop injection**: detect repetitive tool-call patterns, inject correction directive

#### `agent.think.after`
Fires **after** the model completes its reasoning phase (CoT or equivalent).

```text
Input: {
  reasoning: string,          // the model's chain-of-thought (if exposed)
  decision: {
    nextAction: "respond" | "call_tool" | "done",
    plan?: string,
    confidence?: number,
  },
  context: {
    currentTask: string,
    conversationLength: number,
  },
}

Output (mutable): {
  reasoning?: string,         // override: annotate or filter reasoning
  decision?: {
    nextAction?: string,
    plan?: string,
    confidence?: number,
  },
}
```

**Use cases:**
- **Reasoning quality audit**: detect circular reasoning, false assumptions, logical gaps
- **Decision override**: block premature "done" decisions, redirect to continue work
- **Confidence thresholding**: require minimum confidence before allowing tool calls
- **CoT logging**: persist reasoning for trajectory evaluation (AG.1)

### 2.3 Task Lifecycle Domain

#### `task.dispatch.before`
Fires **before** a subagent is dispatched (via `Task` / `Agent` / `Workflow` tool).

```text
Input: {
  task: {
    description: string,       // the task description / dispatch label
    prompt: string,            // the full subagent prompt
    model: string,             // subagent model
    isolation?: "worktree" | "none",
  },
  budget: {
    maxSteps?: number,
    maxTokens?: number,
    timeoutMs?: number,
  },
  dispatcher: {
    currentTaskCount: number,
    floor: number,
    ceiling: number,
  },
}

Output (mutable): {
  prompt?: string,             // override: modify/rewrite subagent prompt
  model?: string,              // override: assign different model
  budget?: {
    maxSteps?: number,
    maxTokens?: number,
    timeoutMs?: number,
  },
  skip?: boolean,              // deny dispatch (return synthetic result)
}
```

**Use cases:**
- **Task validation**: verify prompt includes required directives (bash policy, return format)
- **Budget assignment**: enforce per-task token/timeout limits
- **Deduplication**: check if identical task was already completed, skip dispatch
- **Dispatch rate limiting**: pause dispatch during gate or commit operations
- **Model assignment policy**: enforce sonnet-dominant ratio at dispatch time

#### `task.complete.after`
Fires **after** a subagent returns its final result.

```text
Input: {
  task: {
    id: string,
    description: string,
    prompt: string,
    model: string,
  },
  result: {
    summary: string,           // the subagent's response text
    toolCallCount: number,
    latencyMs: number,
    status: "completed" | "failed" | "stalled",
  },
  evidence?: {
    commitHash?: string,
    testCount?: number,
    filesModified?: string[],
  },
}

Output (mutable): {
  result?: {
    summary?: string,          // override: rewrite result summary
    status?: string,
  },
  codification?: {             // auto-codify result
    updateTasksMd: boolean,    // mark TASKS.md entry complete
    recordCommit: boolean,     // record commit in session ledger
    updateSessionMd: boolean,  // update SESSION.md
  },
}
```

**Use cases:**
- **Result validation**: verify subagent actually produced deliverables (not just prose)
- **Auto-codification**: automatically update TASKS.md, SESSION.md on completion
- **Nothing-dropped guard**: detect completed subagents with no resulting commit/change
- **Completion audit**: log all subagent results to trajectory store (AG.1)
- **Re-dispatch detection**: flag completed tasks being re-dispatched (dedup violation)

### 2.4 Human Interaction Domain

#### `human.escalation.before`
Fires **before** a blocking question or permission prompt is surfaced to the user.

```bash
Input: {
  escalation: {
    type: "question" | "permission" | "error" | "escalation",
    message: string,           // the message to be shown to user
    options?: string[],        // for question/choice escalations
  },
  alternatives: {
    canSolveLocally: boolean,  // agent could make decision itself
    hasDefaults: boolean,      // safe default exists
    fallbackPlan?: string,     // what the agent would do if no answer
  },
  context: {
    taskInProgress: string,
    pendingWorkCount: number,
  },
}

Output (mutable): {
  skip?: boolean,              // deny escalation, proceed with fallback
  message?: string,            // override: rephrase question
  fallback?: string,           // override: specify alternative action
}
```

**Use cases:**
- **Anti-blocking enforcement**: deny questions when agent could self-answer (AGENTS.md policy)
- **Permission auto-granting**: auto-approve operations within human-agent intersection
- **Question rewriting**: rephrase ambiguous questions to be specific and actionable
- **Fallback enforcement**: when pending work exists, never allow "shall I continue?"

### 2.5 Session Management Domain

#### `session.compact.before`
Fires **before** the context window is compacted (old messages truncated/removed).

```text
Input: {
  compaction: {
    trigger: "auto" | "manual",  // what triggered compaction
    currentTokens: number,
    targetTokens: number,
    messagesToRemove: number,    // how many messages will be dropped
  },
  criticalState: {
    taskId?: string,             // current task being worked on
    pendingWork: string[],       // task descriptions still pending
    lastCommitHash?: string,
    enforcementState: {          // loaded enforcement state
      floor: number,
      streak: number,
      deadlines: Record<string, number>,
    },
  },
}

Output (mutable): {
  preserve?: string[],          // message IDs to preserve (don't compact)
  inject?: string,              // text to inject as a "memory" message post-compaction
  criticalState?: {             // override: ensure state survives compaction
    pendingWork: string[],
    enforcementState: Record<string, any>,
  },
}
```

**Use cases:**
- **State preservation**: ensure task ledger entries, enforcement state, and current objective survive compaction
- **Memory injection**: inject a synthetic "here's what's happening" message after compaction so the agent doesn't lose context
- **Budget-aware compaction**: tune compaction aggressiveness based on remaining session budget

---

## 3. Implementation Plan

### Phase 1: Model Call Hooks (highest impact)

**Timeline:** 2-3 sessions
**Priority:** Critical — these hooks enable budget enforcement, model routing, and output filtering. Without them, all model interactions are invisible to enforcement.

| Step | Deliverable |
|------|-------------|
| 1.1 | Define `model.call.before` and `model.call.after` TypeScript types in a shared `plugin-api.d.ts` |
| 1.2 | Implement the hook dispatching in the opencode framework layer (registration + invocation) |
| 1.3 | Port `model_utilization` logic from CLI-side PreToolUse to `model.call.before`: enforce sonnet-dominant ratio before API call |
| 1.4 | Implement `model.call.after` output filter: scan for PII/secrets in model responses |
| 1.5 | Implement `model.call.before` budget enforcement: cap maxTokens per call |
| 1.6 | Runtime tests for both hooks (per self-test quality policy: invoke actual hook, assert return) |

### Phase 2: Agent Thinking Hooks

**Timeline:** 2-3 sessions
**Priority:** High — enables CoT quality analysis and anti-loop detection. The "agent loops on the same tool call" failure mode has no current mechanical detection.

| Step | Deliverable |
|------|-------------|
| 2.1 | Define `agent.think.before` and `agent.think.after` types |
| 2.2 | Implement context injection hook: add policy reminders, task ledger state to CoT context |
| 2.3 | Implement reasoning quality checker: detect circular reasoning, premature stop intent |
| 2.4 | Implement anti-loop detection: detect 3+ identical tool calls in a row, inject correction |
| 2.5 | Runtime tests for both hooks |

### Phase 3: Task Lifecycle Hooks

**Timeline:** 2-3 sessions
**Priority:** Medium — enables automatic result codification and task deduplication. Addresses the "nothing-dropped" failure mode from BUGS.md.

| Step | Deliverable |
|------|-------------|
| 3.1 | Define `task.dispatch.before` and `task.complete.after` types |
| 3.2 | Implement dispatch validation: verify prompt includes required directives, check deduplication |
| 3.3 | Implement result auto-codification: update TASKS.md, SESSION.md on subagent completion |
| 3.4 | Implement completion audit: log all results to trajectory store |
| 3.5 | Runtime tests for both hooks |

### Phase 4: Human Escalation + Session Compaction Hooks

**Timeline:** 1-2 sessions
**Priority:** Medium-low — human escalation prevention is already handled by enforce-stop's `AskUserQuestion` deny; this hook adds finer control. Session compaction preservation is important for long sessions.

| Step | Deliverable |
|------|-------------|
| 4.1 | Define `human.escalation.before` and `session.compact.before` types |
| 4.2 | Implement escalation guard: deny questions when agent could self-answer |
| 4.3 | Implement compaction state preservation: inject task ledger + enforcement state post-compaction |
| 4.4 | Runtime tests for both hooks |

---

## 4. Hook API Design

### 4.1 Registration

Hooks are registered in `opencode.json` under the existing `plugin` array. Each plugin exports a map of hook names to handler functions:

```typescript
// Plugin shape
interface Plugin {
  "model.call.before"?: HookHandler<ModelCallBeforeInput, ModelCallBeforeOutput>;
  "model.call.after"?: HookHandler<ModelCallAfterInput, ModelCallAfterOutput>;
  "agent.think.before"?: HookHandler<AgentThinkBeforeInput, AgentThinkBeforeOutput>;
  "agent.think.after"?: HookHandler<AgentThinkAfterInput, AgentThinkAfterOutput>;
  "task.dispatch.before"?: HookHandler<TaskDispatchBeforeInput, TaskDispatchBeforeOutput>;
  "task.complete.after"?: HookHandler<TaskCompleteAfterInput, TaskCompleteAfterOutput>;
  "human.escalation.before"?: HookHandler<HumanEscalationBeforeInput, HumanEscalationBeforeOutput>;
  "session.compact.before"?: HookHandler<SessionCompactBeforeInput, SessionCompactBeforeOutput>;
  // existing hooks
  "tool.execute.before"?: HookHandler<any, any>;
  "tool.execute.after"?: HookHandler<any, any>;
}
```

### 4.2 Handler Signature

Every hook handler receives `(input, output)` where:
- `input` is the current state (read-only, but mutation isn't prevented)
- `output` is a mutable object — modify its fields to change behavior

```typescript
type HookHandler<I, O> = (input: I, output: O) => Promise<void>;
```

### 4.3 Blocking Semantics

To block an operation, throw an error:

```typescript
"model.call.before": async (input, output) => {
  if (input.budget && exceededSessionBudget(input)) {
    throw Object.assign(new Error("Session budget exhausted"), {
      permissionDecision: "deny",
      reason: "budget_exhausted",
      suggestedAction: "increase budget or split task",
    });
  }
}
```

The framework catches the error and:
- If `permissionDecision === "deny"`: refuse the operation, return controlled error to agent
- Otherwise: treat as a plugin crash, fail-open (allow the operation)

### 4.4 Fail-Open Guarantee

All hook invocations are wrapped in try/catch. A plugin that throws unexpectedly (ReferenceError, TypeError) does NOT block the operation — the error is logged and the operation proceeds. This matches the existing `tool.execute.before` fail-open pattern in all 13 enforcement plugins.

### 4.5 Execution Order

Multiple plugins may register for the same hook. They execute in registration order (the order in `opencode.json`'s `plugin` array). Each plugin sees the `output` as modified by previous plugins in the chain. A plugin that throws with `permissionDecision: "deny"` short-circuits the chain — remaining plugins do not execute.

### 4.6 Subagent Isolation

Existing subagent guard applied to all new hooks:

```typescript
function _isSubagent(): boolean {
  if (process.env.OPENCODE_SUBAGENT === "1") return true;
  try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`); } catch { return false; }
}
```

Subagents skip ALL enforcement hooks. The orchestrator enforces policy — subagents execute tools directly.

---

## 5. Integration with Existing Enforcement

### 5.1 Plugin Migration Path

Existing plugins migrate incrementally — no one-shot rewrite:

| Current Pattern | New Hook | Plugin(s) Affected |
|-----------------|----------|-------------------|
| `tool.execute.before` scanning task prompts for dispatch patterns | `task.dispatch.before` | enforce-deadline, enforce-enhancement-ratio, enforce-clean-tree |
| `tool.execute.before` blocking `AskUserQuestion` | `human.escalation.before` | enforce-stop (partial) |
| `experimental.text.complete` scanning output for stop patterns | `agent.think.after` | enforce-stop |
| No equivalent (model calls invisible) | `model.call.before` / `model.call.after` | New: enforce-budget, enforce-model-ratio |
| No equivalent (compaction invisible) | `session.compact.before` | New: enforce-compaction-state |

Existing plugins continue to use their current hooks while new hooks are added. Migration is progressive — a plugin can register for both old and new hooks simultaneously during the transition period.

### 5.2 opencode.json Registration

New hooks are registered by adding new plugins or extending existing ones:

```json
{
  "plugin": [
    "./.opencode/plugin/enforce-make.ts",
    "./.opencode/plugin/enforce-floor.ts",
    "./.opencode/plugin/enforce-delegate.ts",
    "./.opencode/plugin/enforce-stop.ts",
    "./.opencode/plugin/enforce-session-start.ts",
    "./.opencode/plugin/enforce-deadline.ts",
    "./.opencode/plugin/enforce-deletion-gate.ts",
    "./.opencode/plugin/enforce-no-suppressions.ts",
    "./.opencode/plugin/enforce-no-wait.ts",
    "./.opencode/plugin/enforce-commit-lock.ts",
    "./.opencode/plugin/enforce-clean-tree.ts",
    "./.opencode/plugin/enforce-verified-claims.ts",
    "./.opencode/plugin/enforce-multitask.ts",
    "./.opencode/plugin/enforce-enhancement-ratio.ts",
    "./.opencode/plugins/watchdog.ts",
    "./.opencode/plugin/enforce-budget.ts",
    "./.opencode/plugin/enforce-compaction.ts"
  ]
}
```

### 5.3 Green/Brown Plugin Coexistence

During Phase 1-4 rollout:
- **Brown plugins** (existing): continue using `tool.execute.before/after` + `text.complete`
- **Green plugins** (new): use the new lifecycle hooks
- **Hybrid plugins** (migrated): register for both old and new hooks, with env-var gating to switch between them (`GLUDD_LIFECYCLE_HOOKS=1`)

### 5.4 Testing Requirements

Per the self-test quality policy (AGENTS.md), every new hook requires:
1. **Structural tests**: verify TypeScript types compile, hook exports exist in plugin manifest
2. **Behavioral tests**: invoke the actual hook function with constructed `(input, output)` and assert on return value or error thrown
3. **Isolation tests**: verify subagent guard skips enforcement when `OPENCODE_SUBAGENT=1`
4. **Fail-open tests**: verify corrupt input / thrown TypeError does not block the operation

---

## 6. Design Decisions

| Decision | Rationale |
|----------|-----------|
| `(input, output)` not `(event)` | Matching existing tool.execute pattern. Output mutation is simpler than return-value replacement for chain-of-plugins execution. |
| Throw to deny, not return value | Throw with `permissionDecision: "deny"` is unambiguous. Return-value ambiguity ("did this plugin mean to allow or just forget to return?") caused bugs in earlier guardrail iterations. |
| Registration order = execution order | Deterministic, inspectable. No priority field needed. Plugin order in `opencode.json` IS the priority order. |
| Mutable output, not immutable | Chained plugins modify the same output object — plugin N sees plugin N-1's modifications. Required for progressive filtering (e.g., budget-1 caps tokens, budget-2 further restricts). |
| Subagents skip ALL hooks | Subagent enforcement isolation is already proven with the 13 existing plugins. Extending the same guard to 8 new hook types is zero-risk. |
| Phase 1 first (model.call) | Model calls are the biggest blind spot. Every other hook can be approximated with tool.execute; model calls cannot. |

---

## 7. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Hook chain latency adds overhead per model call | Medium | Low (1-2ms per hook) | Each hook handler is synchronous and lightweight; chain length is capped at plugin count (~15) |
| Plugin crash in new hook blocks operation | Low | High (blocked agent) | Fail-open guarantee: all hook invocations wrapped in try/catch; unhandled errors logged, operation proceeds |
| Migration causes enforcement gap | Medium | High | Hybrid plugins register for both old + new hooks; old hooks remain active until new hooks are verified |
| New hooks enable infinite recursion | Low | Medium | Plugins that dispatch from within a hook could re-trigger the same hook; detect via `_hookDepth` counter, deny at depth > 2 |
| Type mismatch between framework and plugin | Medium | Medium | Shared `plugin-api.d.ts` types published with each phase; versioned; plugin compile-checked against framework version |

---

## 8. Success Criteria

1. **Phase 1 complete**: `model.call.before` and `model.call.after` hooks fire, existing model-utilization logic ported to use them
2. **All 8 new hooks have runtime tests** (behavioral, not just structural)
3. **Zero enforcement regression**: all 52 existing hook-runtime tests continue to pass
4. **No subagent enforcement leakage**: subagents skip new hooks as verified by isolation tests
5. **Fail-open verified**: corrupt state/input on any new hook allows operation to proceed

---

## 9. References

- Amazon Strands Agents Evals SDK — hook surface design pattern for agent lifecycle interception
- `AGENTS.md` — Self-Test Quality policy (structural vs behavioral tests)
- `AGENTS.md` — Subagent Enforcement Isolation section
- `AGENTS.md` — Guardrail Integrity Policy (never weaken a guardrail)
- `docs/AGENT_EVALUATION_FRAMEWORK.md` — AG.1 trajectory capture design (consumer of new hooks)
- `opencode.json` — plugin registration + permission model
- `.opencode/plugin/enforce-make.ts` — reference implementation of `tool.execute.before/after` pattern
