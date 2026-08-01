"""AG.2 — Lifecycle Hook Expansion tests.

Per docs/LIFECYCLE_HOOK_EXPANSION.md §5.4 (Testing Requirements):
  1. Structural tests — types compile, hook exports exist
  2. Behavioral tests — invoke actual hook, assert return
  3. Isolation tests — subagent guard skips enforcement
  4. Fail-open tests — corrupt input allows operation
  5. Chain execution tests — registration order, short-circuit

Coverage: all 8 hook event names across 5 domains.
"""

from __future__ import annotations

import os
import threading
from unittest.mock import patch

import pytest

from general_ludd.ag2_lifecycle import (
    DenyError,
    LifecycleHookSystem,
    dispatch_chain,
)
from general_ludd.ag2_lifecycle.hooks import (
    SubagentGuard,
)
from general_ludd.ag2_lifecycle.types import (
    AgentThinkAfterInput,
    AgentThinkAfterOutput,
    AgentThinkBeforeInput,
    AgentThinkBeforeOutput,
    DispatcherInfo,
    HumanEscalationBeforeInput,
    HumanEscalationBeforeOutput,
    ModelCallAfterInput,
    ModelCallAfterOutput,
    ModelCallBeforeInput,
    ModelCallBeforeOutput,
    SessionCompactBeforeInput,
    SessionCompactBeforeOutput,
    TaskBudget,
    TaskCompleteAfterInput,
    TaskCompleteAfterOutput,
    TaskDispatchBeforeInput,
    TaskDispatchBeforeOutput,
    TaskInfo,
)

# ── helpers ────────────────────────────────────────────────────────────────────

async def _allow_handler(input: object, output: object) -> None:
    pass


async def _deny_handler(input: object, output: object) -> None:
    raise DenyError("test deny", suggested_action="try something else")


async def _crash_handler(input: object, output: object) -> None:
    raise TypeError("simulated plugin crash")


async def _mutate_handler(input: object, output: object) -> None:
    if hasattr(output, "skip"):
        output.skip = True


# ── 1. Hook name completeness ──────────────────────────────────────────────────

def test_all_eight_hook_names_registered() -> None:
    system = LifecycleHookSystem()
    hooks = system.list_hooks()
    expected = {
        "model.call.before",
        "model.call.after",
        "agent.think.before",
        "agent.think.after",
        "task.dispatch.before",
        "task.complete.after",
        "human.escalation.before",
        "session.compact.before",
    }
    assert set(hooks.keys()) == expected, (
        f"Missing hook names: {expected - set(hooks.keys())}"
    )


def test_registration_rejects_unknown_hook() -> None:
    system = LifecycleHookSystem()
    with pytest.raises(ValueError, match="Unknown hook event"):
        system.register("nonexistent.hook", _allow_handler)


# ── 2. Type imports + instantiation ────────────────────────────────────────────

def test_all_input_types_instantiate() -> None:
    ModelCallBeforeInput()
    ModelCallAfterInput()
    AgentThinkBeforeInput()
    AgentThinkAfterInput()
    TaskDispatchBeforeInput()
    TaskCompleteAfterInput()
    HumanEscalationBeforeInput()
    SessionCompactBeforeInput()


def test_all_output_types_instantiate() -> None:
    ModelCallBeforeOutput()
    ModelCallAfterOutput()
    AgentThinkBeforeOutput()
    AgentThinkAfterOutput()
    TaskDispatchBeforeOutput()
    TaskCompleteAfterOutput()
    HumanEscalationBeforeOutput()
    SessionCompactBeforeOutput()


# ── 3. Behavioral: allow path ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_model_call_before_allow() -> None:
    system = LifecycleHookSystem()
    system.register("model.call.before", _allow_handler)
    input = ModelCallBeforeInput(model="sonnet")
    _output, result = await system.dispatch_model_call_before(input)
    assert result.allowed
    assert not result.skipped
    assert len(result.chain_results) == 1
    assert result.chain_results[0].outcome == "allow"


@pytest.mark.asyncio
async def test_task_dispatch_before_allow() -> None:
    system = LifecycleHookSystem()
    system.register("task.dispatch.before", _allow_handler)
    input = TaskDispatchBeforeInput(
        task=TaskInfo(description="test task", model="sonnet"),
        dispatcher=DispatcherInfo(current_task_count=5, floor=10, ceiling=16),
    )
    _output, result = await system.dispatch_task_dispatch_before(input)
    assert result.allowed


# ── 4. Behavioral: deny path (DenyError short-circuits) ────────────────────────

@pytest.mark.asyncio
async def test_deny_error_blocks_operation() -> None:
    system = LifecycleHookSystem()
    system.register("human.escalation.before", _deny_handler)
    input = HumanEscalationBeforeInput()
    _output, result = await system.dispatch_human_escalation_before(input)
    assert not result.allowed
    assert len(result.chain_results) == 1
    assert result.chain_results[0].outcome == "deny"
    assert result.chain_results[0].reason == "test deny"


@pytest.mark.asyncio
async def test_deny_error_includes_suggested_action() -> None:
    exc = DenyError("blocked", suggested_action="use a different model")
    assert exc.permission_decision == "deny"
    assert exc.suggested_action == "use a different model"


@pytest.mark.asyncio
async def test_deny_error_can_override_permission_decision() -> None:
    exc = DenyError("blocked", permission_decision="custom")
    assert exc.permission_decision == "custom"


# ── 5. Behavioral: output mutation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_output_mutation_visible_in_result() -> None:
    system = LifecycleHookSystem()

    async def set_skip(input: object, output: ModelCallBeforeOutput) -> None:
        output.skip = True

    system.register("model.call.before", set_skip)
    input = ModelCallBeforeInput(model="deepseek")
    output, _result = await system.dispatch_model_call_before(input)
    assert output.skip is True


# ── 6. Chain execution (registration order) ────────────────────────────────────

@pytest.mark.asyncio
async def test_chain_executes_in_registration_order() -> None:
    system = LifecycleHookSystem()
    order: list[int] = []

    async def handler_1(input: object, output: object) -> None:
        order.append(1)

    async def handler_2(input: object, output: object) -> None:
        order.append(2)

    async def handler_3(input: object, output: object) -> None:
        order.append(3)

    system.register("task.complete.after", handler_1)
    system.register("task.complete.after", handler_2)
    system.register("task.complete.after", handler_3)

    input = TaskCompleteAfterInput()
    _output, result = await system.dispatch_task_complete_after(input)
    assert result.allowed
    assert order == [1, 2, 3]
    assert len(result.chain_results) == 3
    assert all(c.outcome == "allow" for c in result.chain_results)


@pytest.mark.asyncio
async def test_deny_short_circuits_chain() -> None:
    system = LifecycleHookSystem()
    order: list[int] = []

    async def handler_1(input: object, output: object) -> None:
        order.append(1)

    async def handler_2(input: object, output: object) -> None:
        raise DenyError("blocked at 2")

    async def handler_3(input: object, output: object) -> None:
        order.append(3)

    system.register("session.compact.before", handler_1)
    system.register("session.compact.before", handler_2)
    system.register("session.compact.before", handler_3)

    input = SessionCompactBeforeInput()
    _output, result = await system.dispatch_session_compact_before(input)
    assert not result.allowed
    assert order == [1], "handler_3 must not execute after denial"
    assert result.chain_results[1].outcome == "deny"


# ── 7. Output mutation across chain ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chain_output_progressive_mutation() -> None:
    system = LifecycleHookSystem()

    async def h1(input: object, output: TaskDispatchBeforeOutput) -> None:
        output.model = "sonnet"

    async def h2(input: object, output: TaskDispatchBeforeOutput) -> None:
        assert output.model == "sonnet"
        output.skip = True

    system.register("task.dispatch.before", h1)
    system.register("task.dispatch.before", h2)

    input = TaskDispatchBeforeInput(
        task=TaskInfo(model="haiku"),
        dispatcher=DispatcherInfo(),
    )
    output, _result = await system.dispatch_task_dispatch_before(input)
    assert output.model == "sonnet"
    assert output.skip is True


# ── 8. Fail-open (unexpected exception does not block) ─────────────────────────

@pytest.mark.asyncio
async def test_crash_handler_fail_open() -> None:
    system = LifecycleHookSystem()
    system.register("agent.think.before", _crash_handler)
    input = AgentThinkBeforeInput()
    _output, result = await system.dispatch_agent_think_before(input)
    assert result.allowed, "crash must not block operation (fail-open)"
    assert result.chain_results[0].outcome == "fail_open"


@pytest.mark.asyncio
async def test_crash_then_allow_chain_continues() -> None:
    system = LifecycleHookSystem()
    order: list[int] = []

    async def crash(input: object, output: object) -> None:
        raise RuntimeError("boom")

    async def ok(input: object, output: object) -> None:
        order.append(2)

    system.register("agent.think.after", crash)
    system.register("agent.think.after", ok)

    input = AgentThinkAfterInput()
    _output, result = await system.dispatch_agent_think_after(input)
    assert result.allowed
    assert order == [2], "second handler must execute after crash (fail-open)"

# ── 9. Subagent isolation ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subagent_skips_all_enforcement() -> None:
    system = LifecycleHookSystem()
    system.register("model.call.before", _deny_handler)
    system.register("model.call.after", _deny_handler)
    system.register("agent.think.before", _deny_handler)
    system.register("agent.think.after", _deny_handler)
    system.register("task.dispatch.before", _deny_handler)
    system.register("task.complete.after", _deny_handler)
    system.register("human.escalation.before", _deny_handler)
    system.register("session.compact.before", _deny_handler)

    with patch.object(SubagentGuard, "is_subagent", return_value=True):
        input = ModelCallBeforeInput(model="sonnet")
        _output, result = await system.dispatch_model_call_before(input)
        assert result.allowed
        assert result.skipped, "subagent must skip enforcement"

        input2 = TaskDispatchBeforeInput(
            task=TaskInfo(), dispatcher=DispatcherInfo()
        )
        _output2, result2 = await system.dispatch_task_dispatch_before(input2)
        assert result2.allowed
        assert result2.skipped


@pytest.mark.asyncio
async def test_dispatch_chain_subagent_skipped() -> None:
    with patch.object(SubagentGuard, "is_subagent", return_value=True):
        result = await dispatch_chain("model.call.before", object(), object(), [_deny_handler])
        assert result.allowed
        assert result.skipped


def test_subagent_guard_env_var() -> None:
    guard = SubagentGuard()
    with patch.dict(os.environ, {"OPENCODE_SUBAGENT": "1"}):
        assert guard.is_subagent()
    with patch.dict(os.environ, {}, clear=True), patch("os.path.exists", return_value=False):
        assert not guard.is_subagent()


def test_subagent_guard_file_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from general_ludd.security.state import project_state, secure_write_text

    monkeypatch.setenv("GLUDD_STATE_DIR", str(tmp_path / "state"))
    state = project_state()
    marker = state.path("subagents", f"process-{os.getpid()}.json")
    secure_write_text(marker, "{}")
    guard = SubagentGuard()
    with patch.dict(os.environ, {"GLUDD_STATE_DIR": str(tmp_path / "state")}, clear=True):
        assert guard.is_subagent()


# ── 10. Recursion guard (depth > 2) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recursion_guard_prevents_infinite_loop() -> None:
    system = LifecycleHookSystem()
    depths: list[int] = []

    async def reentrant(input: object, output: object) -> None:
        system._lock.acquire()
        try:
            d = system._hook_depth["model.call.before"]
        finally:
            system._lock.release()
        depths.append(d)
        if d <= 2:
            m_input = ModelCallBeforeInput(model="test")
            await system.dispatch_model_call_before(m_input)

    system.register("model.call.before", reentrant)
    input = ModelCallBeforeInput(model="test")
    _output, result = await system.dispatch_model_call_before(input)
    assert result.allowed
    assert depths == [1, 2], f"depths should be [1,2] (depth-3 denied before handler runs) got {depths}"


# ── 11. Budget enforcement integration ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_budget_enforcement_via_model_call_before() -> None:
    from general_ludd.ag2_lifecycle.types import ModelCallBudget

    system = LifecycleHookSystem()

    async def budget_cap(input: ModelCallBeforeInput, output: ModelCallBeforeOutput) -> None:
        if input.budget and input.budget.max_tokens > 100000:
            output.budget = ModelCallBudget(max_tokens=100000)

    system.register("model.call.before", budget_cap)

    input = ModelCallBeforeInput(
        model="opus",
        budget=ModelCallBudget(max_tokens=200000),
    )
    output, result = await system.dispatch_model_call_before(input)
    assert result.allowed
    assert output.budget is not None
    assert output.budget.max_tokens == 100000


@pytest.mark.asyncio
async def test_budget_exhausted_denies_call() -> None:
    system = LifecycleHookSystem()

    async def budget_guard(input: object, output: ModelCallBeforeOutput) -> None:
        raise DenyError("Session budget exhausted", suggested_action="increase budget")

    system.register("model.call.before", budget_guard)

    input = ModelCallBeforeInput(model="opus")
    _output, result = await system.dispatch_model_call_before(input)
    assert not result.allowed


# ── 12. Task dispatch: skip ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_dispatch_skip() -> None:
    system = LifecycleHookSystem()
    system.register("task.dispatch.before", _mutate_handler)
    input = TaskDispatchBeforeInput(
        task=TaskInfo(description="duplicate task"),
        dispatcher=DispatcherInfo(),
    )
    output, _result = await system.dispatch_task_dispatch_before(input)
    assert output.skip is True


# ── 13. All dispatch wrappers smoke test ───────────────────────────────────────

@pytest.mark.asyncio
async def test_all_dispatch_wrappers_return_results() -> None:
    system = LifecycleHookSystem()

    cases: list[tuple[str, object]] = [
        ("model.call.before", ModelCallBeforeInput(model="sonnet")),
        ("model.call.after", ModelCallAfterInput()),
        ("agent.think.before", AgentThinkBeforeInput()),
        ("agent.think.after", AgentThinkAfterInput()),
        ("task.dispatch.before", TaskDispatchBeforeInput(
            task=TaskInfo(), dispatcher=DispatcherInfo(),
        )),
        ("task.complete.after", TaskCompleteAfterInput()),
        ("human.escalation.before", HumanEscalationBeforeInput()),
        ("session.compact.before", SessionCompactBeforeInput()),
    ]

    for name, input_obj in cases:
        output = object()
        result = await system.dispatch(name, input_obj, output)
        assert result.allowed, f"{name} should default-allow with no handlers"


@pytest.mark.asyncio
async def test_convenience_wrappers_all_call_dispatch() -> None:
    system = LifecycleHookSystem()
    count = 0

    async def counter(input: object, output: object) -> None:
        nonlocal count
        count += 1

    system.register("model.call.after", counter)
    system.register("agent.think.before", counter)
    system.register("task.dispatch.before", counter)
    system.register("task.complete.after", counter)
    system.register("human.escalation.before", counter)
    system.register("session.compact.before", counter)

    await system.dispatch_model_call_after(ModelCallAfterInput())
    await system.dispatch_agent_think_before(AgentThinkBeforeInput())
    await system.dispatch_task_dispatch_before(
        TaskDispatchBeforeInput(task=TaskInfo(), dispatcher=DispatcherInfo()),
    )
    await system.dispatch_task_complete_after(TaskCompleteAfterInput())
    await system.dispatch_human_escalation_before(HumanEscalationBeforeInput())
    await system.dispatch_session_compact_before(SessionCompactBeforeInput())

    assert count == 6


# ── 14. Handler count / introspection ──────────────────────────────────────────

def test_handler_count() -> None:
    system = LifecycleHookSystem()
    system.register("model.call.before", _allow_handler)
    system.register("model.call.before", _deny_handler)
    assert system.handler_count("model.call.before") == 2
    assert system.handler_count("agent.think.after") == 0


def test_list_hooks_shows_counts() -> None:
    system = LifecycleHookSystem()
    system.register("task.complete.after", _allow_handler)
    hooks = system.list_hooks()
    assert hooks["task.complete.after"] == 1
    assert hooks["human.escalation.before"] == 0


# ── 15. Unregister ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unregister_removes_handler() -> None:
    system = LifecycleHookSystem()
    handler = _deny_handler
    system.register("model.call.before", _allow_handler)
    system.register("model.call.before", handler)
    system.unregister("model.call.before", handler)

    input = ModelCallBeforeInput(model="sonnet")
    _output, result = await system.dispatch_model_call_before(input)
    assert result.allowed
    assert len(result.chain_results) == 1


# ── 16. Concurrency safety (threaded registration) ─────────────────────────────

def test_concurrent_registration_no_duplicates() -> None:
    system = LifecycleHookSystem()
    errors: list[Exception] = []

    def register_many() -> None:
        try:
            for _ in range(100):
                system.register("model.call.before", _allow_handler)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=register_many) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent registration errors: {errors}"
    count = system.handler_count("model.call.before")
    assert count == 1000, f"expected 1000 handlers, got {count}"


# ── 17. dispatch_chain standalone function ─────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_chain_standalone() -> None:
    order: list[int] = []

    async def h1(input: object, output: object) -> None:
        order.append(1)

    async def h2(input: object, output: object) -> None:
        order.append(2)

    result = await dispatch_chain("model.call.before", object(), object(), [h1, h2])
    assert result.allowed
    assert order == [1, 2]
    assert len(result.chain_results) == 2


@pytest.mark.asyncio
async def test_dispatch_chain_standalone_deny() -> None:
    result = await dispatch_chain("task.dispatch.before", object(), object(), [_deny_handler])
    assert not result.allowed
    assert result.chain_results[0].outcome == "deny"


@pytest.mark.asyncio
async def test_dispatch_chain_standalone_fail_open() -> None:
    result = await dispatch_chain("agent.think.before", object(), object(), [_crash_handler])
    assert result.allowed
    assert result.chain_results[0].outcome == "fail_open"


# ── 18. DenyError repr / str ───────────────────────────────────────────────────

def test_deny_error_str() -> None:
    exc = DenyError("reason string")
    assert str(exc) == "reason string"


def test_deny_error_default_permission_decision() -> None:
    exc = DenyError("test")
    assert exc.permission_decision == "deny"


# ── 19. Complex type round-trips ───────────────────────────────────────────────

def test_model_call_before_input_fields_settable() -> None:
    from general_ludd.ag2_lifecycle.types import Message, ModelCallBudget, ToolDef

    input = ModelCallBeforeInput(
        model="deepseek-v4-pro",
        messages=[Message(role="user", content="hello")],
        tools=[ToolDef(name="bash", description="run commands")],
        system_prompt="You are helpful.",
        budget=ModelCallBudget(max_tokens=8000, thinking_budget=4000),
    )
    assert input.model == "deepseek-v4-pro"
    assert len(input.messages) == 1
    assert input.messages[0].role == "user"
    assert input.budget is not None
    assert input.budget.max_tokens == 8000
    assert input.budget.thinking_budget == 4000


def test_task_budget_serialization_roundtrip() -> None:
    from dataclasses import asdict

    b = TaskBudget(max_steps=10, max_tokens=50000, timeout_ms=300000)
    d = asdict(b)
    b2 = TaskBudget(**d)
    assert b2.max_steps == 10
    assert b2.timeout_ms == 300000


def test_human_escalation_input_complex() -> None:
    input = HumanEscalationBeforeInput()
    input.escalation.type = "question"
    input.escalation.message = "Shall I continue?"
    input.alternatives.can_solve_locally = True
    input.alternatives.has_defaults = True
    input.alternatives.fallback_plan = "continue with next task"
    input.context.pending_work_count = 5
    assert input.escalation.type == "question"
    assert input.alternatives.can_solve_locally


def test_session_compact_input_complex() -> None:
    input = SessionCompactBeforeInput()
    input.compaction.trigger = "auto"
    input.compaction.current_tokens = 150000
    input.compaction.target_tokens = 80000
    input.compaction.messages_to_remove = 40
    input.critical_state.task_id = "W7.3"
    input.critical_state.pending_work = ["fix bug", "add test"]
    input.critical_state.enforcement_state = {"floor": 10, "streak": 2}
    assert input.compaction.trigger == "auto"
    assert input.critical_state.enforcement_state["floor"] == 10
