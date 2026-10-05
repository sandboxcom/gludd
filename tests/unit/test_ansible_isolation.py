"""Unit tests for ansible/isolation.py — ProcessIsolationConfig."""

from __future__ import annotations

import pytest

from general_ludd.ansible.isolation import (
    _SHELL_MODULES,
    _WRITE_MODULES,
    ProcessIsolationConfig,
)

PINNED_EE_IMAGE = "registry.example/gludd-ee:test@sha256:" + "a" * 64


class TestProcessIsolationConfig:
    def test_defaults_disabled(self):
        cfg = ProcessIsolationConfig()
        assert cfg.enabled is False
        assert cfg.executable == "podman"
        assert cfg.isolation_path is None
        assert cfg.hide_paths == []
        assert cfg.show_paths == []
        assert cfg.ro_paths == []
        assert cfg.block_local_tools == []

    def test_executable_stripped(self):
        cfg = ProcessIsolationConfig(executable="  docker  ")
        assert cfg.executable == "docker"

    def test_executable_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            ProcessIsolationConfig(executable="")

    def test_executable_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            ProcessIsolationConfig(executable="   ")

    def test_to_runner_kwargs_disabled(self):
        cfg = ProcessIsolationConfig()
        kwargs = cfg.to_runner_kwargs()
        assert kwargs["process_isolation"] is False
        assert kwargs["process_isolation_executable"] == "podman"

    def test_to_runner_kwargs_enabled(self):
        cfg = ProcessIsolationConfig(
            enabled=True,
            container_image=PINNED_EE_IMAGE,
            executable="docker",
            isolation_path="/tmp/iso",
            hide_paths=["/etc"],
            show_paths=["/home"],
            ro_paths=["/usr"],
        )
        kwargs = cfg.to_runner_kwargs()
        assert kwargs["process_isolation"] is True
        assert kwargs["process_isolation_executable"] == "docker"
        assert kwargs["process_isolation_path"] == "/tmp/iso"
        assert "/etc" in kwargs["process_isolation_hide_paths"]
        assert kwargs["process_isolation_show_paths"] == ["/home"]
        assert kwargs["process_isolation_ro_paths"] == ["/usr"]

    def test_to_runner_kwargs_block_local_tools_adds_paths(self):
        cfg = ProcessIsolationConfig(
            enabled=True,
            container_image=PINNED_EE_IMAGE,
            block_local_tools=["bash", "docker"],
        )
        kwargs = cfg.to_runner_kwargs()
        hide = kwargs["process_isolation_hide_paths"]
        assert "/usr/bin/bash" in hide
        assert "/var/run/docker.sock" in hide

    def test_to_runner_kwargs_block_file_write_adds_default(self):
        cfg = ProcessIsolationConfig(
            enabled=True,
            container_image=PINNED_EE_IMAGE,
            block_local_tools=["file_write"],
        )
        kwargs = cfg.to_runner_kwargs()
        hide = kwargs["process_isolation_hide_paths"]
        assert "/workspace" in hide
        assert "/project" in hide

    def test_to_runner_kwargs_no_duplicate_hide_paths(self):
        cfg = ProcessIsolationConfig(
            enabled=True,
            container_image=PINNED_EE_IMAGE,
            hide_paths=["/usr/bin/bash"],
            block_local_tools=["bash"],
        )
        kwargs = cfg.to_runner_kwargs()
        assert kwargs["process_isolation_hide_paths"].count("/usr/bin/bash") == 1

    def test_to_core_runner_kwargs(self):
        cfg = ProcessIsolationConfig(
            enabled=True,
            container_image=PINNED_EE_IMAGE,
            hide_paths=["/etc"],
            show_paths=["/home"],
            ro_paths=["/usr"],
        )
        kwargs = cfg.to_core_runner_kwargs()
        assert kwargs["connection"] == "local"
        assert "/etc" in kwargs["hide_paths"]
        assert kwargs["show_paths"] == ["/home"]
        assert kwargs["ro_paths"] == ["/usr"]

    def test_to_core_runner_kwargs_block_tools(self):
        cfg = ProcessIsolationConfig(
            enabled=True,
            container_image=PINNED_EE_IMAGE,
            block_local_tools=["bash", "file_write"],
        )
        kwargs = cfg.to_core_runner_kwargs()
        hide = kwargs["hide_paths"]
        assert "/usr/bin/bash" in hide
        assert "/workspace" in hide


class TestResolveToolPaths:
    def test_known_tool_returns_paths(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("bash")
        assert "/usr/bin/bash" in paths
        assert "/bin/sh" in paths
        assert "/bin/bash" in paths

    def test_python_includes_which_resolution(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("python")
        assert "/usr/bin/python" in paths
        assert "/usr/bin/python3" in paths

    def test_git_returns_dot_git(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("git")
        assert ".git" in paths

    def test_network_returns_empty(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("network")
        assert paths == []

    def test_unknown_tool_returns_empty(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("nonexistent_tool")
        assert paths == []

    def test_file_write_returns_default(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("file_write")
        assert "/workspace" in paths
        assert "/project" in paths


class TestIsModuleBlocked:
    def test_disabled_never_blocks(self):
        cfg = ProcessIsolationConfig(enabled=False, block_local_tools=["bash"])
        assert cfg.is_module_blocked("ansible.builtin.shell") is False

    def test_no_blocked_tools_never_blocks(self):
        cfg = ProcessIsolationConfig(
            enabled=True, container_image=PINNED_EE_IMAGE, block_local_tools=[]
        )
        assert cfg.is_module_blocked("ansible.builtin.shell") is False

    def test_blocks_shell_module(self):
        cfg = ProcessIsolationConfig(
            enabled=True, container_image=PINNED_EE_IMAGE, block_local_tools=["bash"]
        )
        assert cfg.is_module_blocked("ansible.builtin.shell") is True

    def test_blocks_legacy_shell_module(self):
        cfg = ProcessIsolationConfig(
            enabled=True, container_image=PINNED_EE_IMAGE, block_local_tools=["bash"]
        )
        assert cfg.is_module_blocked("ansible.legacy.command") is True

    def test_blocks_write_module(self):
        cfg = ProcessIsolationConfig(
            enabled=True, container_image=PINNED_EE_IMAGE, block_local_tools=["file_write"]
        )
        assert cfg.is_module_blocked("ansible.builtin.copy") is True
        assert cfg.is_module_blocked("ansible.builtin.template") is True

    def test_bash_block_does_not_block_write_modules(self):
        cfg = ProcessIsolationConfig(
            enabled=True, container_image=PINNED_EE_IMAGE, block_local_tools=["bash"]
        )
        assert cfg.is_module_blocked("ansible.builtin.copy") is False

    def test_file_write_block_does_not_block_shell(self):
        cfg = ProcessIsolationConfig(
            enabled=True, container_image=PINNED_EE_IMAGE, block_local_tools=["file_write"]
        )
        assert cfg.is_module_blocked("ansible.builtin.shell") is False

    def test_unknown_module_not_blocked(self):
        cfg = ProcessIsolationConfig(
            enabled=True,
            container_image=PINNED_EE_IMAGE,
            block_local_tools=["bash", "file_write"],
        )
        assert cfg.is_module_blocked("ansible.builtin.ping") is False


class TestModuleSets:
    def test_shell_modules_includes_canonical(self):
        assert "ansible.builtin.shell" in _SHELL_MODULES
        assert "ansible.builtin.command" in _SHELL_MODULES

    def test_write_modules_includes_canonical(self):
        assert "ansible.builtin.copy" in _WRITE_MODULES
        assert "ansible.builtin.file" in _WRITE_MODULES
