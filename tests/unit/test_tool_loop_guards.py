"""Tests for the five untested ToolCallLoop guard branches in tool_loop.py:

  * budget-guard denial               (:174-186)
  * per-iteration timeout             (:193-206)
  * total-token cap                   (:211-223)
  * adversarial scan block            (:225-238)
  * malformed tool-call JSON args     (:245-255)
  * tool-call auditor gate            (:256-316)

Each guard must be proven to actually FIRE (raise / redirect / recover) under
a wiring that would otherwise let the loop proceed, not merely instantiated
with the option turned on.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.execution.tool_auditor import BadCallSituation
from general_ludd.execution.tool_loop import ToolCallLoop, ToolLoopExhausted
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


# --------------------------------------------------------------------------- #
# House fakes (copied local, matching tests/unit/test_execution_engine_fixes.py
# style) so this file has no cross-test-module import dependency.
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def _tc(name, args):
    return {"id": "call-1", "function": {"name": name, "arguments": args}}


def _usage_resp(content="", tool_calls=None, input_tokens=0, output_tokens=0):
    """A _Resp augmented with the usage_metadata dict the loop reads for its
    cumulative-token cap (tool_loop.py:211-214)."""
    resp = _Resp(content=content, tool_calls=tool_calls)
    resp.usage_metadata = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    return resp


def _wiring():
    """A real MCPToolRegistry + AsyncMock mcp_client + MagicMock job.

    Mirrors the house wiring used throughout test_execution_engine_fixes.py:
    one registered "read_file" tool on server "fs", a client whose
    list_tools/call_tool are AsyncMocks, and a job carrying the fields the
    loop reads (job_id, work_type).
    """
    registry = MCPToolRegistry()
    registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))

    mcp_client = MagicMock()
    mcp_client.list_tools = AsyncMock(
        return_value=[MCPTool(name="read_file", server_id="fs")]
    )
    mcp_client.call_tool = AsyncMock(return_value={"ok": True})

    job = MagicMock()
    job.job_id = "JOB-GUARD"
    job.work_type = "analysis"
    return registry, mcp_client, job


# --------------------------------------------------------------------------- #
# Budget-guard denial (tool_loop.py:174-186)
# --------------------------------------------------------------------------- #
class TestToolLoopBudgetGuardDenial:
    @pytest.mark.asyncio
    async def test_denying_guard_raises_before_any_model_call(self):
        """A budget_guard whose check_all_limits() denies must raise
        ToolLoopExhausted at iteration 1, carrying the guard's reason, and the
        model must NEVER be called (the gate fires before _call_with_tools)."""
        registry, mcp_client, job = _wiring()

        gateway = MagicMock()
        gateway.call_model = MagicMock(return_value=_Resp(content="unused"))

        budget_guard = MagicMock()
        budget_guard.check_all_limits = MagicMock(
            return_value={"allowed": False, "reason": "monthly spend cap hit"}
        )

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            budget_guard=budget_guard,
        )

        with pytest.raises(ToolLoopExhausted) as exc_info:
            await asyncio.wait_for(
                loop.run_with_tools(job, "sys", "user"), timeout=5
            )

        assert "budget exhausted at iteration 1" in str(exc_info.value)
        # The guard's own denial reason must be carried through.
        assert "monthly spend cap hit" in str(exc_info.value)
        gateway.call_model.assert_not_called()
        budget_guard.check_all_limits.assert_called_once()

    @pytest.mark.asyncio
    async def test_allowing_guard_is_transparent(self):
        """A budget_guard that ALLOWS the call must not perturb the loop at all
        -- it runs the tool and returns the final content exactly as if no
        guard were configured."""
        registry, mcp_client, job = _wiring()

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": "x"})]),
                _Resp(content="done"),
            ]
        )

        budget_guard = MagicMock()
        budget_guard.check_all_limits = MagicMock(return_value={"allowed": True})

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            budget_guard=budget_guard,
        )

        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )

        assert result == "done"
        mcp_client.call_tool.assert_awaited_once()
        assert budget_guard.check_all_limits.call_count == 2


# --------------------------------------------------------------------------- #
# Per-iteration timeout (tool_loop.py:193-206)
# --------------------------------------------------------------------------- #
class TestToolLoopPerIterationTimeout:
    @pytest.mark.asyncio
    async def test_slow_model_call_raises_with_timeout_error_cause(self):
        """A model call that blows the per-iteration timeout must raise
        ToolLoopExhausted at iteration 1 with the original TimeoutError chained
        as __cause__ (tool_loop.py's `raise ... from err`)."""
        registry, mcp_client, job = _wiring()

        def _slow_call(*_args, **_kwargs):
            time.sleep(0.5)
            return _Resp(content="too-late")

        gateway = MagicMock()
        gateway.call_model = MagicMock(side_effect=_slow_call)

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            per_iteration_timeout=0.05,
        )

        with pytest.raises(ToolLoopExhausted) as exc_info:
            await asyncio.wait_for(
                loop.run_with_tools(job, "sys", "user"), timeout=5
            )

        assert "iteration 1 timed out" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, TimeoutError)


# --------------------------------------------------------------------------- #
# Total-token cap (tool_loop.py:211-223)
# --------------------------------------------------------------------------- #
class TestToolLoopTokenCap:
    @pytest.mark.asyncio
    async def test_single_over_cap_response_raises_even_with_final_content(self):
        """The token check runs BEFORE the tool_calls/final-content branch, so
        even a response that already carries final (non-tool-call) content
        must still raise once its own usage pushes past the cap."""
        registry, mcp_client, job = _wiring()

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            return_value=_usage_resp(
                content="done", input_tokens=60, output_tokens=50,
            )
        )

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            max_total_tokens=100,
        )

        with pytest.raises(ToolLoopExhausted) as exc_info:
            await asyncio.wait_for(
                loop.run_with_tools(job, "sys", "user"), timeout=5
            )

        assert "total tokens 110 exceeded limit 100" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_cumulative_tokens_trip_the_cap_on_the_second_turn(self):
        """Two turns at 60 tokens each stay under the 100 cap individually but
        the SECOND turn's cumulative total (120) must trip it -- proving the
        cap accumulates across iterations rather than resetting each turn."""
        registry, mcp_client, job = _wiring()

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _usage_resp(
                    tool_calls=[_tc("read_file", {"path": "x"})],
                    input_tokens=30, output_tokens=30,
                ),
                _usage_resp(
                    content="final", input_tokens=30, output_tokens=30,
                ),
            ]
        )

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            max_total_tokens=100,
        )

        with pytest.raises(ToolLoopExhausted) as exc_info:
            await asyncio.wait_for(
                loop.run_with_tools(job, "sys", "user"), timeout=5
            )

        assert "total tokens 120 exceeded limit 100" in str(exc_info.value)
        assert gateway.call_model.call_count == 2
        # The first (under-cap) turn's tool call really ran.
        mcp_client.call_tool.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Adversarial scan block (tool_loop.py:225-238)
# --------------------------------------------------------------------------- #
class _ScanResult:
    def __init__(self, blocked, summary):
        self.blocked = blocked
        self.summary = summary


class _FakeAdversarialDetector:
    """Records every scanned (content, file_path) pair; verdict is fixed at
    construction time so blocked / clean behaviour is test-controlled."""

    def __init__(self, blocked: bool, summary: str = "") -> None:
        self._blocked = blocked
        self._summary = summary
        self.calls: list[dict[str, object]] = []

    def scan_text(self, content, file_path=None):
        self.calls.append({"content": content, "file_path": file_path})
        return _ScanResult(self._blocked, self._summary)


class TestToolLoopAdversarialScanBlock:
    @pytest.mark.asyncio
    async def test_blocked_scan_raises_and_never_calls_tool(self):
        registry, mcp_client, job = _wiring()
        job.job_id = "JOB-ADV"

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            return_value=_Resp(content="malicious payload")
        )

        detector = _FakeAdversarialDetector(
            blocked=True, summary="dangerous content detected",
        )

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            adversarial_detector=detector,
        )

        with pytest.raises(ToolLoopExhausted) as exc_info:
            await asyncio.wait_for(
                loop.run_with_tools(job, "sys", "user"), timeout=5
            )

        assert "blocked by adversarial scan" in str(exc_info.value)
        assert "dangerous content detected" in str(exc_info.value)
        mcp_client.call_tool.assert_not_called()
        assert len(detector.calls) == 1
        assert detector.calls[0]["content"] == "malicious payload"
        assert detector.calls[0]["file_path"] == "tool_loop:JOB-ADV"

    @pytest.mark.asyncio
    async def test_clean_scan_runs_to_completion_across_two_turns(self):
        registry, mcp_client, job = _wiring()
        job.job_id = "JOB-ADV-CLEAN"

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(
                    content="turn one reasoning",
                    tool_calls=[_tc("read_file", {"path": "x"})],
                ),
                _Resp(content="final answer"),
            ]
        )

        detector = _FakeAdversarialDetector(blocked=False)

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            adversarial_detector=detector,
        )

        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )

        assert result == "final answer"
        assert len(detector.calls) == 2
        mcp_client.call_tool.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Malformed tool-call JSON args (tool_loop.py:245-255)
# --------------------------------------------------------------------------- #
class TestToolLoopMalformedToolArgs:
    @pytest.mark.asyncio
    async def test_unparseable_string_args_fall_back_to_empty_dict(self):
        registry, mcp_client, job = _wiring()

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", "not-valid-json{")]),
                _Resp(content="done"),
            ]
        )
        mcp_client.call_tool = AsyncMock(return_value="ok")

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
        )

        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )

        assert result == "done"
        mcp_client.call_tool.assert_awaited_once_with("fs", "read_file", {})

    @pytest.mark.asyncio
    async def test_valid_json_string_args_are_parsed_to_dict(self):
        registry, mcp_client, job = _wiring()

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", '{"path": "y"}')]),
                _Resp(content="done2"),
            ]
        )
        mcp_client.call_tool = AsyncMock(return_value="ok2")

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
        )

        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )

        assert result == "done2"
        mcp_client.call_tool.assert_awaited_once_with(
            "fs", "read_file", {"path": "y"}
        )


# --------------------------------------------------------------------------- #
# Tool-call auditor gate (tool_loop.py:256-316)
# --------------------------------------------------------------------------- #
class TestToolLoopAuditorGate:
    @pytest.mark.asyncio
    async def test_blocked_call_saves_situation_and_continues_with_refusal(self):
        registry, mcp_client, job = _wiring()

        situation = BadCallSituation(
            tool_name="read_file",
            tool_args={"path": "x"},
            classification="redundant",
            reason="called too many times consecutively",
            task_excerpt="user",
            recent_calls=[],
            timestamp=0.0,
            work_type="analysis",
        )
        auditor = MagicMock()
        auditor.audit = MagicMock(return_value=situation)
        store = MagicMock()

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": "x"})]),
                _Resp(content="final"),
            ]
        )

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            tool_auditor=auditor, situation_store=store,
        )

        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )

        assert result == "final"
        # The blocked call never reached MCP.
        mcp_client.call_tool.assert_not_called()
        store.save.assert_called_once_with(situation)

        # audit() was called with capture_situation=True.
        assert auditor.audit.call_args.kwargs["capture_situation"] is True

        # The model's next turn saw a role:"tool" refusal message carrying the
        # classification and a "do not retry" instruction.
        second_call_kwargs = gateway.call_model.call_args_list[1].kwargs
        tool_msgs = [
            m for m in second_call_kwargs["messages"] if m.get("role") == "tool"
        ]
        assert len(tool_msgs) == 1
        refusal = tool_msgs[0]["content"]
        assert "Tool call blocked by auditor" in refusal
        assert situation.classification in refusal
        assert "Do not retry" in refusal

    @pytest.mark.asyncio
    async def test_situation_store_save_exception_is_suppressed(self):
        """store.save() raising must NOT crash the loop -- it is wrapped in
        contextlib.suppress(Exception) so a broken store degrades gracefully."""
        registry, mcp_client, job = _wiring()

        situation = BadCallSituation(
            tool_name="read_file",
            tool_args={"path": "x"},
            classification="error_loop",
            reason="errored repeatedly",
        )
        auditor = MagicMock()
        auditor.audit = MagicMock(return_value=situation)
        store = MagicMock()
        store.save = MagicMock(side_effect=RuntimeError("store is down"))

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": "x"})]),
                _Resp(content="final"),
            ]
        )

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            tool_auditor=auditor, situation_store=store,
        )

        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )

        assert result == "final"
        store.save.assert_called_once_with(situation)

    @pytest.mark.asyncio
    async def test_allowed_call_records_success(self):
        """audit() returning None (allowed, capture_situation=True) must let
        the call proceed and record_success must see the real tool result."""
        registry, mcp_client, job = _wiring()
        mcp_client.call_tool = AsyncMock(return_value="OK-RESULT")

        auditor = MagicMock()
        auditor.audit = MagicMock(return_value=None)

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": "x"})]),
                _Resp(content="final"),
            ]
        )

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            tool_auditor=auditor,
        )

        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )

        assert result == "final"
        mcp_client.call_tool.assert_awaited_once_with("fs", "read_file", {"path": "x"})
        auditor.record_success.assert_called_once_with(
            "read_file", {"path": "x"}, "OK-RESULT"
        )

    @pytest.mark.asyncio
    async def test_tool_exception_records_error_and_loop_recovers(self):
        """When the allowed tool call itself raises, record_error must be
        called with the stringified exception, a "Tool error: ..." tool
        message must be appended, and the loop must recover on the next turn
        instead of propagating the exception."""
        registry, mcp_client, job = _wiring()
        mcp_client.call_tool = AsyncMock(side_effect=RuntimeError("boom"))

        auditor = MagicMock()
        auditor.audit = MagicMock(return_value=None)

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": "x"})]),
                _Resp(content="recovered"),
            ]
        )

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            tool_auditor=auditor,
        )

        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )

        assert result == "recovered"
        auditor.record_error.assert_called_once_with("read_file", {"path": "x"}, "boom")

        second_call_kwargs = gateway.call_model.call_args_list[1].kwargs
        tool_msgs = [
            m for m in second_call_kwargs["messages"] if m.get("role") == "tool"
        ]
        assert len(tool_msgs) == 1
        assert "Tool error: boom" in tool_msgs[0]["content"]
