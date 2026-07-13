"""Unit tests for sandbox process executor."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from general_ludd.sandbox.process_executor import ProcessExecutor, ProcessLimits, ProcessResult


class TestProcessLimits:
    def test_defaults_none(self) -> None:
        limits = ProcessLimits()
        assert limits.memory_mb is None
        assert limits.cpu_seconds is None
        assert limits.max_file_size is None
        assert limits.max_open_files is None
        assert limits.max_processes is None

    def test_all_set(self) -> None:
        limits = ProcessLimits(memory_mb=512, cpu_seconds=30, max_file_size=1_000_000, max_open_files=256, max_processes=64)
        assert limits.memory_mb == 512
        assert limits.cpu_seconds == 30
        assert limits.max_file_size == 1_000_000
        assert limits.max_open_files == 256
        assert limits.max_processes == 64


class TestProcessResult:
    def test_success(self) -> None:
        result = ProcessResult(returncode=0, stdout="output", stderr="", pid=1234)
        assert result.returncode == 0
        assert result.stdout == "output"
        assert result.pid == 1234
        assert result.was_killed is False

    def test_killed(self) -> None:
        result = ProcessResult(returncode=-9, stdout="", stderr="", pid=5678, was_killed=True)
        assert result.was_killed is True
        assert result.returncode == -9


class TestProcessExecutor:
    def test_execute_success(self) -> None:
        executor = ProcessExecutor(timeout=5)
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.returncode = 0
            mock_proc.pid = 100
            mock_proc.communicate.return_value = ("hello\n", "")
            result = executor.execute("echo hello")
            assert result.returncode == 0
            assert result.stdout == "hello\n"

    def test_execute_with_workdir(self) -> None:
        executor = ProcessExecutor(timeout=5)
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.returncode = 0
            mock_proc.pid = 101
            mock_proc.communicate.return_value = ("done", "")
            result = executor.execute("ls", workdir="/tmp")
            call_kwargs = mock_popen.call_args[1]
            assert call_kwargs["cwd"] == "/tmp"
            assert result.returncode == 0

    def test_execute_with_env(self) -> None:
        executor = ProcessExecutor(timeout=5)
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.returncode = 0
            mock_proc.pid = 102
            mock_proc.communicate.return_value = ("val", "")
            result = executor.execute("echo $KEY", env={"KEY": "val"})
            call_kwargs = mock_popen.call_args[1]
            assert "KEY" in call_kwargs["env"]
            assert call_kwargs["env"]["KEY"] == "val"
            assert result.returncode == 0

    def test_execute_with_limits(self) -> None:
        executor = ProcessExecutor(timeout=5)
        limits = ProcessLimits(memory_mb=256, cpu_seconds=10)
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.returncode = 0
            mock_proc.pid = 103
            mock_proc.communicate.return_value = ("ok", "")
            result = executor.execute("true", limits=limits)
            call_kwargs = mock_popen.call_args[1]
            assert call_kwargs["preexec_fn"] is not None
            assert result.returncode == 0

    def test_execute_no_limits_no_preexec(self) -> None:
        executor = ProcessExecutor(timeout=5)
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.returncode = 0
            mock_proc.pid = 104
            mock_proc.communicate.return_value = ("ok", "")
            result = executor.execute("true")
            assert mock_popen.call_args[1]["preexec_fn"] is None
            assert result.returncode == 0

    def test_execute_timeout(self) -> None:
        executor = ProcessExecutor(timeout=1)
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.communicate.side_effect = subprocess.TimeoutExpired("cmd", 1)
            mock_proc.returncode = -9
            mock_proc.pid = 105
            result = executor.execute("sleep 999")
            assert result.was_killed is True

    def test_execute_nonzero_exit(self) -> None:
        executor = ProcessExecutor(timeout=5)
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.returncode = 1
            mock_proc.pid = 106
            mock_proc.communicate.return_value = ("", "error")
            result = executor.execute("false")
            assert result.returncode == 1
            assert result.stderr == "error"

    def test_apply_limits_memory(self) -> None:
        import resource
        limits = ProcessLimits(memory_mb=128)
        with patch.object(resource, "setrlimit") as mock_setrlimit:
            ProcessExecutor._apply_limits(limits)
            assert mock_setrlimit.call_count >= 1

    def test_apply_limits_cpu(self) -> None:
        import resource
        limits = ProcessLimits(cpu_seconds=60)
        with patch.object(resource, "setrlimit") as mock_setrlimit:
            ProcessExecutor._apply_limits(limits)
            assert any(call[0][0] == resource.RLIMIT_CPU for call in mock_setrlimit.call_args_list)

    def test_apply_limits_all_fields(self) -> None:
        import resource
        limits = ProcessLimits(memory_mb=256, cpu_seconds=30, max_file_size=1_000_000, max_open_files=128, max_processes=32)
        with patch.object(resource, "setrlimit") as mock_setrlimit:
            ProcessExecutor._apply_limits(limits)
            assert mock_setrlimit.call_count == 5

    def test_execute_max_output_bytes_stored(self) -> None:
        executor = ProcessExecutor(max_output_bytes=500_000)
        assert executor.max_output_bytes == 500_000
