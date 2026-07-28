"""Integration test: SandboxEnforcer wired into ExecutionEngine tool-execution path.

Verifies fail-closed sandbox enforcement before tool execution:
  - Engine rejects all jobs when sandbox is not ready (fail-closed).
  - Engine allows writes confined within the sandbox jail.
  - Path escapes are blocked when sandbox is active.
  - SandboxNone (no enforcer configured) passes through unchanged.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from general_ludd.execution.engine import ExecutionEngine
from general_ludd.sandbox.enforcer import (
    SandboxConfig,
    SandboxEnforcer,
    SandboxNotAvailableError,
)
from general_ludd.schemas.job import JobSpec


class TestSandboxEngineWiring:
    def test_engine_rejects_when_sandbox_not_ready(self, tmp_path: Path) -> None:
        jail = tmp_path / "nonexistent-jail"
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        assert not enforcer.is_ready

        engine = ExecutionEngine(
            model_gateway=MagicMock(),
            workspace_path=str(tmp_path / "workspace"),
            sandbox_enforcer=enforcer,
        )

        job = JobSpec(
            job_id="JOB-SB1",
            todo_id="TODO-SB1",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="test",
        )

        result = engine.execute(job)
        assert result.exit_code == 1
        assert "Sandbox enforcement failed" in result.result_summary

    def test_engine_allows_execution_when_sandbox_ready(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        assert enforcer.is_ready

        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(content="No changes needed."))

        engine = ExecutionEngine(
            model_gateway=mock_gateway,
            workspace_path=str(tmp_path / "workspace"),
            sandbox_enforcer=enforcer,
        )

        job = JobSpec(
            job_id="JOB-SB2",
            todo_id="TODO-SB2",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="test",
        )

        result = engine.execute(job)
        assert result.exit_code == 1
        assert "No changes parsed" in result.result_summary

    def test_engine_without_sandbox_passes_through(self, tmp_path: Path) -> None:
        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(content="No changes needed."))

        engine = ExecutionEngine(
            model_gateway=mock_gateway,
            workspace_path=str(tmp_path / "workspace"),
            sandbox_enforcer=None,
        )

        job = JobSpec(
            job_id="JOB-SB3",
            todo_id="TODO-SB3",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="test",
        )

        result = engine.execute(job)
        assert result.exit_code == 1
        assert "No changes parsed" in result.result_summary

    def test_sandbox_confines_file_writes_to_jail(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()

        mock_gateway = MagicMock()
        file_content = "print('hello from sandbox')"
        mock_gateway.call_model = MagicMock(
            return_value=MagicMock(content=f"```python\nFILE: script.py\n{file_content}\n```")
        )

        engine = ExecutionEngine(
            model_gateway=mock_gateway,
            workspace_path=str(workspace),
            sandbox_enforcer=enforcer,
        )

        job = JobSpec(
            job_id="JOB-SB4",
            todo_id="TODO-SB4",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="write a script",
        )

        result = engine.execute(job)
        assert result.exit_code == 1

        written_path = enforcer.confine_path(str(workspace / "script.py"))
        assert os.path.isfile(written_path)
        assert open(written_path).read() == file_content

    def test_sandbox_blocks_path_escape_from_model(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()

        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(
            return_value=MagicMock(content="```\nFILE: ../../etc/passwd\nmalicious content\n```")
        )

        engine = ExecutionEngine(
            model_gateway=mock_gateway,
            workspace_path=str(workspace),
            sandbox_enforcer=enforcer,
        )

        job = JobSpec(
            job_id="JOB-SB5",
            todo_id="TODO-SB5",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="escape attempt",
        )

        result = engine.execute(job)
        assert result.exit_code == 1
        assert "No changes parsed" in result.result_summary

    def test_sandbox_blocks_absolute_path_outside_jail(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()

        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(
            return_value=MagicMock(content="```\nFILE: /etc/passwd\nmalicious content\n```")
        )

        engine = ExecutionEngine(
            model_gateway=mock_gateway,
            workspace_path=str(workspace),
            sandbox_enforcer=enforcer,
        )

        job = JobSpec(
            job_id="JOB-SB6",
            todo_id="TODO-SB6",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="absolute path escape",
        )

        result = engine.execute(job)
        assert result.exit_code == 1
        assert "No changes parsed" in result.result_summary

    def test_async_engine_rejects_when_sandbox_not_ready(self, tmp_path: Path) -> None:
        jail = tmp_path / "nonexistent-async-jail"
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))

        engine = ExecutionEngine(
            model_gateway=MagicMock(),
            workspace_path=str(tmp_path / "workspace"),
            sandbox_enforcer=enforcer,
        )

        job = JobSpec(
            job_id="JOB-SB7",
            todo_id="TODO-SB7",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="async test",
        )

        import asyncio

        async def _run() -> None:
            result = await engine.execute_async(job)
            assert result.exit_code == 1
            assert "Sandbox enforcement failed" in result.result_summary

        asyncio.run(_run())

    def test_sandbox_verify_is_idempotent_across_calls(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))

        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(content="No changes."))

        engine = ExecutionEngine(
            model_gateway=mock_gateway,
            workspace_path=str(tmp_path / "workspace"),
            sandbox_enforcer=enforcer,
        )

        job = JobSpec(
            job_id="JOB-SB8",
            todo_id="TODO-SB8",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="first call",
        )

        result1 = engine.execute(job)
        assert engine._sandbox_verified is True

        result2 = engine.execute(job)
        assert result2.exit_code == 1
        assert "No changes parsed" in result2.result_summary
