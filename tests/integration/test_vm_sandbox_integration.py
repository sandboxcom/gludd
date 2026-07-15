"""Integration tests for VM sandbox backends (Firecracker + gVisor).

Covers the full apply→verify→release lifecycle, auto-detection chain, AgentExecutor
stub, image builder, and concurrent backend access — all mocked since we don't have
actual /dev/kvm or runsc binaries on the test host.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.security.permissions import Capability, PermissionSpec
from general_ludd.security.sandboxes import (
    SandboxHandle,
    SandboxTarget,
)
from general_ludd.security.sandboxes.vm.agent_executor import AgentExecutor
from general_ludd.security.sandboxes.vm.firecracker_backend import FirecrackerBackend
from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend
from general_ludd.security.sandboxes.vm.image_builder import (
    CACHE_DIR,
    build_rootfs,
    verify_image,
)


def _make_spec(agent_type: str = "sonnet", caps: list[Capability] | None = None) -> PermissionSpec:
    if caps is None:
        caps = [Capability.FILE_READ]
    return PermissionSpec(
        agent_type=agent_type,
        capabilities=tuple(caps),
        allowed_models=["sonnet"],
        max_ttl_seconds=3600,
    )


def _make_target() -> SandboxTarget:
    return SandboxTarget(pid=42, directory="/tmp/sandbox-test")


# ---------------------------------------------------------------------------
# AgentExecutor
# ---------------------------------------------------------------------------


class TestAgentExecutor:
    def test_name_constant(self):
        assert AgentExecutor.name == "agent_executor"

    def test_receive_and_execute_returns_dict(self):
        target = _make_target()
        result = AgentExecutor.receive_and_execute(target)
        assert isinstance(result, dict)
        assert "exit_code" in result
        assert "stdout" in result
        assert "stderr" in result
        assert "wall_time_s" in result
        assert result.get("stub") is True

    def test_receive_and_execute_exit_code_zero(self):
        result = AgentExecutor.receive_and_execute(_make_target())
        assert result["exit_code"] == 0

    def test_stub_returns_empty_stdout_stderr(self):
        result = AgentExecutor.receive_and_execute(_make_target())
        assert result["stdout"] == b""
        assert result["stderr"] == b""

    def test_roundtrip_target_unchanged(self):
        target = _make_target()
        _ = AgentExecutor.receive_and_execute(target)
        assert target.pid == 42
        assert target.directory == "/tmp/sandbox-test"


# ---------------------------------------------------------------------------
# Image Builder
# ---------------------------------------------------------------------------


class TestImageBuilder:
    def test_cache_dir_exists(self):
        assert Path.home() / ".cache" / "gludd" / "sandbox" == CACHE_DIR

    def test_build_rootfs_returns_path(self):
        result = build_rootfs("/tmp/test_rootfs.ext4")
        assert isinstance(result, Path)
        assert str(result) == "/tmp/test_rootfs.ext4"

    def test_build_rootfs_with_path_object(self):
        result = build_rootfs(Path("/tmp/test_rootfs.ext4"))
        assert isinstance(result, Path)

    def test_verify_image_nonexistent(self):
        assert verify_image("/nonexistent/image_12345.ext4") is False

    def test_verify_image_exists_true(self, tmp_path: Path):
        img = tmp_path / "test_rootfs.ext4"
        img.write_bytes(b"\x00" * 1024)
        assert verify_image(str(img)) is True


# ---------------------------------------------------------------------------
# Firecracker Backend
# ---------------------------------------------------------------------------


class TestFirecrackerBackend:
    def test_name_constant(self):
        assert FirecrackerBackend.name == "firecracker"

    def test_available_is_bool(self):
        result = FirecrackerBackend.available()
        assert isinstance(result, bool)

    @patch.object(FirecrackerBackend, "available", return_value=True)
    def test_apply_returns_applied_handle_when_available(self, _mock_avail: MagicMock):
        spec = _make_spec()
        target = _make_target()
        handle = FirecrackerBackend.apply(spec, target)
        assert isinstance(handle, SandboxHandle)
        assert handle.backend == "firecracker"
        assert handle.applied is True
        assert handle.extra.get("stub") is True

    @patch.object(FirecrackerBackend, "available", return_value=False)
    def test_apply_returns_fail_open_handle_when_unavailable(self, _mock_avail: MagicMock):
        spec = _make_spec()
        target = _make_target()
        handle = FirecrackerBackend.apply(spec, target)
        assert handle.applied is False
        assert "reason" in handle.extra

    @patch.object(FirecrackerBackend, "available", return_value=True)
    def test_apply_token_includes_agent_type(self, _mock_avail: MagicMock):
        spec = _make_spec(agent_type="opus")
        handle = FirecrackerBackend.apply(spec, _make_target())
        assert "opus" in handle.token

    def test_verify_not_applied_handle(self):
        handle = SandboxHandle(backend="firecracker", token="test", applied=False, extra={"reason": "absent"})
        findings = FirecrackerBackend.verify(_make_spec(), handle)
        assert len(findings) >= 1
        assert findings[0].severity == "fail"
        assert "not applied" in findings[0].message

    def test_verify_applied_stub_handle(self):
        handle = SandboxHandle(backend="firecracker", token="test", applied=True, extra={"stub": True})
        findings = FirecrackerBackend.verify(_make_spec(), handle)
        assert len(findings) >= 1
        assert findings[0].severity == "warn"
        assert "stub" in findings[0].message.lower()

    def test_release_does_not_raise(self):
        handle = SandboxHandle(backend="firecracker", token="test", extra={"stub": True})
        FirecrackerBackend.release(handle)

    def test_release_unapplied_handle(self):
        handle = SandboxHandle(backend="firecracker", token="test", applied=False)
        FirecrackerBackend.release(handle)


# ---------------------------------------------------------------------------
# Gvisor Backend
# ---------------------------------------------------------------------------


class TestGvisorBackend:
    def test_name_constant(self):
        assert GvisorBackend.name == "gvisor"

    def test_available_is_bool(self):
        result = GvisorBackend.available()
        assert isinstance(result, bool)

    @patch.object(GvisorBackend, "available", return_value=True)
    def test_apply_returns_applied_handle_when_available(self, _mock_avail: MagicMock):
        spec = _make_spec()
        target = _make_target()
        handle = GvisorBackend.apply(spec, target)
        assert isinstance(handle, SandboxHandle)
        assert handle.backend == "gvisor"
        assert handle.applied is True
        assert handle.extra.get("stub") is True

    @patch.object(GvisorBackend, "available", return_value=False)
    def test_apply_returns_fail_open_handle_when_unavailable(self, _mock_avail: MagicMock):
        handle = GvisorBackend.apply(_make_spec(), _make_target())
        assert handle.applied is False
        assert "reason" in handle.extra

    def test_verify_not_applied_handle(self):
        handle = SandboxHandle(backend="gvisor", token="test", applied=False, extra={"reason": "absent"})
        findings = GvisorBackend.verify(_make_spec(), handle)
        assert len(findings) >= 1
        assert findings[0].severity == "fail"

    def test_verify_applied_stub_handle(self):
        handle = SandboxHandle(backend="gvisor", token="test", applied=True, extra={"stub": True})
        findings = GvisorBackend.verify(_make_spec(), handle)
        assert len(findings) >= 1
        assert findings[0].severity == "warn"

    def test_release_does_not_raise(self):
        handle = SandboxHandle(backend="gvisor", token="test", extra={"stub": True})
        GvisorBackend.release(handle)

    def test_release_unapplied_handle(self):
        handle = SandboxHandle(backend="gvisor", token="test", applied=False)
        GvisorBackend.release(handle)


# ---------------------------------------------------------------------------
# Full Lifecycle: Firecracker
# ---------------------------------------------------------------------------


class TestFirecrackerFullLifecycle:
    @patch.object(FirecrackerBackend, "available", return_value=True)
    def test_apply_verify_release_cycle(self, _mock_avail: MagicMock):
        spec = _make_spec()
        target = _make_target()

        handle = FirecrackerBackend.apply(spec, target)
        assert handle.applied is True

        findings = FirecrackerBackend.verify(spec, handle)
        assert len(findings) >= 1
        for f in findings:
            assert f.severity in ("ok", "warn", "fail")

        FirecrackerBackend.release(handle)

    @patch.object(FirecrackerBackend, "available", return_value=False)
    def test_apply_verify_release_fail_open(self, _mock_avail: MagicMock):
        spec = _make_spec()
        target = _make_target()

        handle = FirecrackerBackend.apply(spec, target)
        assert handle.applied is False

        findings = FirecrackerBackend.verify(spec, handle)
        assert any(f.severity == "fail" for f in findings)

        FirecrackerBackend.release(handle)

    def test_multiple_apply_verify_cycles(self):
        with patch.object(FirecrackerBackend, "available", return_value=True):
            for i in range(3):
                spec = _make_spec(agent_type=f"agent_{i}")
                handle = FirecrackerBackend.apply(spec, _make_target())
                assert handle.applied is True
                findings = FirecrackerBackend.verify(spec, handle)
                assert len(findings) >= 1
                FirecrackerBackend.release(handle)


# ---------------------------------------------------------------------------
# Full Lifecycle: Gvisor
# ---------------------------------------------------------------------------


class TestGvisorFullLifecycle:
    @patch.object(GvisorBackend, "available", return_value=True)
    def test_apply_verify_release_cycle(self, _mock_avail: MagicMock):
        spec = _make_spec()
        target = _make_target()

        handle = GvisorBackend.apply(spec, target)
        assert handle.applied is True

        findings = GvisorBackend.verify(spec, handle)
        assert len(findings) >= 1

        GvisorBackend.release(handle)

    @patch.object(GvisorBackend, "available", return_value=False)
    def test_apply_verify_release_fail_open(self, _mock_avail: MagicMock):
        handle = GvisorBackend.apply(_make_spec(), _make_target())
        assert handle.applied is False
        findings = GvisorBackend.verify(_make_spec(), handle)
        assert any(f.severity == "fail" for f in findings)
        GvisorBackend.release(handle)

    def test_multiple_apply_verify_cycles(self):
        with patch.object(GvisorBackend, "available", return_value=True):
            for i in range(3):
                handle = GvisorBackend.apply(_make_spec(agent_type=f"agent_{i}"), _make_target())
                assert handle.applied is True
                GvisorBackend.release(handle)


# ---------------------------------------------------------------------------
# Auto-Detection Chain (mock platform conditions)
# ---------------------------------------------------------------------------


class TestAutoDetectionChain:
    def test_both_available_firecracker_preferred(self):
        with (
            patch.object(FirecrackerBackend, "available", return_value=True),
            patch.object(GvisorBackend, "available", return_value=True),
        ):
            from general_ludd.security.sandboxes.detect import auto
            backend = auto()
            assert backend is not None
            assert backend.name == "firecracker"

    def test_firecracker_unavailable_falls_to_gvisor(self):
        with (
            patch.object(FirecrackerBackend, "available", return_value=False),
            patch.object(GvisorBackend, "available", return_value=True),
        ):
            from general_ludd.security.sandboxes.detect import auto
            backend = auto()
            assert backend is not None
            assert backend.name == "gvisor"

    def test_both_unavailable_linux_falls_to_landlock_or_lower(self):
        with (
            patch.object(FirecrackerBackend, "available", return_value=False),
            patch.object(GvisorBackend, "available", return_value=False),
            patch("sys.platform", "linux"),
        ):
            from general_ludd.security.sandboxes.detect import auto
            backend = auto()
            if backend is not None:
                assert backend.name not in ("firecracker", "gvisor")


# ---------------------------------------------------------------------------
# Concurrent Access
# ---------------------------------------------------------------------------


class TestConcurrentBackendAccess:
    def test_concurrent_firecracker_apply_cycles(self):
        errors: list[Exception] = []

        def cycle(agent_id: int):
            try:
                with patch.object(FirecrackerBackend, "available", return_value=True):
                    spec = _make_spec(agent_type=f"agent_{agent_id}")
                    handle = FirecrackerBackend.apply(spec, _make_target())
                    assert handle.applied is True
                    findings = FirecrackerBackend.verify(spec, handle)
                    assert len(findings) >= 1
                    FirecrackerBackend.release(handle)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=cycle, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_gvisor_apply_cycles(self):
        errors: list[Exception] = []

        def cycle(agent_id: int):
            try:
                with patch.object(GvisorBackend, "available", return_value=True):
                    handle = GvisorBackend.apply(_make_spec(agent_type=f"agent_{agent_id}"), _make_target())
                    assert handle.applied is True
                    GvisorBackend.release(handle)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=cycle, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_mixed_firecracker_gvisor_concurrent(self):
        errors: list[Exception] = []

        def fc_cycle(i: int):
            try:
                with patch.object(FirecrackerBackend, "available", return_value=True):
                    handle = FirecrackerBackend.apply(_make_spec(agent_type=f"fc_{i}"), _make_target())
                    FirecrackerBackend.release(handle)
            except Exception as e:
                errors.append(e)

        def gv_cycle(i: int):
            try:
                with patch.object(GvisorBackend, "available", return_value=True):
                    handle = GvisorBackend.apply(_make_spec(agent_type=f"gv_{i}"), _make_target())
                    GvisorBackend.release(handle)
            except Exception as e:
                errors.append(e)

        threads: list[threading.Thread] = []
        for i in range(3):
            threads.append(threading.Thread(target=fc_cycle, args=(i,)))
            threads.append(threading.Thread(target=gv_cycle, args=(i,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# ---------------------------------------------------------------------------
# PermissionSpec Integration
# ---------------------------------------------------------------------------


class TestPermissionSpecSandboxIntegration:
    def test_spec_agent_type_in_handle_token(self):
        with patch.object(FirecrackerBackend, "available", return_value=True):
            spec = _make_spec(agent_type="haiku")
            handle = FirecrackerBackend.apply(spec, _make_target())
            assert "haiku" in handle.token

    def test_spec_with_multiple_capabilities(self):
        caps = [Capability.FILE_READ, Capability.FILE_WRITE, Capability.NETWORK_OUT]
        with patch.object(FirecrackerBackend, "available", return_value=True):
            spec = _make_spec(caps=caps)
            handle = FirecrackerBackend.apply(spec, _make_target())
            assert handle.applied is True

    def test_spec_with_zero_capabilities(self):
        with patch.object(FirecrackerBackend, "available", return_value=True):
            spec = _make_spec(caps=[])
            handle = FirecrackerBackend.apply(spec, _make_target())
            assert handle.applied is True

    def test_spec_allowed_models_preserved(self):
        spec = _make_spec()
        spec_dict = PermissionSpec(
            agent_type=spec.agent_type,
            capabilities=spec.capabilities,
            allowed_models=spec.allowed_models,
            max_ttl_seconds=spec.max_ttl_seconds,
            id=spec.id,
        )
        with patch.object(FirecrackerBackend, "available", return_value=True):
            handle = FirecrackerBackend.apply(spec_dict, _make_target())
            assert handle.backend == "firecracker"


# ---------------------------------------------------------------------------
# Cross-Feature: SandboxTarget Variants
# ---------------------------------------------------------------------------


class TestSandboxTargetVariants:
    def test_pid_target(self):
        target = SandboxTarget(pid=12345)
        assert target.pid == 12345
        assert target.popen is None
        assert target.directory is None
        assert target.service is None

    def test_directory_target(self):
        target = SandboxTarget(directory="/tmp/jail")
        with patch.object(GvisorBackend, "available", return_value=True):
            handle = GvisorBackend.apply(_make_spec(), target)
            assert handle.applied is True

    def test_service_target(self):
        target = SandboxTarget(service="nginx.service")
        with patch.object(FirecrackerBackend, "available", return_value=True):
            handle = FirecrackerBackend.apply(_make_spec(), target)
            assert handle.applied is True
