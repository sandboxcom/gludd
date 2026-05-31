---
name: codify_directive
description: Port an opencode/AGENTS.md behavioral directive into the hottentot agent's codified AgentBehavior system
model_profile: null
tools: [read, write, edit, glob, grep, bash]
trigger_patterns: ["codify directive", "port directive", "add behavior rule", "codify behavior"]
tags: [meta, guardrail, behavior, skill]
---

# Codify Directive Skill

## Purpose

When you identify a behavioral directive in opencode's configuration (AGENTS.md,
opencode.json, .opencode/plugin/*.ts) that should be ported to the hottentot
agent's own AgentBehavior system, follow this exact workflow.

## The Pattern: Three-Layer Directive Porting

Every opencode behavioral directive maps to a hottentot agent codified directive
through a three-layer porting process:

### Layer 1: AgentBehavior Model Field

1. Open `src/agentic_harness/agents/behavior.py`
2. Add a new field to `AgentBehavior` with appropriate type and default:
   ```python
   new_directive: bool = True  # or str, list[str], int, etc.
   ```
3. If the directive has sub-structure, create a Pydantic sub-model (like `GuardrailConfig`).
4. Update `default_primary_behavior()` and `default_subagent_behavior()` if needed.

### Layer 2: BehaviorRenderer Section

1. In the same file, find `BehaviorRenderer.render()`.
2. Add a conditional section:
   ```python
   if behavior.new_directive:
       sections.append("## New Directive Title")
       sections.append("Description of what the agent must do.")
       sections.append("")
   ```
3. The rendered section becomes part of the agent's system prompt.

### Layer 3: Three-Layer Guardrail (if the directive is a restriction)

If the directive restricts behavior (not just enables it), implement at all three
guardrail layers:

1. **Config layer** — validation in the Pydantic model (e.g., `@model_validator`)
2. **Hook layer** — runtime check in the event loop or agent dispatch
3. **Prompt layer** — the BehaviorRenderer section from Layer 2 above

## Workflow

```
1. Identify directive in opencode config (AGENTS.md, opencode.json, plugin)
   |
   v
2. Write a FAILING test in tests/unit/test_agent_behavior.py
   - Test the new AgentBehavior field
   - Test the rendered prompt section
   - Test any validation logic
   |
   v
3. Add field to AgentBehavior model
   - Choose appropriate type (bool, str, list, sub-model)
   - Set sensible default
   - Update factory functions if needed
   |
   v
4. Add section to BehaviorRenderer.render()
   - Conditionally render based on the field
   - Use clear, imperative language ("You MUST...", "Do NOT...")
   |
   v
5. Add guardrail validation if needed
   - Config: Pydantic validator
   - Hook: runtime check in event loop / dispatch
   - Prompt: already done in step 4
   |
   v
6. Run tests: make test-unit
   |
   v
7. Update AGENTS.md with the new directive section
   |
   v
8. Update SESSION.md with what was codified
   |
   v
9. Commit: make test-and-commit
```

## Examples of Ported Directives

| AGENTS.md Directive | AgentBehavior Field | BehaviorRenderer Section |
|---------------------|--------------------|-------------------------| 
| Task Completion Policy | `completion_policy: str` | "## Task Completion" |
| Self-Directed Work | `self_directed_work: bool` | "## Self-Directed Work" |
| TDD Policy | `tdd_enforced: bool` | "## TDD Policy" |
| Commit-After-Green | `commit_after_green: bool` | "## Commit-After-Green" |
| Evidence-Based Response | `evidence_required: bool` | "## Evidence-Based Responses" |
| Bash Command Policy | `allowed_command_patterns: list[str]` | "## Command Policy" |
| Guardrail Meta-Rule | `guardrail: GuardrailConfig` | "## Guardrail Policy" |
| Session Persistence | `session_persistence: bool` | "## Session Persistence" |
| Stop Conditions | `stop_conditions: list[str]` | "## Stop Conditions" |

## Checklist

Before considering a directive fully codified:

- [ ] AgentBehavior has the new field with proper type and default
- [ ] BehaviorRenderer conditionally renders the section
- [ ] Unit test exists for the field
- [ ] Unit test exists for the rendered section
- [ ] If restriction: all three guardrail layers implemented
- [ ] default_primary_behavior() and default_subagent_behavior() updated if needed
- [ ] E2E test covers the full pipeline (config -> render -> prompt)
- [ ] AGENTS.md updated if it's an opencode directive too
- [ ] SESSION.md updated
- [ ] Tests pass, committed
