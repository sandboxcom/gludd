"""Structural tests for sandbox/backends/firecracker_backend.py."""

from general_ludd.sandbox.backends.firecracker_backend import FirecrackerBackend
from general_ludd.sandbox.contracts import SandboxConfig


class TestFirecrackerBackend:
    def test_imports(self):
        pass

    def test_create_instance(self):
        config = SandboxConfig(backend="firecracker", timeout=60)
        backend = FirecrackerBackend(config)
        assert backend.name == "firecracker"
        assert backend.config is config

    def test_available(self):
        config = SandboxConfig(backend="firecracker")
        backend = FirecrackerBackend(config)
        result = backend.available()
        assert isinstance(result, bool)

    def test_execute_unavailable(self):
        config = SandboxConfig(backend="firecracker")
        backend = FirecrackerBackend(config)
        result = backend.execute("echo hello")
        assert result.returncode == 127
        assert "not available" in result.stderr or "not yet implemented" in result.stderr

    def test_cleanup(self):
        config = SandboxConfig(backend="firecracker")
        backend = FirecrackerBackend(config)
        backend.cleanup()
