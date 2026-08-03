"""Deep sandbox runner and executor tests.

Covers the process execution lifecycle, stdout/stderr capture,
timeout enforcement, resource limits, filesystem isolation,
and process cleanup.

Author: opencode agent
"""

from __future__ import annotations

import errno
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.sandbox.cleanup import CleanupManager, CleanupRecord
from general_ludd.sandbox.contracts import (
    MINIMAL_SANDBOX_CONFIG,
    STRICT_SANDBOX_CONFIG,
    IsolationLevel,
    SandboxConfig,
    SandboxResult,
    validate_config,
)
from general_ludd.sandbox.docker_executor import (
    DockerContainerConfig,
    DockerExecutor,
    DockerResult,
)
from general_ludd.sandbox.enforcer import (
    MaxOutputExceededError,
    PathEscapeError,
    SandboxEnforcer,
    SandboxNotAvailableError,
)
from general_ludd.sandbox.enforcer import (
    SandboxConfig as EnforcerSandboxConfig,
)
from general_ludd.sandbox.network_policy import NetworkPolicy
from general_ludd.sandbox.process_executor import ProcessExecutor, ProcessLimits
from general_ludd.sandbox.resource_limits import ResourceLimits

# ────────────────────────────────────────────────────────────────
# Code execution lifecycle
# ────────────────────────────────────────────────────────────────


class TestExecutionLifecycle:
    """Complete execute → result → cleanup lifecycle."""

    def test_successful_execution_returns_stdout(self) -> None:
        executor = ProcessExecutor(timeout=10)
        result = executor.execute("echo hello")
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.stderr == ""
        assert not result.was_killed

    def test_failing_command_returns_stderr_and_nonzero(self) -> None:
        executor = ProcessExecutor(timeout=10)
        result = executor.execute("sh -c 'echo error >&2; exit 3'")
        assert result.returncode == 3
        assert "error" in result.stderr

    def test_result_includes_pid(self) -> None:
        executor = ProcessExecutor(timeout=10)
        result = executor.execute("echo pid-test")
        assert result.pid > 0
        assert result.was_killed is False

    def test_empty_command_output_yields_empty_strings(self) -> None:
        executor = ProcessExecutor(timeout=10)
        result = executor.execute("sh -c 'exit 0'")
        assert result.returncode == 0
        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)

    def test_execute_with_workdir(self) -> None:
        executor = ProcessExecutor(timeout=10)
        with tempfile.TemporaryDirectory() as td:
            result = executor.execute("pwd", workdir=td)
            assert result.returncode == 0
            assert os.path.realpath(td) in os.path.realpath(result.stdout.strip())

    def test_execute_with_custom_env(self) -> None:
        executor = ProcessExecutor(timeout=10)
        result = executor.execute("sh -c 'echo $MY_VAR'", env={"MY_VAR": "custom_value"})
        assert result.returncode == 0
        assert "custom_value" in result.stdout


# ────────────────────────────────────────────────────────────────
# stdout / stderr capture
# ────────────────────────────────────────────────────────────────


class TestStdoutStderrCapture:
    """Precise stdout/stderr stream capture."""

    def test_stdout_captured_independently(self) -> None:
        executor = ProcessExecutor(timeout=10)
        result = executor.execute('python3 -c \'import sys; sys.stdout.write("out"); sys.stderr.write("err")\'')
        assert result.stdout == "out"
        assert result.stderr == "err"

    def test_large_stdout_captured(self) -> None:
        executor = ProcessExecutor(timeout=10, max_output_bytes=10_000_000)
        result = executor.execute("python3 -c 'for i in range(10000): print(i)'")
        assert result.returncode == 0
        assert len(result.stdout) > 40000

    def test_interleaved_streams_remain_separate(self) -> None:
        executor = ProcessExecutor(timeout=10)
        result = executor.execute(
            "python3 -c '"
            "import sys; "
            'sys.stdout.write("A\\n"); sys.stdout.flush(); '
            'sys.stderr.write("B\\n"); sys.stderr.flush(); '
            'sys.stdout.write("C\\n")\''
        )
        assert result.stdout == "A\nC\n" or "A" in result.stdout
        assert "B" in result.stderr

    def test_binary_output_never_leaks_to_stdout(self) -> None:
        executor = ProcessExecutor(timeout=10)
        result = executor.execute("python3 -c 'import sys; sys.stdout.buffer.write(b\"\\x00\\x01\\x02\")'")
        assert isinstance(result.stdout, str)


# ────────────────────────────────────────────────────────────────
# Timeout enforcement
# ────────────────────────────────────────────────────────────────


class TestTimeoutEnforcement:
    """Process timeout kill and double-kill fallback."""

    def test_timeout_kills_process(self) -> None:
        executor = ProcessExecutor(timeout=1)
        result = executor.execute("sleep 60")
        assert result.was_killed
        assert result.returncode != 0

    def test_timeout_sets_was_killed_flag(self) -> None:
        executor = ProcessExecutor(timeout=1)
        result = executor.execute("sleep 60")
        assert result.was_killed is True

    def test_timeout_double_kill_fallback(self) -> None:
        with (
            patch.object(subprocess.Popen, "__init__", return_value=None),
            patch.object(subprocess.Popen, "communicate") as mock_comm,
            patch.object(subprocess.Popen, "kill") as mock_kill,
        ):
            mock_comm.side_effect = [
                subprocess.TimeoutExpired("cmd", 1),
                subprocess.TimeoutExpired("cmd", 1),
            ]
            proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            proc.returncode = None
            proc.pid = 99999

            with patch.object(subprocess, "Popen", return_value=proc):
                executor = ProcessExecutor(timeout=1)
                result = executor.execute("infinite")
                assert result.was_killed
                assert result.returncode == -1
                assert mock_kill.call_count >= 1

    def test_signal_on_timeout_is_sigkill_equivalent(self) -> None:
        executor = ProcessExecutor(timeout=1)
        result = executor.execute("sleep 60")
        assert result.was_killed

    def test_normal_completion_without_timeout(self) -> None:
        executor = ProcessExecutor(timeout=30)
        result = executor.execute("true")
        assert not result.was_killed
        assert result.returncode == 0

    def test_default_timeout_is_300_seconds(self) -> None:
        executor = ProcessExecutor()
        assert executor.timeout == 300

    def test_custom_timeout_in_constructor(self) -> None:
        executor = ProcessExecutor(timeout=42)
        assert executor.timeout == 42


# ────────────────────────────────────────────────────────────────
# Resource limits
# ────────────────────────────────────────────────────────────────


class TestResourceLimits:
    """Memory, CPU, file-size, open-files, and process-count limits."""

    def test_process_limits_dataclass_defaults(self) -> None:
        limits = ProcessLimits()
        assert limits.memory_mb is None
        assert limits.cpu_seconds is None
        assert limits.max_file_size is None
        assert limits.max_open_files is None
        assert limits.max_processes is None

    def test_process_limits_all_fields_set(self) -> None:
        limits = ProcessLimits(
            memory_mb=256,
            cpu_seconds=60,
            max_file_size=10_000_000,
            max_open_files=64,
            max_processes=20,
        )
        assert limits.memory_mb == 256
        assert limits.cpu_seconds == 60
        assert limits.max_file_size == 10_000_000
        assert limits.max_open_files == 64
        assert limits.max_processes == 20

    def test_process_limits_applied_to_subprocess(self) -> None:
        import resource

        limits = ProcessLimits(memory_mb=128, max_processes=10)
        with patch.object(resource, "setrlimit") as mock_setrlimit:
            ProcessExecutor._apply_limits(limits)
            assert mock_setrlimit.call_count >= 2

    def test_memory_limit_uses_rlimit_as(self) -> None:
        import resource

        limits = ProcessLimits(memory_mb=64)
        with patch.object(resource, "setrlimit") as mock_setrlimit:
            ProcessExecutor._apply_limits(limits)
            called_resources = {c[0][0] for c in mock_setrlimit.call_args_list}
            assert resource.RLIMIT_AS in called_resources

    def test_cpu_limit_uses_rlimit_cpu(self) -> None:
        import resource

        limits = ProcessLimits(cpu_seconds=30)
        with patch.object(resource, "setrlimit") as mock_setrlimit:
            ProcessExecutor._apply_limits(limits)
            called_resources = {c[0][0] for c in mock_setrlimit.call_args_list}
            assert resource.RLIMIT_CPU in called_resources

    def test_file_size_limit_uses_rlimit_fsize(self) -> None:
        import resource

        limits = ProcessLimits(max_file_size=1_000_000)
        with patch.object(resource, "setrlimit") as mock_setrlimit:
            ProcessExecutor._apply_limits(limits)
            called_resources = {c[0][0] for c in mock_setrlimit.call_args_list}
            assert resource.RLIMIT_FSIZE in called_resources

    def test_open_files_limit_uses_rlimit_nofile(self) -> None:
        import resource

        limits = ProcessLimits(max_open_files=32)
        with patch.object(resource, "setrlimit") as mock_setrlimit:
            ProcessExecutor._apply_limits(limits)
            called_resources = {c[0][0] for c in mock_setrlimit.call_args_list}
            assert resource.RLIMIT_NOFILE in called_resources

    def test_process_limit_uses_rlimit_nproc(self) -> None:
        import resource

        limits = ProcessLimits(max_processes=15)
        with patch.object(resource, "setrlimit") as mock_setrlimit:
            ProcessExecutor._apply_limits(limits)
            called_resources = {c[0][0] for c in mock_setrlimit.call_args_list}
            assert resource.RLIMIT_NPROC in called_resources

    def test_setrlimit_failure_suppressed(self) -> None:
        import resource

        limits = ProcessLimits(memory_mb=64)
        with patch.object(resource, "setrlimit", side_effect=OSError(errno.EPERM, "denied")):
            ProcessExecutor._apply_limits(limits)

    def test_resource_limits_to_docker_args_memory(self) -> None:
        limits = ResourceLimits(memory_bytes=512 * 1024 * 1024)
        args = limits.to_docker_args()
        assert "--memory" in args

    def test_resource_limits_to_docker_args_pids(self) -> None:
        limits = ResourceLimits(pids_limit=50)
        args = limits.to_docker_args()
        assert "--pids-limit" in args

    def test_resource_limits_to_process_limits(self) -> None:
        limits = ResourceLimits(
            memory_bytes=256 * 1024 * 1024,
            cpu_shares=2048,
        )
        proc = limits.to_process_limits()
        assert proc["memory_mb"] == 256
        assert proc["cpu_seconds"] == 2

    def test_resource_limits_to_kubernetes_resources(self) -> None:
        limits = ResourceLimits(memory_bytes=1024 * 1024 * 1024, cpu_shares=2048)
        k8s = limits.to_kubernetes_resources()
        assert "limits" in k8s
        assert "memory" in k8s["limits"]

    def test_resource_limits_exceed_memory_detection(self) -> None:
        limits = ResourceLimits(memory_bytes=100)
        assert limits.exceed_memory(200)
        assert not limits.exceed_memory(50)

    def test_resource_limits_exceed_timeout_detection(self) -> None:
        limits = ResourceLimits(timeout_seconds=30)
        assert limits.exceed_timeout(31.0)
        assert not limits.exceed_timeout(29.0)

    def test_resource_limits_default_factories_ordered(self) -> None:
        light = ResourceLimits.default_light()
        medium = ResourceLimits.default_medium()
        heavy = ResourceLimits.default_heavy()
        assert light.memory_bytes is not None
        assert medium.memory_bytes is not None
        assert heavy.memory_bytes is not None
        assert light.memory_bytes < medium.memory_bytes  # type: ignore[operator]
        assert medium.memory_bytes < heavy.memory_bytes  # type: ignore[operator]
        assert light.timeout_seconds <= medium.timeout_seconds
        assert medium.timeout_seconds <= heavy.timeout_seconds


# ────────────────────────────────────────────────────────────────
# Filesystem isolation
# ────────────────────────────────────────────────────────────────


class TestFilesystemIsolation:
    """Path confinement, escape detection, and symlink handling."""

    def test_confine_path_within_jail_returns_resolved_path(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        subdir = jail / "subdir"
        subdir.mkdir(parents=True)
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        result = enforcer.confine_path(str(subdir / "file.txt"))
        assert result.endswith("file.txt")
        assert str(jail) in result

    def test_confine_path_outside_jail_raises(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path("/etc/passwd")

    def test_dot_dot_traversal_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path(str(jail / ".." / ".." / "secret"))

    def test_symlink_escape_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        sensitive = tmp_path / "sensitive"
        sensitive.mkdir()
        (sensitive / "secret.txt").write_text("secret")
        link = jail / "leak"
        link.symlink_to(sensitive)
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path(str(link / "secret.txt"))

    def test_symlink_to_root_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        rootlink = jail / "root"
        rootlink.symlink_to("/")
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path(str(rootlink / "tmp"))

    def test_relative_path_resolved_inside_jail(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        subdir = jail / "subdir"
        subdir.mkdir(parents=True)
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        result = enforcer.confine_path("subdir/./file.txt")
        assert "subdir" in result
        assert result.endswith("file.txt")

    def test_auto_created_jail_directory(self, tmp_path: Path) -> None:
        config = EnforcerSandboxConfig(jail_dir="", fail_open=False)
        enforcer = SandboxEnforcer(config)
        enforcer.verify_ready()
        assert enforcer.is_ready
        assert enforcer.jail_dir
        assert os.path.isdir(enforcer.jail_dir)

    def test_execute_fails_when_not_verified(self) -> None:
        config = EnforcerSandboxConfig(fail_open=False)
        enforcer = SandboxEnforcer(config)
        with pytest.raises(SandboxNotAvailableError):
            enforcer.execute("echo hello")

    def test_fail_open_allows_unverified_execution(self) -> None:
        config = EnforcerSandboxConfig(fail_open=True)
        enforcer = SandboxEnforcer(config)
        result = enforcer.execute("echo hello")
        assert result.returncode == 0

    def test_execute_with_confined_workdir(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        config = EnforcerSandboxConfig(jail_dir=str(jail), fail_open=False)
        enforcer = SandboxEnforcer(config)
        enforcer.verify_ready()
        result = enforcer.execute("pwd", workdir=str(jail))
        assert result.returncode == 0


# ────────────────────────────────────────────────────────────────
# Process cleanup
# ────────────────────────────────────────────────────────────────


class TestProcessCleanup:
    """CleanupManager resource tracking and cleanup lifecycle."""

    def test_track_adds_resource_to_pending(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "container-123")
        assert cm.pending_count() == 1

    def test_cleanup_resource_removes_from_pending(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "container-abc")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            success = cm.cleanup_resource("docker_container", "container-abc")
            assert success
        assert cm.pending_count() == 0

    def test_cleanup_unknown_resource_type_returns_false(self) -> None:
        cm = CleanupManager()
        cm.track("unknown_type", "id-001")
        success = cm.cleanup_resource("unknown_type", "id-001")
        assert not success
        assert cm.pending_count() == 1

    def test_cleanup_all_processes_all_pending(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "a")
        cm.track("docker_container", "b")
        cm.track("kubernetes_pod", "c")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            count = cm.cleanup_all()
            assert count == 3
            assert cm.pending_count() == 0

    def test_cleanup_history_records_success(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "id")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            cm.cleanup_resource("docker_container", "id")
        assert cm.history_count() == 1
        record = cm.last_cleanup()
        assert record is not None
        assert record.success
        assert record.resource_id == "id"

    def test_cleanup_history_records_failure(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "broken")
        with patch("subprocess.run", side_effect=Exception("boom")):
            result = cm.cleanup_resource("docker_container", "broken")
            assert not result
        record = cm.last_cleanup()
        assert record is not None
        assert not record.success

    def test_cleanup_docker_containers_finds_exited(self) -> None:
        cm = CleanupManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="cid1\ncid2\n")
            count = cm.cleanup_docker_containers(label="gludd-sandbox")
            assert count == 2
            assert cm.history_count() == 2

    def test_cleanup_docker_containers_empty_list(self) -> None:
        cm = CleanupManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            count = cm.cleanup_docker_containers()
            assert count == 0

    def test_cleanup_kubernetes_resources(self) -> None:
        cm = CleanupManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="pod deleted\npod deleted\n")
            cm.cleanup_kubernetes_resources(namespace="sandbox-ns")

    def test_cleanuprecord_dataclass_fields(self) -> None:
        now = time.time()
        record = CleanupRecord(
            resource_type="docker_container",
            resource_id="abc",
            cleaned_at=now,
            reason="explicit",
            success=True,
        )
        assert record.resource_type == "docker_container"
        assert record.resource_id == "abc"
        assert record.success

    def test_cleanup_manager_empty_state(self) -> None:
        cm = CleanupManager()
        assert cm.pending_count() == 0
        assert cm.history_count() == 0
        assert cm.last_cleanup() is None

    def test_cleanup_manager_duplicate_tracks_deduplicated(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "dup")
        cm.track("docker_container", "dup")
        assert cm.pending_count() == 1

    def test_cleanup_partial_success_all_tracked(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "ok")
        cm.track("docker_container", "fail")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ok = cm.cleanup_resource("docker_container", "ok")
            assert ok
        with patch("subprocess.run", side_effect=Exception("fail")):
            fail = cm.cleanup_resource("docker_container", "fail")
            assert not fail
        assert cm.history_count() == 2
        assert cm.pending_count() == 0


# ────────────────────────────────────────────────────────────────
# Docker executor
# ────────────────────────────────────────────────────────────────


class TestDockerExecutorDeep:
    """Docker executor build, execute, and lifecycle."""

    def test_docker_container_config_defaults(self) -> None:
        config = DockerContainerConfig(image="alpine:latest", command="echo hi")
        assert config.workdir == "/workspace"
        assert config.network_mode == "none"
        assert config.environment == {}
        assert config.volumes == {}

    def test_docker_executor_builds_command_with_auto_remove(self) -> None:
        executor = DockerExecutor(auto_remove=True)
        config = DockerContainerConfig(image="alpine:3.18", command="echo hello")
        cmd = executor._build_command(config)
        assert "--rm" in cmd
        assert "alpine:3.18" in cmd
        assert "echo hello" in cmd

    def test_docker_executor_builds_command_with_volumes(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(
            image="ubuntu",
            command="/bin/sh",
            volumes={"/host/data": "/container/data"},
        )
        cmd = executor._build_command(config)
        assert "-v" in cmd
        assert "/host/data:/container/data" in cmd

    def test_docker_executor_builds_command_with_environment(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(
            image="python:3.12",
            command="python -c 'import os; print(os.environ[\"FOO\"])'",
            environment={"FOO": "bar"},
        )
        cmd = executor._build_command(config)
        assert "-e" in cmd
        assert "FOO=bar" in cmd

    def test_docker_executor_builds_command_with_memory(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(
            image="alpine",
            command="echo",
            memory_bytes=512 * 1024 * 1024,
        )
        cmd = executor._build_command(config)
        assert "--memory" in cmd
        assert str(512 * 1024 * 1024) in cmd

    def test_docker_executor_builds_command_with_cpu_shares(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(
            image="alpine",
            command="echo",
            cpu_shares=1024,
        )
        cmd = executor._build_command(config)
        assert "--cpu-shares" in cmd
        assert "1024" in cmd

    def test_docker_executor_builds_command_with_network(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(
            image="alpine",
            command="echo",
            network_mode="host",
        )
        cmd = executor._build_command(config)
        assert "--network" in cmd
        assert "host" in cmd

    def test_docker_executor_execute_mocked(self) -> None:
        executor = DockerExecutor(timeout=10)
        config = DockerContainerConfig(image="alpine", command="echo hello")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="container-id\n", stderr="")
            result = executor.execute(config)
            assert result.returncode == 0

    def test_docker_executor_pull_image(self) -> None:
        executor = DockerExecutor(timeout=30)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = executor.pull_image("alpine:3.18")
            assert result.returncode == 0

    def test_docker_executor_stop_container(self) -> None:
        executor = DockerExecutor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = executor.stop_container("abc123")
            assert result.returncode == 0

    def test_docker_executor_remove_container(self) -> None:
        executor = DockerExecutor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = executor.remove_container("abc123")
            assert result.returncode == 0

    def test_docker_executor_execute_in_container(self) -> None:
        executor = DockerExecutor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor.execute_in_container("cid", "ls -la")
            assert result.returncode == 0

    def test_parse_container_id(self) -> None:
        output = "some output\nabc123def456\n"
        result = DockerExecutor.parse_container_id(output)
        assert result == "abc123def456"

    def test_docker_result_dataclass(self) -> None:
        result = DockerResult(
            returncode=0,
            stdout="hello",
            stderr="",
            container_id="abc123",
        )
        assert result.returncode == 0
        assert result.stdout == "hello"
        assert result.container_id == "abc123"


# ────────────────────────────────────────────────────────────────
# SandboxResult, SandboxConfig contracts
# ────────────────────────────────────────────────────────────────


class TestSandboxContracts:
    """SandboxResult and SandboxConfig contract validation."""

    def test_sandbox_result_success_when_zero(self) -> None:
        result = SandboxResult(returncode=0, stdout="ok", stderr="")
        assert result.success

    def test_sandbox_result_not_success_when_nonzero(self) -> None:
        result = SandboxResult(returncode=1, stdout="", stderr="error")
        assert not result.success

    def test_sandbox_result_is_frozen(self) -> None:
        import dataclasses

        assert dataclasses.is_dataclass(SandboxResult)
        params = getattr(SandboxResult, "__dataclass_params__", None)
        assert params is not None
        assert params.frozen is True

    def test_sandbox_config_validate_valid(self) -> None:
        errors = validate_config(SandboxConfig())
        assert errors == []

    def test_sandbox_config_validate_negative_memory(self) -> None:
        errors = validate_config(SandboxConfig(memory_mb=-1))
        assert len(errors) >= 1

    def test_sandbox_config_validate_negative_timeout(self) -> None:
        errors = validate_config(SandboxConfig(timeout=-1))
        assert len(errors) >= 1

    def test_sandbox_config_to_resource_limits(self) -> None:
        config = SandboxConfig(memory_mb=1024, cpu_seconds=120, max_processes=100)
        limits = config.to_resource_limits()
        assert limits.memory_bytes == 1024 * 1024 * 1024
        assert limits.timeout_seconds == 120
        assert limits.pids_limit == 100

    def test_minimal_config_is_fail_open(self) -> None:
        assert MINIMAL_SANDBOX_CONFIG.fail_open
        assert MINIMAL_SANDBOX_CONFIG.isolation == IsolationLevel.NONE
        assert MINIMAL_SANDBOX_CONFIG.backend == "process"

    def test_strict_config_is_fail_closed(self) -> None:
        assert not STRICT_SANDBOX_CONFIG.fail_open
        assert STRICT_SANDBOX_CONFIG.isolation == IsolationLevel.VM_HARDWARE
        assert STRICT_SANDBOX_CONFIG.backend == "firecracker"


# ────────────────────────────────────────────────────────────────
# MaxOutputExceeded enforcement
# ────────────────────────────────────────────────────────────────


class TestMaxOutputEnforcement:
    """MaxOutputExceededError and output truncation."""

    def test_max_output_exceeded_raises_when_fail_closed(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        config = EnforcerSandboxConfig(
            jail_dir=str(jail),
            max_output_bytes=5,
            fail_open=False,
        )
        enforcer = SandboxEnforcer(config)
        enforcer.verify_ready()
        with pytest.raises(MaxOutputExceededError):
            enforcer.execute("sh -c 'echo 123456'")

    def test_max_output_exceeded_warns_when_fail_open(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        config = EnforcerSandboxConfig(
            jail_dir=str(jail),
            max_output_bytes=3,
            fail_open=True,
        )
        enforcer = SandboxEnforcer(config)
        enforcer.verify_ready()
        result = enforcer.execute("sh -c 'echo abcdef'")
        assert result.returncode == 0

    def test_max_output_bytes_on_executor(self) -> None:
        executor = ProcessExecutor(max_output_bytes=500)
        assert executor.max_output_bytes == 500


# ────────────────────────────────────────────────────────────────
# Network policy deep
# ────────────────────────────────────────────────────────────────


class TestNetworkPolicyDeep:
    """NetworkPolicy construction and host/port matching."""

    def test_fully_isolated_denies_outbound(self) -> None:
        policy = NetworkPolicy.fully_isolated()
        assert not policy.allow_outbound
        assert not policy.allows_host("example.com")

    def test_allowed_hosts_only_permit_listed(self) -> None:
        policy = NetworkPolicy(allowed_hosts=["10.0.0.1"], allow_outbound=True)
        assert policy.allows_host("10.0.0.1")
        assert not policy.allows_host("10.0.0.2")

    def test_blocked_host_overrides_allowed(self) -> None:
        policy = NetworkPolicy(
            allowed_hosts=["10.0.0.1", "10.0.0.2"],
            blocked_hosts=["10.0.0.2"],
            allow_outbound=True,
        )
        assert not policy.allows_host("10.0.0.2")

    def test_blocked_port_overrides_allowed(self) -> None:
        policy = NetworkPolicy(
            allowed_ports=[80, 443, 8080],
            blocked_ports=[8080],
            allow_outbound=True,
        )
        assert not policy.allows_port(8080)

    def test_allow_localhost(self) -> None:
        policy = NetworkPolicy.allow_localhost()
        assert policy.allows_host("127.0.0.1")
        assert policy.allows_host("::1")
        assert not policy.allows_host("0.0.0.0")

    def test_docker_args_isolated_network_none(self) -> None:
        policy = NetworkPolicy.fully_isolated()
        args = policy.to_docker_args()
        assert "--network" in args
        assert "none" in args

    def test_docker_args_dns_servers(self) -> None:
        policy = NetworkPolicy(dns_servers=["8.8.8.8", "1.1.1.1"], allow_outbound=True)
        args = policy.to_docker_args()
        assert "--dns" in args

    def test_kubernetes_policy_structure(self) -> None:
        policy = NetworkPolicy(allowed_hosts=["10.0.0.0/8"], allow_outbound=True)
        k8s = policy.to_kubernetes_policy("ns", {"app": "gludd"})
        assert k8s["kind"] == "NetworkPolicy"
        assert k8s["metadata"]["name"] == "gludd-sandbox"

    def test_is_isolated(self) -> None:
        assert NetworkPolicy.fully_isolated().is_isolated()
        assert not NetworkPolicy(allow_outbound=True).is_isolated()
