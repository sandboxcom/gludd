"""Tests for ag2_lifecycle types: all dataclass models across 5 domains."""

from __future__ import annotations

from general_ludd.ag2_lifecycle.types import (
    AgentThinkAfterInput,
    AgentThinkBeforeInput,
    CompactionInfo,
    CriticalState,
    DispatcherInfo,
    EscalationAlternatives,
    EscalationContext,
    EscalationInfo,
    HumanEscalationBeforeInput,
    HumanEscalationBeforeOutput,
    Message,
    ModelCallAfterInput,
    ModelCallAfterOutput,
    ModelCallBeforeInput,
    ModelCallBeforeOutput,
    ModelCallBudget,
    ModelCallResponse,
    ModelCallUsage,
    SessionCompactBeforeInput,
    SessionCompactBeforeOutput,
    TaskBudget,
    TaskCompleteAfterInput,
    TaskCompleteAfterOutput,
    TaskCompleteCodification,
    TaskCompleteInfo,
    TaskDispatchBeforeInput,
    TaskDispatchBeforeOutput,
    TaskEvidence,
    TaskInfo,
    TaskResult,
    ThinkingConstraints,
    ThinkingContext,
    ThinkingDecision,
    ToolCall,
    ToolDef,
    ToolResult,
)


class TestMessage:
    def test_defaults(self):
        m = Message(role="user", content="Hello")
        assert m.role == "user"
        assert m.content == "Hello"
        assert m.tool_calls is None
        assert m.tool_call_id is None

    def test_with_tool_calls(self):
        m = Message(role="assistant", content="", tool_calls=[{"name": "read"}])
        assert m.tool_calls == [{"name": "read"}]

    def test_with_tool_call_id(self):
        m = Message(role="tool", content="result", tool_call_id="call-1")
        assert m.tool_call_id == "call-1"


class TestToolDef:
    def test_defaults(self):
        t = ToolDef(name="read")
        assert t.name == "read"
        assert t.description == ""
        assert t.parameters == {}

    def test_with_parameters(self):
        t = ToolDef(name="write", description="Write file", parameters={"path": {"type": "string"}})
        assert t.parameters == {"path": {"type": "string"}}


class TestToolCall:
    def test_defaults(self):
        tc = ToolCall(id="call-1", name="read")
        assert tc.id == "call-1"
        assert tc.name == "read"
        assert tc.arguments == {}

    def test_with_arguments(self):
        tc = ToolCall(id="call-1", name="write", arguments={"path": "/tmp/x"})
        assert tc.arguments == {"path": "/tmp/x"}


class TestToolResult:
    def test_defaults(self):
        tr = ToolResult(tool_call_id="call-1", output="done")
        assert tr.tool_call_id == "call-1"
        assert tr.output == "done"
        assert tr.is_error is False

    def test_error_result(self):
        tr = ToolResult(tool_call_id="call-1", output="permission denied", is_error=True)
        assert tr.is_error is True


class TestModelCallDomain:
    def test_budget_defaults(self):
        b = ModelCallBudget()
        assert b.max_tokens == 0
        assert b.thinking_budget is None

    def test_before_input_defaults(self):
        inp = ModelCallBeforeInput()
        assert inp.model == ""
        assert inp.messages == []
        assert inp.tools == []

    def test_before_output_defaults(self):
        out = ModelCallBeforeOutput()
        assert out.skip is False

    def test_before_output_skip(self):
        out = ModelCallBeforeOutput(skip=True)
        assert out.skip is True

    def test_usage_defaults(self):
        u = ModelCallUsage()
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_usage_values(self):
        u = ModelCallUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert u.total_tokens == 150

    def test_response_defaults(self):
        r = ModelCallResponse()
        assert r.content == ""
        assert r.finish_reason == ""
        assert r.latency_ms == 0

    def test_after_input_defaults(self):
        inp = ModelCallAfterInput()
        assert inp.model == ""
        assert inp.messages == []

    def test_after_output_defaults(self):
        out = ModelCallAfterOutput()
        assert out.content is None
        assert out.tool_calls is None


class TestThinkingDomain:
    def test_context_defaults(self):
        ctx = ThinkingContext()
        assert ctx.current_task == ""
        assert ctx.conversation_length == 0
        assert ctx.tool_results == []
        assert ctx.memory_entries == []

    def test_context_with_tool_results(self):
        tr = ToolResult(tool_call_id="c1", output="ok")
        ctx = ThinkingContext(tool_results=[tr])
        assert len(ctx.tool_results) == 1

    def test_constraints_defaults(self):
        c = ThinkingConstraints()
        assert c.max_thinking_tokens is None
        assert c.reasoning_style == ""

    def test_decision_defaults(self):
        d = ThinkingDecision()
        assert d.next_action == ""
        assert d.plan is None
        assert d.confidence is None

    def test_think_before_input_defaults(self):
        inp = AgentThinkBeforeInput()
        assert isinstance(inp.context, ThinkingContext)
        assert isinstance(inp.constraints, ThinkingConstraints)

    def test_think_after_input_defaults(self):
        inp = AgentThinkAfterInput()
        assert inp.reasoning == ""


class TestTaskLifecycleDomain:
    def test_task_info_defaults(self):
        ti = TaskInfo()
        assert ti.description == ""
        assert ti.prompt == ""
        assert ti.model == ""
        assert ti.isolation == "none"

    def test_task_budget_defaults(self):
        tb = TaskBudget()
        assert tb.max_steps is None
        assert tb.max_tokens is None
        assert tb.timeout_ms is None

    def test_dispatcher_info_defaults(self):
        di = DispatcherInfo()
        assert di.current_task_count == 0
        assert di.floor == 0
        assert di.ceiling == 0

    def test_dispatch_before_input_defaults(self):
        inp = TaskDispatchBeforeInput()
        assert isinstance(inp.task, TaskInfo)
        assert isinstance(inp.budget, TaskBudget)

    def test_dispatch_before_output_skip(self):
        out = TaskDispatchBeforeOutput(skip=True)
        assert out.skip is True

    def test_task_complete_info_defaults(self):
        tci = TaskCompleteInfo()
        assert tci.id == ""
        assert tci.description == ""

    def test_task_result_defaults(self):
        tr = TaskResult()
        assert tr.tool_call_count == 0
        assert tr.latency_ms == 0
        assert tr.status == ""

    def test_task_evidence_defaults(self):
        ev = TaskEvidence()
        assert ev.commit_hash is None
        assert ev.test_count is None
        assert ev.files_modified is None

    def test_task_evidence_with_data(self):
        ev = TaskEvidence(commit_hash="abc123", test_count=42, files_modified=["a.py"])
        assert ev.commit_hash == "abc123"
        assert ev.test_count == 42
        assert ev.files_modified == ["a.py"]

    def test_complete_after_input(self):
        inp = TaskCompleteAfterInput()
        assert isinstance(inp.task, TaskCompleteInfo)
        assert isinstance(inp.result, TaskResult)

    def test_complete_after_output(self):
        out = TaskCompleteAfterOutput()
        assert out.result is None
        assert out.codification is None

    def test_codification_defaults(self):
        cod = TaskCompleteCodification()
        assert cod.update_tasks_md is False
        assert cod.record_commit is False
        assert cod.update_session_md is False


class TestHumanInteractionDomain:
    def test_escalation_info_defaults(self):
        ei = EscalationInfo()
        assert ei.type == ""
        assert ei.message == ""

    def test_escalation_alternatives_defaults(self):
        ea = EscalationAlternatives()
        assert ea.can_solve_locally is False
        assert ea.has_defaults is False

    def test_escalation_context(self):
        ec = EscalationContext(task_in_progress="fix-bug", pending_work_count=3)
        assert ec.task_in_progress == "fix-bug"
        assert ec.pending_work_count == 3

    def test_human_escalation_before_input(self):
        inp = HumanEscalationBeforeInput()
        assert isinstance(inp.escalation, EscalationInfo)

    def test_human_escalation_before_output(self):
        out = HumanEscalationBeforeOutput(skip=True, message="handled")
        assert out.skip is True
        assert out.message == "handled"


class TestSessionManagementDomain:
    def test_compaction_info(self):
        ci = CompactionInfo(trigger="threshold", current_tokens=1000, target_tokens=500, messages_to_remove=10)
        assert ci.trigger == "threshold"
        assert ci.current_tokens == 1000
        assert ci.target_tokens == 500
        assert ci.messages_to_remove == 10

    def test_critical_state(self):
        cs = CriticalState(task_id="t1", pending_work=["w1"], last_commit_hash="abc")
        assert cs.task_id == "t1"
        assert cs.pending_work == ["w1"]
        assert cs.last_commit_hash == "abc"

    def test_session_compact_before_input(self):
        inp = SessionCompactBeforeInput()
        assert isinstance(inp.compaction, CompactionInfo)
        assert isinstance(inp.critical_state, CriticalState)

    def test_session_compact_before_output(self):
        out = SessionCompactBeforeOutput(preserve=["msg-1"], inject="summary")
        assert out.preserve == ["msg-1"]
        assert out.inject == "summary"
