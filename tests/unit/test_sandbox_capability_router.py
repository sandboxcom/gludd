"""Structural tests for sandbox/capability_router.py."""

from general_ludd.sandbox.capability_router import SandboxCapabilityRouter
from general_ludd.sandbox.contracts import SandboxConfig


class TestSandboxCapabilityRouter:
    def test_imports(self):
        pass

    def test_create_instance(self):
        config = SandboxConfig(backend="process")
        router = SandboxCapabilityRouter(config)
        assert router.config is config

    def test_backend_name_process(self):
        config = SandboxConfig(backend="process")
        router = SandboxCapabilityRouter(config)
        assert router.backend_name == "process"

    def test_backend_name_auto(self):
        config = SandboxConfig(backend="auto")
        router = SandboxCapabilityRouter(config)
        assert isinstance(router.backend_name, str)

    def test_available(self):
        config = SandboxConfig(backend="process")
        router = SandboxCapabilityRouter(config)
        result = router.available()
        assert isinstance(result, bool)

    def test_execute(self):
        config = SandboxConfig(backend="process")
        router = SandboxCapabilityRouter(config)
        result = router.execute("echo hello")
        assert hasattr(result, "returncode")

    def test_cleanup(self):
        config = SandboxConfig(backend="process")
        router = SandboxCapabilityRouter(config)
        router.cleanup()

    def test_unknown_backend_fallback(self):
        config = SandboxConfig(backend="quantum_computer")
        router = SandboxCapabilityRouter(config)
        assert router.backend_name == "process"
