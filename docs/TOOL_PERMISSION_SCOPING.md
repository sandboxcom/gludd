# AG.4 — Tool Permission Scoping for Subagent Dispatch

**Version:** 1.0 (2026-07-13)
**Dependencies:** `AgentRegistry`, `AgentDispatcher`, `CapabilityLattice`

---

## 1. Overview

When an agent dispatches a subagent, the subagent must not gain capabilities
beyond the parent. Enforced at two levels:

1. **Coarse** — `_check_capability_escalation()` in `agents/dispatcher.py:138`:
   denies dispatch if child has broader `AgentPermission` fields than parent.
2. **Fine** — `PermissionEvaluator.may_use()` in `permissions/tool_permissions.py:249`:
   evaluates specific (tool, action, scope) tuples against a `ToolPermissionSpec`.

Rule: a subagent's effective tool permissions = intersection of its own spec and
the parent's spec, narrowed to the dispatch scope.

---

## 2. Permission Layers

### 2.1 Coarse: `AgentPermission` (dispatch-time gate)

Fields: `can_edit`, `can_bash`, `can_read`, `can_dispatch_subagents`,
`allowed_subagents`. Compared parent-to-child; wider child → dened. Fail-closed.

### 2.2 Fine: `ToolPermissionSpec` (tool-use-time gate)

| Component | Role |
|---|---|
| `ToolPermission` | Per-tool rule: tool name, allowed_actions, denied_actions, optional scope |
| `ToolPermissionSpec` | Ordered tuple of `ToolPermission` rules for a role |
| `CapabilityLattice` | Hierarchical role capabilities (reader→writer→coder→admin) |
| `PermissionEvaluator` | Evaluator: deny-first, allow-second, lattice-check, default-deny |

### 2.3 Scope narrowing

Effective scope = narrowest of: dispatch `project_id`, parent scope, child scope.
Scope wildcards (`project:*`) resolve to concrete project at dispatch. A
project-scoped parent cannot dispatch a global child that escapes its scope.

---

## 3. Evaluation Order at Dispatch

```text
1. AgentRegistry.can_invoke(invoker,target)  — fnmatch on allowed_subagents
2. _check_capability_escalation()             — coarse AgentPermission gate
3. (Proposed) tool-permission intersection    — fine ToolPermissionSpec gate
4. _check_nesting_depth()                     — max depth
5. _check_rate_limiter()                      — rate window
6. _check_spiral()                            — re-dispatch loop
```

---

## 4. Subagent Tool Intersection

```python
effective = PermissionEvaluator.intersect(
    child_spec, parent_spec, scope=dispatch_project_id
)
```

Intersection rules (deny-takes-precedence):
- **Tool set**: child sees only tools present in BOTH specs.
- **Actions**: allowed only if BOTH specs allow it.
- **Deny wins**: EITHER deny → denied.
- **Scope**: narrowed to concrete project_id.

A parent with `read_file:[read]` dispatching a child with
`read_file:[read,write]` → child gets `read_file:[read]` only.

---

## 5. CapabilityLattice + Registry

Built-in chain: `reader→writer→coder→admin`. Each role inherits parent actions
transitively. Wildcard (`"*"`) allow skips lattice check.

Built-in agents (from `agents/registry.py`): build (edit+bash+dispatch+wildcard),
plan (read+dispatch explore only), explore/general/research subagents. Unknown
agents get default-deny.

---

## 6. Related Modules

| Module | Purpose |
|---|---|
| `permissions/tool_permissions.py` | ToolPermission, ToolPermissionSpec, CapabilityLattice, PermissionEvaluator |
| `agents/types.py` | AgentPermission, AgentTask, AgentConfig |
| `agents/registry.py` | AgentRegistry, can_invoke(), default_registry() |
| `agents/dispatcher.py` | AgentDispatcher, capability escalation check |
| `security/capability_lattice.py` | Daemon-side role capabilities, self-modify guards |
| `tests/unit/test_ag4_tool_permissions.py` | 30 tests covering all layers |
