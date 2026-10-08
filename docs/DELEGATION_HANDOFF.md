# AG.7 — Agent Delegation & Handoff Architecture

**Status:** design | **Effort:** medium | **Parent task:** TASKS.md AG.7

## 1. Overview

Inter-agent task handoff allows one agent to transfer a running task (plus
context) to another agent — not a fresh dispatch, but a mid-execution
transfer of responsibility. The invariant: a handoff MUST NOT grant the
receiving agent any capability the originating agent does not hold.

## 2. Capability Non-Escalation

Already partially enforced in `AgentDispatcher._check_capability_escalation()`
(`src/general_ludd/agents/dispatcher.py:138`). On every `dispatch_one`, the
dispatcher compares the parent agent's `AgentPermission` against the child's:

```text
child.can_edit              ⊆  parent.can_edit
child.can_bash              ⊆  parent.can_bash
child.can_read              ⊆  parent.can_read
child.can_dispatch_subagents  ⊆  parent.can_dispatch_subagents
child.allowed_subagents     ⊆  parent.allowed_subagents
```

A handoff extends this check: the originating agent is NOT the direct
dispatcher — handoff traverses a chain of custody. The receiver's effective
permissions are the **intersection** of every agent in the handoff chain:

```text
effective = ∩ { sender.permissions, …chain…, receiver.permissions }
```

No agent in the chain can inject a capability absent from every participant.

### 2.1 Handoff-Depth Guard

The existing `_check_nesting_depth()` (`dispatcher.py:120`) limits task
depth to `max_nesting_depth`. Handoffs increment depth by 1 per hop; the
same limit applies. A handoff at the depth ceiling fails immediately.

## 3. Handoff Protocol

### 3.1 Lifecycle

```text
  Agent-A (running)           Dispatcher           Agent-B (receiving)
      │                          │                        │
      │──handoff_task(task_id,──▶│                        │
      │   target_agent,          │                        │
      │   handoff_context)       │                        │
      │                          │──validate recipient───▶│ (exists? enabled?
      │                          │                        │  can_receive_handoffs?)
      │                          │──check capability──────│ (intersection ⊆
      │                          │   non-escalation        │  sender perms)
      │                          │──transfer context──────▶│ (task state,
      │                          │   (AgenticContext)      │  artifacts, lineage)
      │                          │                        │
      │   Agent-A ← released ◀──│                        │──execute remainder─▶
      │                          │                        │
      │                          │◀──result────────────────│ (AgentTaskResult)
```

### 3.2 Handoff Record (`HandoffSpec`)

A new dataclass in `src/general_ludd/agents/types.py`:

```python
@dataclass
class HandoffSpec:
    handoff_id: str           # UUID, unique per handoff
    origin_task_id: str       # task being handed off
    sender_agent: str         # agent initiating the handoff
    target_agent: str         # intended recipient
    reason: str               # human-readable reason for the handoff
    context_snapshot: dict    # serialized task state at handoff time
    created_at: float         # monotonic timestamp
    depth: int                # current nesting depth
    chain: list[str]          # ordered list of agents in the handoff chain
```

### 3.3 Recipient Validation

The dispatcher gates every handoff on three checks (in order):

1. **Target exists & enabled** — `registry.get(target)` returns a non-None,
   enabled `AgentConfig`. Absent/disabled → handoff rejected with status
   `failed`.

2. **Recipient accepts handoffs** — a new `AgentPermission.can_receive_handoffs`
   boolean (default `False`). Only agents explicitly opted-in to handoff
   reception may receive. Prevents accidental handoff to read-only agents.

3. **Handoff-chain capability intersection** — the sender's permissions
   intersected with every agent in `HandoffSpec.chain`, then intersected
   with the receiver. Any capability present in the receiver but absent
   from the intersection is stripped (not the handoff — the capability is
   removed from the task's tool set at transfer time). If this results in
   zero capabilities (no edit, no bash, no read), the handoff is rejected
   as a no-op.

### 3.4 Sender-Side Rules

- **Only agents with `can_dispatch_subagents=True` may initiate a handoff.**
  This maps to the existing `AgentRegistry.can_invoke()` check.
- **The sender MUST NOT continue executing after handoff.** The sender's
  task transitions to `handed_off`; its `AgentTaskResult.status` is
  `handed_off` with the `HandoffSpec.handoff_id` in `output`.
- **The handoff chain is appended** — `depth` increments, `chain` gets the
  sender appended. The recipient sees the full chain in context.

## 4. Context Transfer

### 4.1 What Transfers

| Field | Source | Required |
|-------|--------|----------|
| `task.prompt` | original AgentTask | yes |
| `task.description` | original AgentTask | yes |
| `task.project_id` | original AgentTask | yes |
| `task.tools` (pruned) | capability-intersection | yes |
| `handoff_context` | sender-provided dict | yes |
| `artifacts` | sender's result list | no |
| `chain` | HandoffSpec.chain | yes |
| `reason` | sender-provided string | yes |
| `parent_task_id` | original AgentTask | yes |

### 4.2 What Does NOT Transfer

- **Raw tool output** — only structured `artifacts` and `handoff_context`.
- **Sender's agent config** — the receiver's config applies.
- **In-flight subagents** — any subagents spawned by the sender remain owned
  by the sender's task; they are NOT transferred. If the sender had live
  subagents at handoff time, they are cancelled and recorded in `context_snapshot`.
- **Mutable local state** — file handles, in-memory caches, network
  connections. The handoff is a **state-transfer, not a process-migration**.

### 4.3 Context Format

```python
@dataclass
class HandoffContext:
    task_state: str          # free-text summary from the sender
    artifacts: list[str]     # file paths or artifact IDs
    last_output: str         # last meaningful output before handoff
    pending_decisions: list[str]  # unresolved questions for receiver
```

Serialized into `HandoffSpec.context_snapshot`.

## 5. Integration Points

| Component | Change |
|-----------|--------|
| `AgentPermission` (`types.py:18`) | Add `can_receive_handoffs: bool = False` |
| `AgentTask` (`types.py:42`) | Add `handoff_spec: HandoffSpec | None = None` |
| `AgentTaskResult` (`dispatcher.py:33`) | Add `handoff_id: str | None = None` |
| `AgentDispatcher.dispatch_one` | Add `_validate_handoff()` guard before `_check_nesting_depth` |
| `AgentRegistry` | Add `can_receive_handoff(target: str) -> bool` |
| `HandoffSpec` / `HandoffContext` | New dataclasses in `types.py` |

## 6. Test Plan

| Test file | Coverage |
|-----------|----------|
| `tests/unit/test_handoff_spec.py` | HandoffSpec validation, chain integrity, depth guards |
| `tests/unit/test_handoff_context.py` | Context serialization, artifact pruning, tool-set intersection |
| `tests/unit/test_handoff_dispatcher.py` | Dispatch gating: valid handoff, rejected escalation, disabled recipient, chain limit |
| `tests/unit/test_handoff_registry.py` | `can_receive_handoff` integration with `AgentPermission` |

## 7. Edge Cases & Constraints

- **Empty chain**: first-hop handoff (sender→receiver, no prior hops). Depth
  starts at sender's depth + 1.
- **Self-handoff**: sender == receiver → rejected (no-op; use task resumption
  instead).
- **Max depth**: handoff at `max_nesting_depth` → rejected.
- **Handoff to disabled agent**: rejected at recipient-validation stage.
- **Handoff with live subagents**: sender's subagents cancelled; recorded in
  `context_snapshot.subagents_cancelled`.
- **Handoff during pause**: if sender's project is paused, handoff is queued
  until unpause (same as `dispatch_one` pause gate at `dispatcher.py:268`).
