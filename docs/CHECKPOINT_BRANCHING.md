# AG.9 — Checkpoint Branching Design

**Status:** design | **Effort:** medium | **Lines:** ≤80 target

## 1. Problem

Agent execution is linear — one path from start to finish. When a strategy fails
(broken code, bad prompt, wrong tool choice), the only recourse is to restart
the whole invocation. There is no way to:

- **Fork** from a known-good intermediate checkpoint and try an alternative.
- **Compare** multiple branches against the same input, picking the best result.
- **Restore** a prior checkpoint when a downstream failure reveals a poor choice.

## 2. Solution

A lightweight **checkpoint-branch** layer: named snapshots of agent/graph state
that can be forked, restored, and compared. No heavy LangGraph runtime dependency
at storage time — branches carry metadata + state blobs. The orchestrator replays
a branch by loading its state into a new invocation.

## 3. Core Types

```text
CheckpointBranch(branch_id, name, checkpoint_id, state, parent_branch?, description?)
BranchResult(branch_id, status, output, error?, duration_ms)
```

`BranchManager` persists branches as JSON files under `.gludd/branches/`.

## 4. Operations

| Operation | Signature | Behavior |
|---|---|---|
| `create_branch` | (name, checkpoint_id, state) → Branch | Write JSON blob |
| `restore_branch` | (branch_id) → Branch | Re-hydrate from disk |
| `list_branches` | () → [Branch] | Enumerate all |
| `delete_branch` | (branch_id) → bool | Remove file |
| `compare_branches` | ([branch_id]) → [Result] | Batch load for A/B |

## 5. Persistence

Atomic writes (temp file + `os.replace`).  Corrupt files are silently skipped on
enumeration (fail-open).  Each branch carries a UTC created_at timestamp.  Parent
links support lineage tracing.

## 6. Integration Points

- **Graph checkpointer:** branches fork from the checkpointer's stored state.
- **Orchestrator:** replays a `CheckpointBranch.state` into a new graph
  invocation with modified parameters (different prompt, model, constraints).
- **Comparison:** `compare_branches` feeds into the evaluation framework
  (AG.1) for A/B scoring.

## 7. Open Questions

- Should branch replay share the existing `TickCheckpointer` store or use a
  separate directory?  (Current design: separate, to avoid polluting operational
  checkpoint state with exploratory branches.)
- Should stale branches auto-expire?  (Current design: no — explicit delete only.)
