"""Unit tests for podman container runtime support in ansible/isolation.py."""

from __future__ import annotations

import os
from unittest.mock import patch

from general_ludd.ansible.isolation import (
    ProcessIsolationConfig,
    _podman_socket_paths,
    detect_container_runtime,
)


class TestPodmanSocketPaths:
    def test_windows_without_getuid_uses_system_socket_only(self, monkeypatch):
        monkeypatch.delattr(os, "getuid")

        assert _podman_socket_paths() == ["/run/podman/podman.sock"]

    def test_includes_user_specific_paths(self):
        paths = _podman_socket_paths()
        uid = os.getuid()
        assert f"/run/user/{uid}/podman/podman.sock" in paths
        assert f"/run/user/{uid}/podman.sock" in paths

    def test_includes_system_socket_path(self):
        paths = _podman_socket_paths()
        assert "/run/podman/podman.sock" in paths

    def test_user_paths_use_current_uid(self):
        paths = _podman_socket_paths()
        uid = os.getuid()
        assert paths[0] == f"/run/user/{uid}/podman/podman.sock"

    def test_returns_three_paths(self):
        paths = _podman_socket_paths()
        assert len(paths) == 3


@patch("general_ludd.ansible.isolation.shutil.which")
class TestDetectContainerRuntime:
    def test_returns_podman_when_available(self, mock_which):
        mock_which.side_effect = lambda cmd: {
            "podman": "/usr/bin/podman",
            "docker": None,
        }[cmd]
        result = detect_container_runtime()
        assert result == "/usr/bin/podman"

    def test_returns_docker_when_podman_unavailable(self, mock_which):
        mock_which.side_effect = lambda cmd: {
            "podman": None,
            "docker": "/usr/bin/docker",
        }[cmd]
        result = detect_container_runtime()
        assert result == "/usr/bin/docker"

    def test_returns_none_when_neither_available(self, mock_which):
        mock_which.return_value = None
        result = detect_container_runtime()
        assert result is None

    def test_podman_preferred_over_docker(self, mock_which):
        mock_which.side_effect = lambda cmd: {
            "podman": "/usr/bin/podman",
            "docker": "/usr/bin/docker",
        }[cmd]
        result = detect_container_runtime()
        assert result == "/usr/bin/podman"


class TestPodmanToolPathResolution:
    def test_resolve_podman_returns_socket_paths(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("podman")
        uid = os.getuid()
        assert f"/run/user/{uid}/podman/podman.sock" in paths
        assert "/run/podman/podman.sock" in paths

    def test_podman_block_local_adds_sockets_to_hide(self):
        cfg = ProcessIsolationConfig(enabled=True, block_local_tools=["podman"])
        kwargs = cfg.to_runner_kwargs()
        hide = kwargs["process_isolation_hide_paths"]
        uid = os.getuid()
        assert f"/run/user/{uid}/podman/podman.sock" in hide

    def test_podman_block_local_in_core_runner_kwargs(self):
        cfg = ProcessIsolationConfig(enabled=True, block_local_tools=["podman"])
        kwargs = cfg.to_core_runner_kwargs()
        hide = kwargs["hide_paths"]
        uid = os.getuid()
        assert f"/run/user/{uid}/podman/podman.sock" in hide

    def test_podman_resolve_non_empty(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("podman")
        assert len(paths) == 3
        assert all(isinstance(p, str) for p in paths)


@patch("general_ludd.ansible.isolation.detect_container_runtime")
class TestAutoDetectRuntime:
    def test_updates_executable_to_podman_path(self, mock_detect):
        mock_detect.return_value = "/usr/local/bin/podman"
        cfg = ProcessIsolationConfig()
        result = cfg.auto_detect_runtime()
        assert result.executable == "/usr/local/bin/podman"

    def test_updates_executable_to_docker_fallback(self, mock_detect):
        mock_detect.return_value = "/usr/bin/docker"
        cfg = ProcessIsolationConfig()
        result = cfg.auto_detect_runtime()
        assert result.executable == "/usr/bin/docker"

    def test_preserves_other_fields(self, mock_detect):
        mock_detect.return_value = "/usr/bin/podman"
        cfg = ProcessIsolationConfig(
            enabled=True,
            isolation_path="/tmp/iso",
            hide_paths=["/etc"],
            show_paths=["/home"],
            ro_paths=["/usr"],
            block_local_tools=["bash"],
        )
        result = cfg.auto_detect_runtime()
        assert result.enabled is True
        assert result.isolation_path == "/tmp/iso"
        assert result.hide_paths == ["/etc"]
        assert result.show_paths == ["/home"]
        assert result.ro_paths == ["/usr"]
        assert result.block_local_tools == ["bash"]

    def test_returns_copy_not_same_object(self, mock_detect):
        mock_detect.return_value = "/usr/bin/podman"
        cfg = ProcessIsolationConfig()
        result = cfg.auto_detect_runtime()
        assert result is not cfg

    def test_no_runtime_found_returns_copy_with_default(self, mock_detect):
        mock_detect.return_value = None
        cfg = ProcessIsolationConfig()
        result = cfg.auto_detect_runtime()
        assert result.executable == "podman"
        assert result is not cfg


class TestPodmanDefaultExecutable:
    def test_default_executable_is_podman(self):
        cfg = ProcessIsolationConfig()
        assert cfg.executable == "podman"

    def test_to_runner_kwargs_default_executable(self):
        cfg = ProcessIsolationConfig(enabled=True)
        kwargs = cfg.to_runner_kwargs()
        assert kwargs["process_isolation_executable"] == "podman"

    def test_executable_can_be_overridden_to_docker(self):
        cfg = ProcessIsolationConfig(executable="docker")
        assert cfg.executable == "docker"

    def test_podman_and_docker_tools_coexist(self):
        cfg = ProcessIsolationConfig(
            enabled=True, block_local_tools=["podman", "docker"]
        )
        kwargs = cfg.to_runner_kwargs()
        hide = kwargs["process_isolation_hide_paths"]
        assert "/var/run/docker.sock" in hide
        uid = os.getuid()
        assert f"/run/user/{uid}/podman/podman.sock" in hide
