"""Integration and wiring tests for SandboxExecutor."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.sandbox_exec.executor import SandboxExecutor


class TestSandboxExecutorIntegration:
    def test_execute_real_command_returns_output(self) -> None:
        executor = SandboxExecutor(timeout=5)
        result = executor.execute("echo hello-world")
        assert result.returncode == 0
        assert "hello-world" in result.stdout

    def test_execute_with_real_workdir(self, tmp_path: Path) -> None:
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file.txt").write_text("content")
        executor = SandboxExecutor(timeout=5)
        result = executor.execute("pwd", workdir=str(subdir))
        assert result.returncode == 0
        assert str(subdir) in result.stdout.strip()

    def test_execute_timeout_propagates(self) -> None:
        executor = SandboxExecutor(timeout=0.01)
        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["sleep", "999"],
                timeout=0.01,
            )
            with pytest.raises(subprocess.TimeoutExpired):
                executor.execute("sleep 999")
            mock_run.assert_called_once()
            _, kwargs = mock_run.call_args
            assert kwargs["timeout"] == 0.01

    def test_execute_nonzero_exit_real_command(self) -> None:
        executor = SandboxExecutor(timeout=5)
        result = executor.execute("false")
        assert result.returncode != 0
        assert result.returncode == 1

    def test_max_output_bytes_stored(self) -> None:
        executor = SandboxExecutor(timeout=10, max_output_bytes=12345)
        assert executor.max_output_bytes == 12345

    def test_max_output_bytes_default(self) -> None:
        executor = SandboxExecutor()
        assert executor.max_output_bytes == 1_000_000


class TestSandboxExecutorWiring:
    def test_import_in_daemon_py(self) -> None:
        root = Path(__file__).parent.parent.parent
        daemon_path = root / "src" / "general_ludd" / "daemon.py"
        source = daemon_path.read_text()
        assert "from general_ludd.sandbox_exec.executor import SandboxExecutor" in source

    def test_instantiated_in_daemon_py(self) -> None:
        root = Path(__file__).parent.parent.parent
        daemon_path = root / "src" / "general_ludd" / "daemon.py"
        source = daemon_path.read_text()
        assert "SandboxExecutor(timeout=30)" in source

    def test_passed_to_event_loop_constructor(self) -> None:
        root = Path(__file__).parent.parent.parent
        daemon_path = root / "src" / "general_ludd" / "daemon.py"
        tree = ast.parse(daemon_path.read_text())

        sandbox_kwarg_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in getattr(node, "keywords", []):
                    if (
                        kw.arg == "sandbox_executor"
                        and isinstance(kw.value, ast.Name)
                        and kw.value.id == "sandbox_executor"
                    ):
                        sandbox_kwarg_found = True
                        break
            if sandbox_kwarg_found:
                break
        assert sandbox_kwarg_found, (
            "sandbox_executor not found as kwarg in daemon.py EventLoop constructor call"
        )

    def test_event_loop_stores_sandbox_executor(self) -> None:
        root = Path(__file__).parent.parent.parent
        loop_path = root / "src" / "general_ludd" / "event_loop" / "loop.py"
        source = loop_path.read_text()
        assert "sandbox_executor" in source
        assert "self._sandbox_executor = sandbox_executor" in source

    def test_no_shell_true_in_implementation(self) -> None:
        root = Path(__file__).parent.parent.parent
        exec_path = root / "src" / "general_ludd" / "sandbox_exec" / "executor.py"
        source = exec_path.read_text()
        assert "shell=" not in source, (
            "executor.py must not pass shell= keyword — subprocess.run defaults to shell=False"
        )
        assert "shell" not in source.split("subprocess.run")[1].split("\n")[0].split(")")[0], (
            "subprocess.run call in execute() must not set shell=True"
        )
