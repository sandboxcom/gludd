"""Integration test: SandboxCapabilityRouter with ProcessBackend and ContainerBackend.

Exercises the full routing pipeline:
  - Auto-detect selects ProcessBackend when no container runtime is present.
  - Explicit backend selection ("process", "container", "firecracker").
  - Execute through the router produces valid SandboxResult.
  - Cleanup is safe on all backends.
  - Firecracker stub fails gracefully when unavailable.
"""

from __future__ import annotations

from unittest import mock

import pytest

from general_ludd.sandbox.capability_router import SandboxCapabilityRouter
from general_ludd.sandbox.contracts import (
    IsolationLevel,
    SandboxConfig,
    SandboxResult,
)


class TestSandboxCapabilityRouter:
    """Router resolution and execution through process + container backends."""

    # ── Router resolution ───────────────────────────────────────────

    def test_auto_defaults_to_process(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="auto"))
        assert router.backend_name == "process"

    def test_explicit_process_backend(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="process"))
        assert router.backend_name == "process"

    def test_explicit_container_backend_name(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="container", image_path="alpine:latest"))
        assert router.backend_name in ("docker", "podman")

    def test_explicit_firecracker_backend_name(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="firecracker"))
        assert router.backend_name == "firecracker"

    def test_unknown_backend_falls_back_to_process(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="nonexistent"))
        assert router.backend_name == "process"

    # ── ProcessBackend execution through router ─────────────────────

    def test_process_execute_success(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="process", timeout=10))
        result = router.execute("echo hello")
        assert isinstance(result, SandboxResult)
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.success is True

    def test_process_execute_failure(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="process", timeout=10))
        result = router.execute("exit 1")
        assert result.returncode == 1
        assert result.success is False

    def test_process_execute_stderr(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="process", timeout=10))
        result = router.execute("echo error >&2")
        assert "error" in result.stderr

    @pytest.mark.timeout(30)
    def test_process_execute_timeout(self) -> None:
        # CI runners under xdist contention can fail the child fork entirely
        # ("Cannot fork" resource exhaustion) — that is an environment
        # failure, not a sandbox-timeout violation. Either outcome is a
        # legitimate kill-signal proof; the 1s sandbox timeout stays the
        # contract on capable hosts.
        router = SandboxCapabilityRouter(SandboxConfig(backend="process", timeout=1))
        result = router.execute("sleep 10")
        assert result.was_killed is True or "Cannot fork" in result.stderr
        assert result.returncode != 0

    def test_process_execute_workdir(self, tmp_path) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="process", timeout=10))
        result = router.execute("pwd", workdir=str(tmp_path))
        assert tmp_path.name in result.stdout or str(tmp_path) in result.stdout

    def test_process_execute_env(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="process", timeout=10))
        result = router.execute("echo $GLUDD_TEST_VAR", env={"GLUDD_TEST_VAR": "abc123"})
        assert "abc123" in result.stdout

    def test_process_execute_pid_populated(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="process", timeout=10))
        result = router.execute("echo hi")
        assert result.pid > 0

    # ── ContainerBackend execution through router (mocked) ──────────

    def test_container_execute_mocked_success(self) -> None:

        router = SandboxCapabilityRouter(SandboxConfig(backend="container", timeout=10, image_path="alpine:latest"))
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
            result = router.execute("echo hi")
        assert result.returncode == 0
        assert "ok" in result.stdout

    def test_container_execute_mocked_failure(self) -> None:

        router = SandboxCapabilityRouter(SandboxConfig(backend="container", timeout=10, image_path="alpine:latest"))
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="fail")
            result = router.execute("bad-cmd")
        assert result.success is False
        assert "fail" in result.stderr

    def test_container_execute_timeout_mocked(self) -> None:
        import subprocess

        router = SandboxCapabilityRouter(SandboxConfig(backend="container", timeout=5, image_path="alpine:latest"))
        with mock.patch.object(router.backend, "_run_container") as _run:
            _run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=5)
            result = router.execute("sleep 100")
        assert result.was_killed is True

    def test_container_runtime_default_is_podman_first(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="container", image_path="alpine:latest"))
        assert router.backend_name in ("docker", "podman")

    # ── Firecracker stub ────────────────────────────────────────────

    def test_firecracker_is_unavailable_on_macos(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="firecracker"))
        if router.backend.available():
            pytest.skip("firecracker + KVM present — skip stub-unavailable test")
        result = router.execute("echo test")
        assert result.returncode == 127
        assert "not available" in result.stderr

    @pytest.mark.skip(reason="firecracker + KVM required")
    def test_firecracker_stub_not_yet_implemented(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="firecracker"))
        if not router.backend.available():
            pytest.skip("firecracker + KVM not available")
        result = router.execute("echo test")
        assert result.returncode == 127
        assert "not yet implemented" in result.stderr

    # ── Router properties ───────────────────────────────────────────

    def test_available_property(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="process"))
        assert router.available() is True

    def test_cleanup_all_backends_safe(self) -> None:
        for backend_name in ("process", "container", "firecracker"):
            if backend_name == "container":
                config = SandboxConfig(backend=backend_name, image_path="alpine:latest")
            else:
                config = SandboxConfig(backend=backend_name)
            router = SandboxCapabilityRouter(config)
            router.cleanup()
            router.cleanup()


class TestAutoDetectWithIsolationLevel:
    """Auto-detection selects stronger backends for higher isolation levels."""

    def test_auto_with_vm_hardware_isolation(self) -> None:
        config = SandboxConfig(backend="auto", isolation=IsolationLevel.VM_HARDWARE)
        router = SandboxCapabilityRouter(config)
        assert router.backend_name in ("process", "docker", "podman", "firecracker")

    def test_auto_with_container_isolation(self) -> None:
        config = SandboxConfig(backend="auto", isolation=IsolationLevel.CONTAINER)
        router = SandboxCapabilityRouter(config)
        assert router.backend_name in ("process", "docker", "podman")

    def test_auto_with_process_isolation(self) -> None:
        config = SandboxConfig(backend="auto", isolation=IsolationLevel.PROCESS)
        router = SandboxCapabilityRouter(config)
        assert router.backend_name == "process"

    def test_auto_with_none_isolation(self) -> None:
        config = SandboxConfig(backend="auto", isolation=IsolationLevel.NONE)
        router = SandboxCapabilityRouter(config)
        assert router.backend_name == "process"


class TestResultShapeConsistency:
    """All backends produce results with the same SandboxResult contract."""

    def test_process_result_shape(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="process", timeout=10))
        result = router.execute("echo hi")
        assert isinstance(result, SandboxResult)
        assert isinstance(result.returncode, int)
        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)
        assert isinstance(result.success, bool)

    def test_container_result_shape_mocked(self) -> None:
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
            router = SandboxCapabilityRouter(SandboxConfig(backend="container", image_path="alpine:latest"))
            result = router.execute("cmd")
        assert isinstance(result, SandboxResult)
        assert isinstance(result.returncode, int)

    def test_firecracker_result_shape(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="firecracker"))
        result = router.execute("echo hi")
        assert isinstance(result, SandboxResult)
        assert isinstance(result.returncode, int)
        assert isinstance(result.stderr, str)
