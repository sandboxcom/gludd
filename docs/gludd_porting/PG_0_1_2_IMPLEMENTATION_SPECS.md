# PG-0 / PG-1 / PG-2 Implementation Specs

Three tightly-scoped changes that wire `AgentBehavior` / `BehaviorRenderer` into the live
system-prompt path and add two first-class behavioral rules.

---

## Implementation Order

PG-0 is the gating prerequisite. PG-1 and PG-2 add fields and renderer sections that are
independently testable at the behavior layer as soon as their fields exist, but neither rule
reaches the live model prompt until PG-0 lands. Implement in order: PG-0 first (makes the
renderer live), then PG-1 and PG-2 (can be done in one commit since they follow the same
pattern). The PG-1/PG-2 renderer tests pass without PG-0; the engine-level assertion (that
the live prompt contains the rendered rules) requires PG-0 to be wired first.

---

## PG-0 — Wire BehaviorRenderer into the live system prompt

### Problem

`_build_system_prompt(job: JobSpec) -> str` at `engine.py:58` ignores `AgentBehavior`.
Every model call therefore receives a generic 5-line prompt regardless of what behavior
config has been set. `BehaviorRenderer` is never called during execution.

### Files changed

| File | Location |
|------|----------|
| `src/general_ludd/execution/engine.py` | imports (line 18 area), `_build_system_prompt` (line 58), `ExecutionEngine.__init__` (line 154), `execute()` call site (line 234) |
| `tests/unit/test_engine_behavior_wiring.py` | new file |

---

### Edit 1 — Add imports to engine.py

**File:** `src/general_ludd/execution/engine.py`

**Location:** After the existing imports block (after line 18, which imports `JobSpec`).

```text
old_string:
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_return import TaskReturn

new_string:
from general_ludd.agents.behavior import AgentBehavior, BehaviorRenderer
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_return import TaskReturn
```

---

### Edit 2 — Change `_build_system_prompt` signature and body

**File:** `src/general_ludd/execution/engine.py`

**Location:** Lines 58–71, the entire `_build_system_prompt` function.

```python
old_string:
def _build_system_prompt(job: JobSpec) -> str:
    lines: list[str] = []
    lines.append(
        "You are a coding agent. Generate code changes for the following task."
    )
    if job.skill_body:
        rendered = _render_skill_body(job.skill_body)
        lines.append(f"\nGuidelines:\n{rendered}")
    lines.append("\nOutput format:")
    lines.append("- Use fenced code blocks for code.")
    lines.append(
        "- Prefix each file with 'FILE: <path>' followed by the content."
    )
    return "\n".join(lines)

new_string:
def _build_system_prompt(job: JobSpec, behavior: AgentBehavior | None = None) -> str:
    lines: list[str] = []
    lines.append(
        "You are a coding agent. Generate code changes for the following task."
    )
    if job.skill_body:
        rendered = _render_skill_body(job.skill_body)
        lines.append(f"\nGuidelines:\n{rendered}")
    lines.append("\nOutput format:")
    lines.append("- Use fenced code blocks for code.")
    lines.append(
        "- Prefix each file with 'FILE: <path>' followed by the content."
    )
    base = "\n".join(lines)
    if behavior is not None:
        renderer = BehaviorRenderer()
        behavior_block = renderer.render(behavior)
        return behavior_block + "\n\n" + base
    return base
```

---

### Edit 3 — Add `behavior` parameter to `ExecutionEngine.__init__`

**File:** `src/general_ludd/execution/engine.py`

**Location:** Lines 154–168, the `ExecutionEngine.__init__` method.

```text
old_string:
    def __init__(
        self,
        model_gateway: Any = None,
        workspace_path: str = "/tmp/gludd-workspace",
        benchmark_recorder: Any = None,
        metrics_collector: Any = None,
        budget_guard: Any = None,
    ) -> None:
        self._model_gateway = model_gateway
        self.workspace_path = workspace_path
        self._benchmark_recorder = benchmark_recorder
        self._metrics_collector = metrics_collector
        self._budget_guard = budget_guard
        self._background_tasks: set[asyncio.Task[Any]] = set()
        os.makedirs(workspace_path, exist_ok=True)

new_string:
    def __init__(
        self,
        model_gateway: Any = None,
        workspace_path: str = "/tmp/gludd-workspace",
        benchmark_recorder: Any = None,
        metrics_collector: Any = None,
        budget_guard: Any = None,
        behavior: AgentBehavior | None = None,
    ) -> None:
        self._model_gateway = model_gateway
        self.workspace_path = workspace_path
        self._benchmark_recorder = benchmark_recorder
        self._metrics_collector = metrics_collector
        self._budget_guard = budget_guard
        self._behavior = behavior
        self._background_tasks: set[asyncio.Task[Any]] = set()
        os.makedirs(workspace_path, exist_ok=True)
```

---

### Edit 4 — Pass `self._behavior` at the call site in `execute()`

**File:** `src/general_ludd/execution/engine.py`

**Location:** Line 234 inside `execute()`.

```text
old_string:
        system_prompt = _build_system_prompt(job)

new_string:
        system_prompt = _build_system_prompt(job, behavior=self._behavior)
```

---

### TDD tests for PG-0

**File:** `tests/unit/test_engine_behavior_wiring.py` (new file — does not exist today)

**Class:** `TestBuildSystemPromptBehaviorWiring`

```python
"""Tests that _build_system_prompt wires AgentBehavior into the live prompt (PG-0)."""

from __future__ import annotations

import pytest

from general_ludd.agents.behavior import AgentBehavior
from general_ludd.execution.engine import _build_system_prompt
from general_ludd.schemas.job import JobSpec


def _minimal_job() -> JobSpec:
    return JobSpec(
        job_id="JOB-PG0",
        playbook="code",
        queue="core",
    )


class TestBuildSystemPromptBehaviorWiring:
    def test_system_prompt_contains_rendered_behavior_when_wired(self):
        """FAILS today: _build_system_prompt ignores the behavior arg.

        After PG-0 lands, the rendered behavior block (which includes the
        'Do NOT pause' line from the completion_policy section of
        BehaviorRenderer.render()) must appear in the returned string.
        """
        job = _minimal_job()
        behavior = AgentBehavior(completion_policy="complete_all")
        result = _build_system_prompt(job, behavior=behavior)
        # BehaviorRenderer.render() emits this exact phrase for completion_policy="complete_all"
        assert "Do NOT pause to ask" in result

    def test_system_prompt_generic_when_no_behavior(self):
        """Old path must still work when no behavior is supplied."""
        job = _minimal_job()
        result = _build_system_prompt(job)
        assert "You are a coding agent" in result
```

**Why `test_system_prompt_contains_rendered_behavior_when_wired` fails today:**
`_build_system_prompt(job: JobSpec)` at engine.py:58 ignores any extra argument and never
calls `BehaviorRenderer`. The assertion `"Do NOT pause to ask" in result` will fail because
the current output is only the 5-line generic prompt. After Edit 2 above lands, the function
accepts `behavior` and prepends the rendered block, so the assertion passes.

---

## PG-1 — `never_block_on_questions` first-class rule

### Problem

The "never pause to ask" constraint lives only in the user's memory file
(`agent-orchestration-prefs.md`). It is never injected into the system prompt of a live model
call. Adding it as a first-class `AgentBehavior` field makes it renderable and auditable.

### Files changed

| File | Location |
|------|----------|
| `src/general_ludd/agents/behavior.py` | `AgentBehavior` field list (after line 49), `BehaviorRenderer.render()` (after line 236), `default_primary_behavior()` (line 249), `default_subagent_behavior()` (line 263) |
| `tests/unit/test_agent_behavior.py` | new class `TestNeverBlockOnQuestions` |

---

### Edit 1 — Add `never_block_on_questions` field to `AgentBehavior`

**File:** `src/general_ludd/agents/behavior.py`

**Location:** Line 49 — after `self_improve_interval: int = 0`.

```text
old_string:
    max_retries: int = 3
    self_improve_interval: int = 0

new_string:
    max_retries: int = 3
    self_improve_interval: int = 0
    never_block_on_questions: bool = True
```

---

### Edit 2 — Add renderer section for `never_block_on_questions`

**File:** `src/general_ludd/agents/behavior.py`

**Location:** Lines 227–238 — after the `self_improve_interval` block and before
`return "\n".join(sections)`.

```text
old_string:
        if behavior.self_improve_interval > 0:
            sections.append("## Self-Improvement Cycle")
            sections.append(
                f"Every {behavior.self_improve_interval} ticks, run self-improvement analysis "
                "to discover gaps and create fix todos autonomously."
            )
            sections.append(
                "Gaps found are enqueued as high-priority self_improve todos."
            )
            sections.append("")

        return "\n".join(sections)

new_string:
        if behavior.self_improve_interval > 0:
            sections.append("## Self-Improvement Cycle")
            sections.append(
                f"Every {behavior.self_improve_interval} ticks, run self-improvement analysis "
                "to discover gaps and create fix todos autonomously."
            )
            sections.append(
                "Gaps found are enqueued as high-priority self_improve todos."
            )
            sections.append("")

        if behavior.never_block_on_questions:
            sections.append("## Never Block On Questions")
            sections.append(
                "Never pause work to ask the user a question. Default to action: make a "
                "reasonable assumption, state it explicitly, and keep going."
            )
            sections.append(
                "Only stop for a stop_condition (missing credentials or irreversible destructive action)."
            )
            sections.append("")

        return "\n".join(sections)
```

---

### Edit 3 — Enable in `default_primary_behavior()`

**File:** `src/general_ludd/agents/behavior.py`

**Location:** Lines 248–260, `default_primary_behavior()`.

```python
old_string:
def default_primary_behavior() -> AgentBehavior:
    return AgentBehavior(
        completion_policy="complete_all",
        self_directed_work=True,
        tdd_enforced=True,
        commit_after_green=True,
        evidence_required=True,
        atomic_commits=True,
        session_persistence=True,
        guardrail=GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True),
        allowed_command_patterns=["make *"],
        stop_conditions=["missing_credentials", "environment_change"],
    )

new_string:
def default_primary_behavior() -> AgentBehavior:
    return AgentBehavior(
        completion_policy="complete_all",
        self_directed_work=True,
        tdd_enforced=True,
        commit_after_green=True,
        evidence_required=True,
        atomic_commits=True,
        session_persistence=True,
        guardrail=GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True),
        allowed_command_patterns=["make *"],
        stop_conditions=["missing_credentials", "environment_change"],
        never_block_on_questions=True,
    )
```

---

### Edit 4 — Enable in `default_subagent_behavior()`

**File:** `src/general_ludd/agents/behavior.py`

**Location:** Lines 263–275, `default_subagent_behavior()`.

```python
old_string:
def default_subagent_behavior() -> AgentBehavior:
    return AgentBehavior(
        completion_policy="complete_all",
        self_directed_work=False,
        tdd_enforced=True,
        commit_after_green=True,
        evidence_required=True,
        atomic_commits=True,
        session_persistence=True,
        guardrail=GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True),
        allowed_command_patterns=["make *"],
        stop_conditions=["missing_credentials", "environment_change"],
    )

new_string:
def default_subagent_behavior() -> AgentBehavior:
    return AgentBehavior(
        completion_policy="complete_all",
        self_directed_work=False,
        tdd_enforced=True,
        commit_after_green=True,
        evidence_required=True,
        atomic_commits=True,
        session_persistence=True,
        guardrail=GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True),
        allowed_command_patterns=["make *"],
        stop_conditions=["missing_credentials", "environment_change"],
        never_block_on_questions=True,
    )
```

---

### TDD tests for PG-1

**File:** `tests/unit/test_agent_behavior.py`

**Class:** `TestNeverBlockOnQuestions` (append after the last existing class `TestDefaultBehaviors`)

```python
class TestNeverBlockOnQuestions:
    def test_never_block_renders_section_when_true(self):
        b = AgentBehavior(never_block_on_questions=True)
        result = BehaviorRenderer().render(b)
        assert "Never pause work to ask" in result

    def test_never_block_omits_section_when_false(self):
        b = AgentBehavior(never_block_on_questions=False)
        result = BehaviorRenderer().render(b)
        assert "Never Block On Questions" not in result

    def test_never_block_default_is_true(self):
        b = AgentBehavior()
        assert b.never_block_on_questions is True
```

**Why these fail today:**
`AgentBehavior` has no `never_block_on_questions` field yet (line 49 ends at
`self_improve_interval`). The constructor call `AgentBehavior(never_block_on_questions=True)`
will raise a `ValidationError`, and `AgentBehavior().never_block_on_questions` will raise
`AttributeError`. After Edit 1 and Edit 2 above land, all three assertions pass.

---

## PG-2 — `repair_not_disable` rule

### Problem

Agents occasionally silence test failures by commenting out assertions, adding `xfail` marks,
or deleting the failing test. Making this a first-class behavioral rule puts the prohibition
into every system prompt and makes it auditable.

### Files changed

| File | Location |
|------|----------|
| `src/general_ludd/agents/behavior.py` | `AgentBehavior` field list (after `never_block_on_questions`), `BehaviorRenderer.render()` (after the `never_block_on_questions` block), `default_primary_behavior()`, `default_subagent_behavior()` |
| `tests/unit/test_agent_behavior.py` | new class `TestRepairNotDisable` |

---

### Edit 1 — Add `repair_not_disable` field to `AgentBehavior`

**File:** `src/general_ludd/agents/behavior.py`

**Location:** After the `never_block_on_questions` field added in PG-1.

```text
old_string:
    max_retries: int = 3
    self_improve_interval: int = 0
    never_block_on_questions: bool = True

new_string:
    max_retries: int = 3
    self_improve_interval: int = 0
    never_block_on_questions: bool = True
    repair_not_disable: bool = True
```

---

### Edit 2 — Add renderer section for `repair_not_disable`

**File:** `src/general_ludd/agents/behavior.py`

**Location:** After the `never_block_on_questions` renderer block added in PG-1, before
`return "\n".join(sections)`.

```text
old_string:
        if behavior.never_block_on_questions:
            sections.append("## Never Block On Questions")
            sections.append(
                "Never pause work to ask the user a question. Default to action: make a "
                "reasonable assumption, state it explicitly, and keep going."
            )
            sections.append(
                "Only stop for a stop_condition (missing credentials or irreversible destructive action)."
            )
            sections.append("")

        return "\n".join(sections)

new_string:
        if behavior.never_block_on_questions:
            sections.append("## Never Block On Questions")
            sections.append(
                "Never pause work to ask the user a question. Default to action: make a "
                "reasonable assumption, state it explicitly, and keep going."
            )
            sections.append(
                "Only stop for a stop_condition (missing credentials or irreversible destructive action)."
            )
            sections.append("")

        if behavior.repair_not_disable:
            sections.append("## Fix Means Repair, Never Disable")
            sections.append(
                "When something fails, repair the root cause. Do NOT disable, comment-out, "
                "skip, xfail, or delete the feature or test to make the gate green."
            )
            sections.append(
                "A disable is only legitimate as an explicitly tracked decision with a "
                "follow-up todo — never a silent one."
            )
            sections.append("")

        return "\n".join(sections)
```

---

### Edit 3 — Enable in `default_primary_behavior()`

**File:** `src/general_ludd/agents/behavior.py`

**Location:** `default_primary_behavior()` — add after `never_block_on_questions=True`.

```python
old_string:
        stop_conditions=["missing_credentials", "environment_change"],
        never_block_on_questions=True,
    )


def default_subagent_behavior

new_string:
        stop_conditions=["missing_credentials", "environment_change"],
        never_block_on_questions=True,
        repair_not_disable=True,
    )


def default_subagent_behavior
```

---

### Edit 4 — Enable in `default_subagent_behavior()`

**File:** `src/general_ludd/agents/behavior.py`

**Location:** `default_subagent_behavior()` — add after `never_block_on_questions=True`.

```text
old_string:
        stop_conditions=["missing_credentials", "environment_change"],
        never_block_on_questions=True,
    )

new_string:
        stop_conditions=["missing_credentials", "environment_change"],
        never_block_on_questions=True,
        repair_not_disable=True,
    )
```

---

### TDD tests for PG-2

**File:** `tests/unit/test_agent_behavior.py`

**Class:** `TestRepairNotDisable` (append after `TestNeverBlockOnQuestions`)

```python
class TestRepairNotDisable:
    def test_repair_not_disable_renders_section_when_true(self):
        b = AgentBehavior(repair_not_disable=True)
        result = BehaviorRenderer().render(b)
        assert "repair the root cause" in result

    def test_repair_not_disable_omits_section_when_false(self):
        b = AgentBehavior(repair_not_disable=False)
        result = BehaviorRenderer().render(b)
        assert "Fix Means Repair" not in result

    def test_repair_not_disable_default_is_true(self):
        b = AgentBehavior()
        assert b.repair_not_disable is True
```

**Why these fail today:**
`AgentBehavior` has no `repair_not_disable` field. The constructor call
`AgentBehavior(repair_not_disable=True)` will raise `ValidationError`, and
`AgentBehavior().repair_not_disable` will raise `AttributeError`. After Edit 1 and Edit 2
above land, all three assertions pass.

---

## Quick reference: file/line anchor table

| Spec | File | Old anchor | Change |
|------|------|-----------|--------|
| PG-0 | `engine.py:15` | import block | Add `AgentBehavior, BehaviorRenderer` import |
| PG-0 | `engine.py:58` | `def _build_system_prompt(job: JobSpec) -> str` | Add `behavior` param + prepend logic |
| PG-0 | `engine.py:154` | `ExecutionEngine.__init__` signature | Add `behavior: AgentBehavior \| None = None` param + `self._behavior = behavior` |
| PG-0 | `engine.py:234` | `system_prompt = _build_system_prompt(job)` | Pass `behavior=self._behavior` |
| PG-1 | `behavior.py:49` | `self_improve_interval: int = 0` | Add `never_block_on_questions: bool = True` after |
| PG-1 | `behavior.py:238` | `return "\n".join(sections)` | Add `never_block_on_questions` renderer block before return |
| PG-1 | `behavior.py:249` | `default_primary_behavior()` body | Add `never_block_on_questions=True` |
| PG-1 | `behavior.py:263` | `default_subagent_behavior()` body | Add `never_block_on_questions=True` |
| PG-2 | `behavior.py:50` | `never_block_on_questions: bool = True` | Add `repair_not_disable: bool = True` after |
| PG-2 | `behavior.py:238` | `never_block_on_questions` renderer block | Add `repair_not_disable` renderer block after |
| PG-2 | `behavior.py:249` | `default_primary_behavior()` body | Add `repair_not_disable=True` |
| PG-2 | `behavior.py:263` | `default_subagent_behavior()` body | Add `repair_not_disable=True` |
