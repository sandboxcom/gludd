"""Unit tests for SandboxExecutor wiring in the EventLoop dispatch path."""

from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.sandbox_exec.executor import SandboxExecutor


class TestSandboxExecutorDispatch:
    def test_sandbox_executor_stored_in_event_loop(self) -> None:
        executor = SandboxExecutor(timeout=60)
        loop = EventLoop(sandbox_executor=executor)
        assert loop._sandbox_executor is executor

    def test_sandbox_executor_defaults_to_none(self) -> None:
        loop = EventLoop()
        assert loop._sandbox_executor is None

    @pytest.mark.asyncio
    async def test_sandbox_executor_called_when_sandbox_applied(self) -> None:
        mock_executor = MagicMock(spec=SandboxExecutor)
        mock_executor.execute.return_value = subprocess.CompletedProcess(
            args="dispatch:TODO-001:code",
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__ = AsyncMock(return_value=None)

        loop = EventLoop(
            sandbox_executor=mock_executor,
            runner=None,
            http_client=None,
            session=MagicMock(),
        )
        loop._session_factory = mock_session_factory

        mock_handle = MagicMock()
        mock_handle.token = "test-token"
        mock_handle.applied = True
        loop._sandbox_apply_for_todo = AsyncMock(return_value=mock_handle)
        loop._sandbox_release = AsyncMock()
        loop._dispatch_execute_job = AsyncMock()

        todo = MagicMock()
        todo.todo_id = "TODO-001"
        todo.work_type = "code"
        todo.project_id = None

        with patch(
            "general_ludd.event_loop.loop.VariableNamespaceRepository",
            return_value=AsyncMock(),
        ), patch(
            "general_ludd.event_loop.loop.TaskReturnRepository",
            return_value=AsyncMock(),
        ):
            await loop._dispatch_execute_job_isolated(todo)

        mock_executor.execute.assert_called_once()
        call_args = mock_executor.execute.call_args
        command_arg = call_args[0][0]
        assert "TODO-001" in command_arg

        loop._sandbox_apply_for_todo.assert_called_once_with(todo)
        loop._sandbox_release.assert_called_once_with(mock_handle)
        loop._dispatch_execute_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_sandbox_executor_not_called_when_no_sandbox_handle(self) -> None:
        mock_executor = MagicMock(spec=SandboxExecutor)

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__ = AsyncMock(return_value=None)

        loop = EventLoop(
            sandbox_executor=mock_executor,
            runner=None,
            http_client=None,
            session=MagicMock(),
        )
        loop._session_factory = mock_session_factory

        loop._sandbox_apply_for_todo = AsyncMock(return_value=None)
        loop._sandbox_release = AsyncMock()
        loop._dispatch_execute_job = AsyncMock()

        todo = MagicMock()
        todo.todo_id = "TODO-002"
        todo.work_type = "code"
        todo.project_id = None

        with patch(
            "general_ludd.event_loop.loop.VariableNamespaceRepository",
            return_value=AsyncMock(),
        ), patch(
            "general_ludd.event_loop.loop.TaskReturnRepository",
            return_value=AsyncMock(),
        ):
            await loop._dispatch_execute_job_isolated(todo)

        mock_executor.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_sandbox_executor_not_called_when_not_wired(self) -> None:
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__ = AsyncMock(return_value=None)

        loop = EventLoop(
            sandbox_executor=None,
            runner=None,
            http_client=None,
            session=MagicMock(),
        )
        loop._session_factory = mock_session_factory

        mock_handle = MagicMock()
        loop._sandbox_apply_for_todo = AsyncMock(return_value=mock_handle)
        loop._sandbox_release = AsyncMock()
        loop._dispatch_execute_job = AsyncMock()

        todo = MagicMock()
        todo.todo_id = "TODO-003"
        todo.work_type = "code"
        todo.project_id = None

        with patch(
            "general_ludd.event_loop.loop.VariableNamespaceRepository",
            return_value=AsyncMock(),
        ), patch(
            "general_ludd.event_loop.loop.TaskReturnRepository",
            return_value=AsyncMock(),
        ):
            await loop._dispatch_execute_job_isolated(todo)

        # sandbox handle exists but no executor wired — should not crash
        loop._dispatch_execute_job.assert_called_once()
