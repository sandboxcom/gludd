"""Unit tests for sandbox code execution."""

from __future__ import annotations

import shlex
import subprocess
from unittest.mock import ANY, patch

from general_ludd.sandbox_exec.executor import SandboxExecutor


class TestSandboxExecutor:
    def test_execute_runs_command_and_returns_result(self) -> None:
        executor = SandboxExecutor(timeout=5)
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["echo", "hello"],
                returncode=0,
                stdout="hello\n",
                stderr="",
            )
            result = executor.execute("echo hello")
            mock_run.assert_called_once_with(
                shlex.split("echo hello"),
                cwd=None,
                capture_output=True,
                text=True,
                timeout=5,
                env=ANY,
            )
            assert result.returncode == 0
            assert result.stdout == "hello\n"

    def test_execute_with_workdir(self) -> None:
        executor = SandboxExecutor(timeout=10, max_output_bytes=50000)
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["ls"],
                returncode=0,
                stdout="file1\nfile2\n",
                stderr="",
            )
            result = executor.execute("ls -la", workdir="/tmp")
            mock_run.assert_called_once_with(
                ["ls", "-la"],
                cwd="/tmp",
                capture_output=True,
                text=True,
                timeout=10,
                env=ANY,
            )
            assert result.returncode == 0

    def test_execute_nonzero_exit(self) -> None:
        executor = SandboxExecutor(timeout=5)
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["false"],
                returncode=1,
                stdout="",
                stderr="error",
            )
            result = executor.execute("false")
            assert result.returncode == 1
            assert result.stderr == "error"
