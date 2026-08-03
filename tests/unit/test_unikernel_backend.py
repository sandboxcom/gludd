"""Tests for sandbox/backends/unikernel_backend.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.sandbox.backends.unikernel_backend import UnikernelBackend
from general_ludd.sandbox.contracts import SandboxConfig
from general_ludd.security.sandboxes.vm.contracts import BootConfig, ImageConfig


@pytest.fixture
def sandbox_config() -> SandboxConfig:
    return SandboxConfig(backend="unikernel")


class TestUnikernelBackend:
    def test_name_defaults_to_unikernel(self, sandbox_config):
        backend = UnikernelBackend(sandbox_config)
        assert backend.name == "unikernel"

    def test_config_stored(self, sandbox_config):
        backend = UnikernelBackend(sandbox_config)
        assert backend.config is sandbox_config

    @patch("shutil.which")
    def test_available_true_when_firecracker(self, mock_which, sandbox_config):
        mock_which.side_effect = lambda cmd: "/usr/bin/firecracker" if cmd == "firecracker" else None
        backend = UnikernelBackend(sandbox_config)
        assert backend.available() is True

    @patch("shutil.which")
    def test_available_true_when_gvisor(self, mock_which, sandbox_config):
        mock_which.side_effect = lambda cmd: "/usr/bin/runsc" if cmd == "runsc" else None
        backend = UnikernelBackend(sandbox_config)
        assert backend.available() is True

    @patch("shutil.which")
    def test_available_false_when_no_runtime(self, mock_which, sandbox_config):
        mock_which.return_value = None
        backend = UnikernelBackend(sandbox_config)
        assert backend.available() is False

    @patch("shutil.which")
    def test_execute_unavailable_returns_error(self, mock_which, sandbox_config):
        mock_which.return_value = None
        backend = UnikernelBackend(sandbox_config)
        result = backend.execute("echo hello")
        assert result.returncode == 127
        assert "not available" in result.stderr
        assert result.was_killed is False

    @patch("shutil.which")
    def test_execute_no_image_or_boot_returns_error(self, mock_which, sandbox_config):
        mock_which.side_effect = lambda cmd: "/usr/bin/firecracker" if cmd == "firecracker" else None
        backend = UnikernelBackend(sandbox_config)
        result = backend.execute("echo hello")
        assert result.returncode == 127
        assert "configure_image" in result.stderr
        assert result.was_killed is False

    @patch("shutil.which")
    def test_execute_stub_with_config(self, mock_which, sandbox_config):
        mock_which.side_effect = lambda cmd: "/usr/bin/firecracker" if cmd == "firecracker" else None
        backend = UnikernelBackend(sandbox_config)
        backend.configure_image(ImageConfig(name="test-sandbox"))
        backend.configure_boot(BootConfig(vcpu_count=2, mem_size_mib=512))
        result = backend.execute("echo hello")
        assert result.returncode == 127
        assert "not yet implemented" in result.stderr
        assert "firecracker" in result.stderr
        assert result.was_killed is False

    @patch("shutil.which")
    def test_configure_image_sets_attribute(self, mock_which, sandbox_config):
        mock_which.return_value = None
        backend = UnikernelBackend(sandbox_config)
        img = ImageConfig(name="my-sandbox")
        backend.configure_image(img)
        assert backend._image is img

    @patch("shutil.which")
    def test_configure_boot_sets_attribute(self, mock_which, sandbox_config):
        mock_which.return_value = None
        backend = UnikernelBackend(sandbox_config)
        boot = BootConfig(vcpu_count=4, mem_size_mib=1024)
        backend.configure_boot(boot)
        assert backend._boot is boot

    @patch("shutil.which")
    def test_cleanup_does_not_raise(self, mock_which, sandbox_config):
        mock_which.return_value = None
        backend = UnikernelBackend(sandbox_config)
        backend.cleanup()

    @patch("shutil.which")
    def test_vm_runtime_detected(self, mock_which, sandbox_config):
        mock_which.side_effect = lambda cmd: "/usr/bin/firecracker" if cmd == "firecracker" else None
        backend = UnikernelBackend(sandbox_config)
        assert backend._vm_runtime == "firecracker"

    @patch("shutil.which")
    def test_firecracker_priority_over_gvisor(self, mock_which, sandbox_config):
        mock_which.return_value = "/usr/bin/firecracker"
        backend = UnikernelBackend(sandbox_config)
        assert backend._vm_runtime == "firecracker"
