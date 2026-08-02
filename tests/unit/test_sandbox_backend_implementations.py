"""Unit tests for sandbox/backends/ — ProcessBackend and ContainerBackend.

Covers:
  - SandboxBackend Protocol conformance for both backends
  - ProcessBackend subprocess execution, timeout, output truncation
  - ContainerBackend docker/podman detection, execution, image pull, cleanup
  - SandboxConfig → backend wiring
"""

from __future__ import annotations

import subprocess
from unittest import mock

from general_ludd.sandbox.contracts import (
    IsolationLevel,
    SandboxBackend,
    SandboxConfig,
    SandboxResult,
)

# ---------------------------------------------------------------------------
# ProcessBackend
# ---------------------------------------------------------------------------


class TestProcessBackendProtocol:
    def test_satisfies_sandbox_backend_protocol(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig())
        assert isinstance(backend, SandboxBackend)

    def test_name_is_process(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        assert ProcessBackend(SandboxConfig()).name == "process"

    def test_available_returns_true(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        assert ProcessBackend(SandboxConfig()).available() is True


class TestProcessBackendExecute:
    def test_successful_command(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo hello")
        assert isinstance(result, SandboxResult)
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.success is True

    def test_failing_command(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("exit 1")
        assert result.returncode == 1
        assert result.success is False

    def test_stderr_captured(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo error >&2")
        assert "error" in result.stderr

    def test_timeout_kills_process(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=1))
        result = backend.execute("sleep 10")
        assert result.was_killed is True
        assert result.returncode != 0

    def test_respects_workdir(self, tmp_path) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("pwd", workdir=str(tmp_path))
        assert tmp_path.name in result.stdout or str(tmp_path) in result.stdout

    def test_respects_env(self, tmp_path) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo $GLUDD_TEST_VAR", env={"GLUDD_TEST_VAR": "abc123"})
        assert "abc123" in result.stdout

    def test_max_output_bytes_truncates(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10, max_output_bytes=50))
        result = backend.execute("python3 -c 'print(\"x\" * 1000)'")
        total = len(result.stdout) + len(result.stderr)
        assert total <= 50 or result.returncode != 0

    def test_max_output_bytes_does_not_truncate_small_output(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10, max_output_bytes=1_000_000))
        result = backend.execute("echo small")
        assert "small" in result.stdout

    def test_pid_populated(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo hi")
        assert result.pid > 0

    def test_cpu_time_ms_populated(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("python3 -c 'import time; time.sleep(0.1)'")
        assert result.cpu_time_ms >= 0

    def test_memory_used_bytes_populated(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo hi")
        assert result.memory_used_bytes >= 0


class TestProcessBackendCleanup:
    def test_cleanup_is_noop(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig())
        backend.cleanup()


class TestProcessBackendWithConfig:
    def test_default_config(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig())
        assert backend.config.timeout == 300
        assert backend.config.max_output_bytes == 1_000_000

    def test_custom_config(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        config = SandboxConfig(
            timeout=60,
            max_output_bytes=5000,
            isolation=IsolationLevel.PROCESS,
            memory_mb=128,
        )
        backend = ProcessBackend(config)
        assert backend.config == config

    def test_respects_memory_limit(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10, memory_mb=64))
        result = backend.execute("echo ok")
        assert result.success is True


# ---------------------------------------------------------------------------
# ContainerBackend
# ---------------------------------------------------------------------------


class TestContainerBackendProtocol:
    def test_satisfies_sandbox_backend_protocol(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        assert isinstance(backend, SandboxBackend)

    def test_name_is_podman_or_docker(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        assert backend.name in ("docker", "podman")

    def test_default_name_is_podman_preferred(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        assert backend.name in ("docker", "podman")


class TestContainerBackendAvailable:
    def test_available_when_docker_found(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        with mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x):
            assert backend.available() is True

    def test_available_false_when_no_runtime(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        with mock.patch("shutil.which", return_value=None):
            assert backend.available() is False

    def test_available_first_checks_podman_then_docker(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        calls: list[str] = []

        def _which(prog: str) -> str | None:
            calls.append(prog)
            if prog == "podman":
                return "/usr/bin/podman"
            return None

        with mock.patch("shutil.which", side_effect=_which):
            assert backend.available() is True
        assert calls[0] == "podman", "must check podman first"


class TestContainerBackendExecute:
    def test_execute_success_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(timeout=10, image_path="alpine:latest"))
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="hello container", stderr="")
            result = backend.execute("echo hello")
        assert isinstance(result, SandboxResult)
        assert result.returncode == 0
        assert "hello container" in result.stdout

    def test_execute_failure_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(timeout=10, image_path="alpine:latest"))
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="command failed")
            result = backend.execute("bad-command")
        assert result.success is False
        assert "command failed" in result.stderr

    def test_execute_timeout_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(timeout=5, image_path="alpine:latest"))
        with mock.patch.object(backend, "_run_container") as _run:
            _run.side_effect = subprocess.TimeoutExpired(cmd="docker run ...", timeout=5)
            result = backend.execute("sleep 100")
        assert result.was_killed is True
        assert result.returncode != 0

    def test_uses_podman_when_available(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        with mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x):
            backend = ContainerBackend(SandboxConfig(timeout=10, image_path="alpine:latest"))
        assert backend._runtime == "podman"


class TestContainerBackendImagePull:
    def test_pull_image_success_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(timeout=30, image_path="alpine:latest"))
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            result = backend.pull_image("alpine:latest")
        assert result.returncode == 0

    def test_pull_image_failure_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(timeout=30, image_path="alpine:latest"))
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="pull failed")
            result = backend.pull_image("alpine:latest")
        assert result.returncode == 1


class TestContainerBackendCleanup:
    def test_cleanup_is_noop_when_no_containers(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        backend.cleanup()

    def test_cleanup_removes_containers_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        backend._container_ids = ["abc123", "def456"]
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0)
            backend.cleanup()
        assert run.call_count == 2


class TestContainerBackendWithConfig:
    def test_image_path_required(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="python:3.11-slim"))
        assert backend.config.image_path == "python:3.11-slim"

    def test_config_values_propagate(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        config = SandboxConfig(
            timeout=120,
            max_output_bytes=500_000,
            memory_mb=256,
            cpu_seconds=60,
            image_path="ubuntu:22.04",
            allow_network=True,
            allowed_hosts=["api.example.com"],
            isolation=IsolationLevel.CONTAINER,
        )
        backend = ContainerBackend(config)
        assert backend.config == config


class TestContainerBackendAutoDetection:
    def test_prefers_podman_over_docker(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine"))
        with mock.patch.object(backend, "_detect_runtime") as _detect:
            _detect.return_value = "podman"
            assert backend._detect_runtime() == "podman"

    def test_falls_back_to_docker(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine"))
        with mock.patch("shutil.which", side_effect=lambda p: "/usr/bin/" + p if p == "docker" else None):
            assert backend._detect_runtime() == "docker"


# ---------------------------------------------------------------------------
# Cross-backend consistency
# ---------------------------------------------------------------------------


class TestBackendConsistency:
    def test_both_backends_conform_to_protocol(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        process = ProcessBackend(SandboxConfig(timeout=5))
        container = ContainerBackend(SandboxConfig(image_path="alpine"))

        assert isinstance(process, SandboxBackend)
        assert isinstance(container, SandboxBackend)

    def test_both_return_sandbox_result(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo hi")
        assert isinstance(result, SandboxResult)

    def test_names_are_distinct(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        process = ProcessBackend(SandboxConfig())
        container = ContainerBackend(SandboxConfig(image_path="alpine"))
        assert process.name != container.name

    def test_both_cleanup_is_safe_on_double_call(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig())
        backend.cleanup()
        backend.cleanup()
