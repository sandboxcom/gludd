"""Prove ToolCallLoop recognizes code-generation work types.

The game audit report flagged that ToolCallLoop was "restricted to
'analysis'/'audit' only." This test proves the expansion is real:
``_TOOL_USE_WORK_TYPES`` includes code work types, each gets proper
per-type max-iteration caps, and the Phase 2 gate accepts them.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# — _TOOL_USE_WORK_TYPES is defined in event_loop/loop.py —
from general_ludd.event_loop.loop import (
    _CODE_WORK_TYPES,
    _TOOL_USE_WORK_TYPES,
)


class TestToolUseWorkTypesExpanded:
    """Verify code-generation work types are gated into Phase 2."""

    def test_all_code_work_types_are_in_tool_use_set(self):
        """Every code work type must be in _TOOL_USE_WORK_TYPES."""
        assert isinstance(_TOOL_USE_WORK_TYPES, frozenset)
        assert isinstance(_CODE_WORK_TYPES, frozenset)

        for wt in _CODE_WORK_TYPES:
            assert wt in _TOOL_USE_WORK_TYPES, (
                f"Code work type {wt!r} is NOT in _TOOL_USE_WORK_TYPES — "
                f"Phase 2 (ToolCallLoop) will NOT fire for it"
            )

    def test_legacy_work_types_still_present(self):
        """Analysis and audit must still be present (backward compat)."""
        assert "analysis" in _TOOL_USE_WORK_TYPES
        assert "audit" in _TOOL_USE_WORK_TYPES

    def test_specific_code_types_exist(self):
        """Confirm the individual code work types are recognised."""
        expected = frozenset({"code", "bug_fix", "refactor", "feature", "test"})
        assert _CODE_WORK_TYPES == expected

        for wt in expected:
            assert wt in _TOOL_USE_WORK_TYPES

    def test_no_unknown_work_types_sneak_in(self):
        """All members should be from the known list."""
        known = {
            "analysis", "audit",
            "code", "bug_fix", "refactor", "feature", "test",
        }
        assert set(_TOOL_USE_WORK_TYPES) == known


class TestWorkTypeIterationCaps:
    """ToolCallLoop applies per-work-type max-iteration caps correctly."""

    @pytest.mark.asyncio
    async def test_code_work_type_uses_code_max_iterations(self):
        """ToolCallLoop uses work_type_max_iterations dict for code types."""
        from general_ludd.schemas.job import JobSpec

        gateway = MagicMock()
        mcp = AsyncMock()
        mcp.list_tools = AsyncMock(return_value=[])

        job = JobSpec(
            job_id="J-TEST-001",
            todo_id="T-001",
            work_type="code",
            prompt_text="test",
            playbook="noop.yml",
            queue="core",
        )

        from general_ludd.execution.tool_loop import ToolCallLoop

        with patch.object(ToolCallLoop, "_call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "done"
            loop = ToolCallLoop(
                model_gateway=gateway,
                mcp_client=mcp,
                max_iterations=10,
                work_type_max_iterations={"code": 5, "bug_fix": 5, "analysis": 10},
            )
            await loop.run_with_tools(job, "sys", "user")
            # Should have completed — "done" with no tool_calls exits
            mock_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_code_work_type_iteration_limit_raises(self):
        """tool_calls that exceed the code max_iterations raise ToolLoopExhausted."""
        from general_ludd.schemas.job import JobSpec
        from general_ludd.execution.tool_loop import ToolCallLoop, ToolLoopExhausted

        gateway = MagicMock()
        mcp = AsyncMock()
        mcp.list_tools = AsyncMock(return_value=[
            MagicMock(name="echo", description="test", input_schema={}),
        ])
        mcp.call_tool = AsyncMock(return_value="ok")

        # Return tool_calls every time — never a final answer.
        class _Response:
            content = ""
            tool_calls = [{
                "id": "c1",
                "function": {"name": "echo", "arguments": "{}"},
            }]
            usage_metadata = {"input_tokens": 1, "output_tokens": 1}

        job = JobSpec(
            job_id="J-TEST-002",
            todo_id="T-002",
            work_type="refactor",
            prompt_text="test",
            playbook="noop.yml",
            queue="core",
        )

        from general_ludd.execution.tool_loop import ToolCallLoop

        # Skip the registry gate — mock _resolve_server_id
        with patch.object(ToolCallLoop, "_resolve_server_id", return_value="echo"):
            with patch.object(
                ToolCallLoop, "_call_with_tools", new_callable=AsyncMock,
            ) as mock_ct:
                mock_ct.return_value = _Response()
                loop = ToolCallLoop(
                    model_gateway=gateway,
                    mcp_client=mcp,
                    max_iterations=10,
                    work_type_max_iterations={"refactor": 2},
                    mcp_registry=MagicMock(),
                )
                with pytest.raises(ToolLoopExhausted):
                    await loop.run_with_tools(job, "sys", "user")
                # Called 2 times (refactor max) then exhausted
                assert mock_ct.call_count == 2

    @pytest.mark.asyncio
    async def test_analysis_gets_higher_max_iterations(self):
        """Analysis work type gets the full max_iterations (higher cap)."""
        from general_ludd.schemas.job import JobSpec
        from general_ludd.execution.tool_loop import ToolCallLoop, ToolLoopExhausted

        gateway = MagicMock()
        mcp = AsyncMock()
        mcp.list_tools = AsyncMock(return_value=[
            MagicMock(name="echo", description="test", input_schema={}),
        ])
        mcp.call_tool = AsyncMock(return_value="ok")

        class _Response:
            content = ""
            tool_calls = [{
                "id": "c1",
                "function": {"name": "echo", "arguments": "{}"},
            }]
            usage_metadata = {"input_tokens": 1, "output_tokens": 1}

        job = JobSpec(
            job_id="J-TEST-003",
            todo_id="T-003",
            work_type="analysis",
            prompt_text="test",
            playbook="noop.yml",
            queue="core",
        )

        from general_ludd.execution.tool_loop import ToolCallLoop

        with patch.object(ToolCallLoop, "_resolve_server_id", return_value="echo"):
            with patch.object(
                ToolCallLoop, "_call_with_tools", new_callable=AsyncMock,
            ) as mock_ct:
                mock_ct.return_value = _Response()
                loop = ToolCallLoop(
                    model_gateway=gateway,
                    mcp_client=mcp,
                    max_iterations=10,
                    work_type_max_iterations={"analysis": 10, "code": 5},
                    mcp_registry=MagicMock(),
                )
                with pytest.raises(ToolLoopExhausted):
                    await loop.run_with_tools(job, "sys", "user")
                assert mock_ct.call_count == 10


class TestPhase2GateIntegration:
    """The event-loop Phase 2 gate accepts code work types."""

    def test_phase2_gate_includes_all_code_types(self):
        """_TOOL_USE_WORK_TYPES import is clean and the set is usable."""
        assert isinstance(_TOOL_USE_WORK_TYPES, frozenset)
        assert len(_TOOL_USE_WORK_TYPES) == 7

        # Simulate the Phase 2 gate check from loop.py:2180
        for work_type in ("code", "bug_fix", "refactor", "feature", "test"):
            assert work_type in _TOOL_USE_WORK_TYPES, (
                f"Phase 2 gate would NOT fire for work_type={work_type!r}"
            )
