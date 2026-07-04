"""Unit tests for sandbox code execution."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from general_ludd.sandbox_exec.executor import SandboxExecutor


class TestSandboxExecutor:
    def test_execute_runs_command_and_returns_result(self) -> None:
        executor = SandboxExecutor(timeout=5)
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args="echo hello",
                returncode=0,
                stdout="hello\n",
                stderr="",
            )
            result = executor.execute("echo hello")
            mock_run.assert_called_once_with(
                "echo hello",
                shell=True,
                cwd=None,
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert result.returncode == 0
            assert result.stdout == "hello\n"
