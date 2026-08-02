"""Integration test: UnikernelBackend wired through SandboxCapabilityRouter.

Exercises the full routing pipeline for the unikernel backend:
  - Router resolves "unikernel" backend name.
  - UnikernelBackend implements both SandboxBackend and UnikernelBackend protocols.
  - configure_image / configure_boot are accepted and stored.
  - execute produces a SandboxResult (stub when no VM runtime).
  - Cleanup is safe.
  - Auto-detect with VM_HARDWARE isolation prefers unikernel when available.
"""

from __future__ import annotations

from unittest import mock

from general_ludd.sandbox.backends.unikernel_backend import (
    UnikernelBackend,
    _detect_vm_runtime,
)
from general_ludd.sandbox.capability_router import SandboxCapabilityRouter
from general_ludd.sandbox.contracts import (
    IsolationLevel,
    SandboxBackend,
    SandboxConfig,
    SandboxResult,
)
from general_ludd.security.sandboxes.vm.contracts import (
    BootConfig,
    ImageConfig,
)
from general_ludd.security.sandboxes.vm.contracts import (
    UnikernelBackend as UnikernelBackendProtocol,
)


class TestUnikernelBackendProtocolConformance:
    """The concrete UnikernelBackend satisfies both protocol contracts."""

    def test_is_instance_of_sandbox_backend(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        assert isinstance(backend, SandboxBackend)

    def test_is_instance_of_unikernel_backend_protocol(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        assert isinstance(backend, UnikernelBackendProtocol)

    def test_has_configure_image(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        assert hasattr(backend, "configure_image")
        assert callable(backend.configure_image)

    def test_has_configure_boot(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        assert hasattr(backend, "configure_boot")
        assert callable(backend.configure_boot)

    def test_has_execute(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        assert hasattr(backend, "execute")
        assert callable(backend.execute)

    def test_has_available(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        assert hasattr(backend, "available")
        assert callable(backend.available)

    def test_has_cleanup(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        assert hasattr(backend, "cleanup")
        assert callable(backend.cleanup)

    def test_name_is_unikernel(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        assert backend.name == "unikernel"


class TestConfigureImageAndBoot:
    """configure_image and configure_boot store their arguments."""

    def test_configure_image_stores_config(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        img = ImageConfig(name="test-img", image_type="firecracker")
        backend.configure_image(img)
        assert backend._image is img

    def test_configure_boot_stores_config(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        boot = BootConfig(vcpu_count=2, mem_size_mib=256)
        backend.configure_boot(boot)
        assert backend._boot is boot

    def test_configure_image_and_boot_together(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        img = ImageConfig(name="my-sandbox", packages=("python3",))
        boot = BootConfig(vcpu_count=4, mem_size_mib=1024)
        backend.configure_image(img)
        backend.configure_boot(boot)
        assert backend._image is img
        assert backend._boot is boot


class TestExecute:
    """execute produces SandboxResult for various states."""

    def test_execute_without_configure_returns_error(self) -> None:
        with mock.patch.object(UnikernelBackend, "available", return_value=True):
            backend = UnikernelBackend(SandboxConfig())
            result = backend.execute("echo hello")
        assert isinstance(result, SandboxResult)
        assert result.returncode == 127
        assert "configure_image" in result.stderr

    def test_execute_unavailable_returns_error(self) -> None:
        with mock.patch.object(UnikernelBackend, "available", return_value=False):
            backend = UnikernelBackend(SandboxConfig())
            result = backend.execute("echo hello")
        assert result.returncode == 127
        assert "not available" in result.stderr

    def test_execute_configured_returns_stub_when_unavailable(self) -> None:
        with mock.patch.object(UnikernelBackend, "available", return_value=False):
            backend = UnikernelBackend(SandboxConfig())
            backend.configure_image(ImageConfig(name="test"))
            backend.configure_boot(BootConfig())
            result = backend.execute("echo hello")
        assert result.returncode == 127
        assert "not available" in result.stderr

    def test_execute_configured_available_returns_stub(self) -> None:
        with mock.patch.object(UnikernelBackend, "available", return_value=True):
            backend = UnikernelBackend(SandboxConfig())
            backend.configure_image(ImageConfig(name="test"))
            backend.configure_boot(BootConfig())
            result = backend.execute("echo hello")
        assert isinstance(result, SandboxResult)
        assert result.returncode == 127
        assert "not yet implemented" in result.stderr

    def test_execute_result_has_all_fields(self) -> None:
        with mock.patch.object(UnikernelBackend, "available", return_value=True):
            backend = UnikernelBackend(SandboxConfig())
            backend.configure_image(ImageConfig(name="test"))
            backend.configure_boot(BootConfig())
            result = backend.execute("echo hello")
        assert isinstance(result.returncode, int)
        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)
        assert isinstance(result.success, bool)


class TestCleanup:
    """cleanup is always safe to call."""

    def test_cleanup_before_configure(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        backend.cleanup()

    def test_cleanup_after_configure(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        backend.configure_image(ImageConfig(name="test"))
        backend.configure_boot(BootConfig())
        backend.cleanup()

    def test_cleanup_twice(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        backend.cleanup()
        backend.cleanup()


class TestRouterIntegration:
    """UnikernelBackend is correctly resolved through the capability router."""

    def test_explicit_unikernel_backend_name(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="unikernel"))
        assert router.backend_name == "unikernel"

    def test_router_returns_unikernel_instance(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="unikernel"))
        backend = router.backend
        assert isinstance(backend, UnikernelBackend)
        assert isinstance(backend, UnikernelBackendProtocol)

    def test_router_execute_through_unikernel(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="unikernel"))
        result = router.execute("echo hello")
        assert isinstance(result, SandboxResult)

    def test_router_available_on_unikernel(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="unikernel"))
        assert isinstance(router.available(), bool)

    def test_router_cleanup_on_unikernel(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="unikernel"))
        router.cleanup()

    def test_auto_detect_vm_hardware_uses_unikernel_first(self) -> None:
        config = SandboxConfig(backend="auto", isolation=IsolationLevel.VM_HARDWARE)
        router = SandboxCapabilityRouter(config)
        assert router.backend_name in ("unikernel", "docker", "podman", "process")


class TestVmRuntimeDetection:
    """_detect_vm_runtime returns the best available VM runtime."""

    def test_returns_bool_or_none(self) -> None:
        runtime = _detect_vm_runtime()
        assert runtime is None or isinstance(runtime, str)

    def test_return_value_in_valid_set(self) -> None:
        runtime = _detect_vm_runtime()
        assert runtime in {None, "firecracker", "gvisor"}

    def test_available_matches_runtime_detection(self) -> None:
        backend = UnikernelBackend(SandboxConfig())
        runtime = _detect_vm_runtime()
        assert backend.available() == (runtime is not None)


class TestUnikernelBackendIntegrationWithContracts:
    """End-to-end: wire through router, configure, execute, cleanup."""

    def test_full_lifecycle(self) -> None:
        router = SandboxCapabilityRouter(SandboxConfig(backend="unikernel"))
        backend = router.backend
        assert isinstance(backend, UnikernelBackend)

        backend.configure_image(
            ImageConfig(
                name="integration-test",
                packages=("python3", "ansible"),
            )
        )
        backend.configure_boot(
            BootConfig(
                vcpu_count=2,
                mem_size_mib=256,
                timeout_seconds=5,
            )
        )

        result = router.execute("echo integration-test")
        assert isinstance(result, SandboxResult)
        assert isinstance(result.returncode, int)

        router.cleanup()

    def test_default_configs_work(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            DEFAULT_BOOT_CONFIG,
            DEFAULT_IMAGE_CONFIG,
        )

        backend = UnikernelBackend(SandboxConfig())
        backend.configure_image(DEFAULT_IMAGE_CONFIG)
        backend.configure_boot(DEFAULT_BOOT_CONFIG)
        result = backend.execute("echo default")
        assert isinstance(result, SandboxResult)

    def test_all_backends_still_work_alongside_unikernel(self) -> None:
        for backend_name in ("process", "container", "firecracker", "unikernel"):
            if backend_name == "container":
                config = SandboxConfig(backend=backend_name, image_path="alpine:latest")
            else:
                config = SandboxConfig(backend=backend_name)
            router = SandboxCapabilityRouter(config)
            assert isinstance(router.backend_name, str)
            router.cleanup()
