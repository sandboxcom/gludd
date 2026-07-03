"""Tests for confirmed execution-engine bug fixes (red-team findings).

Covers:
  Fix 1: GitAutomation._run_git passes a timeout + non-interactive env, and the
         event loop time-bounds commit/push (offloaded via asyncio.to_thread).
  Fix 2: ToolCallLoop bounds each MCP tool call with asyncio.wait_for and turns a
         timeout into a tool-error message instead of hanging.
  Fix 3: ExecutionEngine refuses model FILE:/diff paths that escape the workspace
         (absolute or ../ traversal), for both _write_file and _apply_unified_diff.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.execution.engine import ExecutionEngine
from general_ludd.execution.tool_loop import ToolCallLoop, ToolLoopExhausted
from general_ludd.git_automation.repo import (
    _GIT_TIMEOUT_SECONDS,
    GitAutomation,
)
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.security.capability_lattice import CapabilityError


# --------------------------------------------------------------------------- #
# Fix 1 — git subprocess is time-bounded + non-interactive
# --------------------------------------------------------------------------- #
class TestFix1GitTimeout:
    def test_run_git_passes_timeout_and_noninteractive_env(self):
        """_run_git must pass timeout= and GIT_TERMINAL_PROMPT/GIT_ASKPASS env."""
        captured: dict[str, object] = {}

        def _fake_run(*args, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        with patch("general_ludd.git_automation.repo.subprocess.run", side_effect=_fake_run):
            GitAutomation("/tmp/repo")._run_git("status")

        assert captured.get("timeout") == _GIT_TIMEOUT_SECONDS
        env = captured.get("env")
        assert isinstance(env, dict)
        assert env.get("GIT_TERMINAL_PROMPT") == "0"
        assert env.get("GIT_ASKPASS") == "echo"

    def test_run_git_timeout_becomes_clean_called_process_error(self):
        """A TimeoutExpired must surface as CalledProcessError, never propagate a hang."""
        with patch(
            "general_ludd.git_automation.repo.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git", "push"], timeout=_GIT_TIMEOUT_SECONDS),
        ), pytest.raises(subprocess.CalledProcessError):
            GitAutomation("/tmp/repo")._run_git("push")

    def test_push_returns_false_on_timeout_instead_of_raising(self):
        """push() catches CalledProcessError -> a timed-out push fails closed."""
        with patch(
            "general_ludd.git_automation.repo.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git", "push"], timeout=_GIT_TIMEOUT_SECONDS),
        ):
            assert GitAutomation("/tmp/repo").push() is False


class TestFix1EventLoopOffload:
    @pytest.mark.asyncio
    async def test_commit_completed_work_is_time_bounded_via_to_thread(self):
        """_try_commit_completed_work must offload blocking git via asyncio.to_thread.

        We make the GitAutomation slow-but-not-infinite and assert the call is
        awaited through asyncio.to_thread (so a real hang cannot freeze the loop).
        The test itself is wrapped in asyncio.wait_for to prove it cannot hang.
        """
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(config={})

        todo = MagicMock()
        todo.todo_id = "TODO-X"
        todo.title = "do thing"
        todo.branch_name = "gludd-x"
        todo.worktree = "/tmp/some-worktree"

        commit_calls: list[str] = []
        push_calls: list[str] = []

        class _FakeRepo:
            def __init__(self, path):
                self.path = path

            def commit(self, msg):
                commit_calls.append(msg)
                return "abc123"

            def push(self, branch="main"):
                push_calls.append(branch)
                return True

        to_thread_used = {"count": 0}
        real_to_thread = asyncio.to_thread

        async def _counting_to_thread(fn, *a, **k):
            to_thread_used["count"] += 1
            return await real_to_thread(fn, *a, **k)

        with patch("general_ludd.git_automation.repo.GitAutomation", _FakeRepo), \
             patch("general_ludd.event_loop.loop.asyncio.to_thread", _counting_to_thread):
            await asyncio.wait_for(loop._try_commit_completed_work(todo), timeout=5)

        # Both commit and push must have run, and each through to_thread.
        assert commit_calls and push_calls
        assert push_calls == ["gludd-x"]
        assert to_thread_used["count"] >= 2


# --------------------------------------------------------------------------- #
# Fix 2 — per-tool timeout in the tool-call loop
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def _tc(name, args):
    return {"id": "call-1", "function": {"name": name, "arguments": args}}


class TestFix2ToolTimeout:
    @pytest.mark.asyncio
    async def test_hung_tool_times_out_and_appends_error_then_continues(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))

        async def _hang(*a, **k):
            await asyncio.sleep(3600)
            return {"never": True}

        mcp_client = MagicMock()
        mcp_client.list_tools = AsyncMock(
            return_value=[MCPTool(name="read_file", server_id="fs")]
        )
        mcp_client.call_tool = AsyncMock(side_effect=_hang)

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": "x"})]),
                _Resp(content="recovered"),
            ]
        )

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            per_tool_timeout=0.05,
        )
        job = MagicMock()
        job.job_id = "JOB-1"

        # Must return (not hang) because the tool call is bounded.
        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )
        assert result == "recovered"
        # The model's second turn must have seen a timeout tool-error message.
        second_call_kwargs = gateway.call_model.call_args_list[1].kwargs
        messages = second_call_kwargs["messages"]
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert any("timed out" in m["content"] for m in tool_msgs)


# --------------------------------------------------------------------------- #
# D1 — max-iteration exhaustion raises instead of returning trailing garbage
# --------------------------------------------------------------------------- #
class TestToolLoopMaxIterationExhaustion:
    @pytest.mark.asyncio
    async def test_exhaustion_raises_tool_loop_exhausted_not_garbage(self):
        """If the model keeps requesting tools past the iteration cap, the loop
        must RAISE ToolLoopExhausted rather than silently returning the last
        (empty / raw-repr) content."""
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))

        mcp_client = MagicMock()
        mcp_client.list_tools = AsyncMock(
            return_value=[MCPTool(name="read_file", server_id="fs")]
        )
        mcp_client.call_tool = AsyncMock(return_value={"ok": True})

        gateway = MagicMock()
        # The model NEVER stops requesting a tool -> the loop always sees
        # tool_calls and can never reach a final-content return.
        gateway.call_model = MagicMock(
            return_value=_Resp(tool_calls=[_tc("read_file", {"path": "x"})])
        )

        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            max_iterations=3,
        )
        job = MagicMock()
        job.job_id = "JOB-EXHAUST"

        with pytest.raises(ToolLoopExhausted):
            await asyncio.wait_for(
                loop.run_with_tools(job, "sys", "user"), timeout=5
            )
        # The cap must have been honoured exactly (no infinite loop).
        assert gateway.call_model.call_count == 3


# --------------------------------------------------------------------------- #
# Per-role capability gate — ToolCallLoop refuses MCP tool use fail-closed for a
# role that lacks the "mcp" dispatch capability (issue #58 lattice wiring).
# --------------------------------------------------------------------------- #
class TestToolLoopRoleCapabilityGate:
    """ToolCallLoop(role=...) gates MCP tool use through check_dispatch.

    A role WITHOUT the "mcp" dispatch kind (e.g. built-in "report_status", which
    grants only {"skill"}) must be refused fail-closed BEFORE any tool is called.
    A role WITH "mcp" (e.g. built-in "event_loop", grants {"role","mcp","skill"})
    permits the tool call. role=None preserves the pre-existing ungated behaviour.
    """

    @staticmethod
    def _wiring():
        """A registry + mcp_client + gateway that would drive one tool call."""
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))

        mcp_client = MagicMock()
        mcp_client.list_tools = AsyncMock(
            return_value=[MCPTool(name="read_file", server_id="fs")]
        )
        mcp_client.call_tool = AsyncMock(return_value={"ok": True})

        gateway = MagicMock()
        # Turn 1: request the tool. Turn 2: final content (only reached if the
        # gate permits the call and the tool result is fed back).
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": "x"})]),
                _Resp(content="done"),
            ]
        )
        job = MagicMock()
        job.job_id = "JOB-ROLE"
        return registry, mcp_client, gateway, job

    @pytest.mark.asyncio
    async def test_role_without_mcp_is_refused_before_call_tool(self):
        """A role lacking "mcp" (report_status) raises and never calls a tool."""
        registry, mcp_client, gateway, job = self._wiring()
        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            role="report_status",
        )

        with pytest.raises(CapabilityError):
            await asyncio.wait_for(
                loop.run_with_tools(job, "sys", "user"), timeout=5
            )

        # Fail-closed: the tool was never invoked, and the gate fired BEFORE the
        # loop even asked the model or listed tools.
        mcp_client.call_tool.assert_not_called()
        mcp_client.list_tools.assert_not_called()
        gateway.call_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_loop_role_permits_the_tool_call(self):
        """The built-in "event_loop" role grants "mcp" -> the tool runs (regression)."""
        registry, mcp_client, gateway, job = self._wiring()
        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
            role="event_loop",
        )

        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )

        assert result == "done"
        mcp_client.call_tool.assert_awaited_once()
        assert mcp_client.call_tool.call_args.args[1] == "read_file"

    @pytest.mark.asyncio
    async def test_role_none_is_backward_compatible_ungated(self):
        """role=None (default) skips the gate entirely -> unchanged behaviour."""
        registry, mcp_client, gateway, job = self._wiring()
        loop = ToolCallLoop(
            gateway, mcp_client=mcp_client, mcp_registry=registry,
        )
        assert loop._role is None

        result = await asyncio.wait_for(
            loop.run_with_tools(job, "sys", "user"), timeout=5
        )

        assert result == "done"
        mcp_client.call_tool.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Fix 3 — workspace path containment jail
# --------------------------------------------------------------------------- #
class TestFix3PathContainment:
    def _engine(self):
        ws = tempfile.mkdtemp()
        return ExecutionEngine(model_gateway=MagicMock(), workspace_path=ws), ws

    def test_write_file_refuses_parent_traversal(self):
        engine, _ = self._engine()
        with pytest.raises(ValueError, match="escapes the workspace"):
            engine._write_file("../escape.txt", "pwned")

    def test_write_file_refuses_deep_traversal(self):
        engine, _ = self._engine()
        with pytest.raises(ValueError, match="escapes the workspace"):
            engine._write_file("../../../../tmp/escape.txt", "pwned")

    def test_write_file_refuses_absolute_path(self):
        engine, _ = self._engine()
        target = os.path.join(tempfile.gettempdir(), "gludd-abs-escape.txt")
        with pytest.raises(ValueError, match="escapes the workspace"):
            engine._write_file(target, "pwned")
        assert not os.path.exists(target)

    def test_write_file_allows_in_workspace_path(self):
        engine, ws = self._engine()
        engine._write_file("sub/dir/ok.txt", "fine")
        assert os.path.exists(os.path.join(ws, "sub", "dir", "ok.txt"))

    def test_apply_unified_diff_refuses_escaping_target(self):
        engine, _ws = self._engine()
        # A diff whose +++ target escapes via ../ must NOT be applied.
        diff = (
            "--- a/../../etc/escape\n"
            "+++ b/../../etc/escape\n"
            "@@ -0,0 +1 @@\n"
            "+pwned\n"
        )
        with patch("general_ludd.execution.engine.subprocess.run") as mock_run:
            changed = engine._apply_unified_diff(diff)
        # patch() must never have been invoked, and nothing reported changed.
        mock_run.assert_not_called()
        assert changed == []

    def test_apply_unified_diff_refuses_absolute_target(self):
        engine, _ = self._engine()
        diff = (
            "--- a//etc/passwd\n"
            "+++ b//etc/passwd\n"
            "@@ -0,0 +1 @@\n"
            "+pwned\n"
        )
        with patch("general_ludd.execution.engine.subprocess.run") as mock_run:
            changed = engine._apply_unified_diff(diff)
        mock_run.assert_not_called()
        assert changed == []

    def test_apply_unified_diff_allows_contained_target(self):
        engine, _ = self._engine()
        diff = (
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+y\n"
        )
        with patch("general_ludd.execution.engine.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
            changed = engine._apply_unified_diff(diff)
        mock_run.assert_called_once()
        assert "src/main.py" in changed
