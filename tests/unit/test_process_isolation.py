"""Unit tests for Ansible process isolation configuration."""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

from agentic_harness.ansible.action_policy import (
    ActionManifest,
    ActionPolicyConfig,
    validate_action,
)
from agentic_harness.ansible.isolation import ProcessIsolationConfig
from agentic_harness.ansible.runner import AnsibleRunnerAdapter


class TestProcessIsolationConfigDefaults:
    def test_defaults_disabled(self):
        cfg = ProcessIsolationConfig()
        assert cfg.enabled is False

    def test_default_executable_is_podman(self):
        cfg = ProcessIsolationConfig()
        assert cfg.executable == "podman"

    def test_default_paths_empty(self):
        cfg = ProcessIsolationConfig()
        assert cfg.hide_paths == []
        assert cfg.show_paths == []
        assert cfg.ro_paths == []

    def test_default_block_local_tools_empty(self):
        cfg = ProcessIsolationConfig()
        assert cfg.block_local_tools == []


class TestToRunnerKwargs:
    def test_disabled_produces_empty_kwargs(self):
        cfg = ProcessIsolationConfig()
        kwargs = cfg.to_runner_kwargs()
        assert kwargs == {
            "process_isolation": False,
            "process_isolation_executable": "podman",
            "process_isolation_path": None,
            "process_isolation_hide_paths": [],
            "process_isolation_show_paths": [],
            "process_isolation_ro_paths": [],
        }

    def test_enabled_produces_correct_kwargs(self):
        cfg = ProcessIsolationConfig(
            enabled=True,
            executable="bwrap",
            isolation_path="/tmp/sandbox",
            hide_paths=["/secret"],
            show_paths=["/workspace"],
            ro_paths=["/usr"],
        )
        kwargs = cfg.to_runner_kwargs()
        assert kwargs["process_isolation"] is True
        assert kwargs["process_isolation_executable"] == "bwrap"
        assert kwargs["process_isolation_path"] == "/tmp/sandbox"
        assert kwargs["process_isolation_hide_paths"] == ["/secret"]
        assert kwargs["process_isolation_show_paths"] == ["/workspace"]
        assert kwargs["process_isolation_ro_paths"] == ["/usr"]


class TestResolveToolPaths:
    def test_bash_returns_shell_paths(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("bash")
        assert "/usr/bin/bash" in paths
        assert "/bin/sh" in paths

    def test_python_returns_python_paths(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("python")
        assert "/usr/bin/python" in paths

    def test_git_returns_git_dir(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("git")
        assert ".git" in paths

    def test_file_write_returns_project_root(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("file_write")
        assert len(paths) > 0

    def test_docker_returns_docker_sock(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("docker")
        assert "/var/run/docker.sock" in paths

    def test_network_returns_empty(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("network")
        assert paths == []

    def test_unknown_tool_returns_empty(self):
        cfg = ProcessIsolationConfig()
        paths = cfg.resolve_tool_paths("nonexistent_tool")
        assert paths == []


class TestBlockLocalTools:
    def test_block_local_tools_adds_paths_to_hide_paths(self):
        cfg = ProcessIsolationConfig(block_local_tools=["bash"])
        kwargs = cfg.to_runner_kwargs()
        hide = kwargs["process_isolation_hide_paths"]
        assert "/usr/bin/bash" in hide

    def test_block_local_tools_merges_with_explicit_hide_paths(self):
        cfg = ProcessIsolationConfig(
            hide_paths=["/explicit/path"],
            block_local_tools=["git"],
        )
        kwargs = cfg.to_runner_kwargs()
        hide = kwargs["process_isolation_hide_paths"]
        assert "/explicit/path" in hide
        assert ".git" in hide

    def test_block_multiple_tools(self):
        cfg = ProcessIsolationConfig(block_local_tools=["bash", "git"])
        kwargs = cfg.to_runner_kwargs()
        hide = kwargs["process_isolation_hide_paths"]
        assert "/usr/bin/bash" in hide
        assert ".git" in hide


class TestRunnerAdapterIsolation:
    @patch("ansible_runner.run")
    def test_adapter_accepts_isolation_config(self, mock_run: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.status = "successful"
        mock_result.rc = 0
        mock_result.events = []
        mock_run.return_value = mock_result

        iso = ProcessIsolationConfig(enabled=True, executable="bwrap")
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(
                private_data_dir=tmp,
                isolation_config=iso,
            )
            assert adapter.isolation_config is iso

    @patch("ansible_runner.run")
    def test_run_playbook_passes_isolation_kwargs(self, mock_run: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.status = "successful"
        mock_result.rc = 0
        mock_result.events = []
        mock_run.return_value = mock_result

        iso = ProcessIsolationConfig(
            enabled=True,
            executable="bwrap",
            hide_paths=["/secret"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(
                private_data_dir=tmp,
                isolation_config=iso,
            )
            dirs = adapter.prepare_job_dirs("JOB-ISO-1")
            adapter.run_playbook(
                playbook_name="noop.yml",
                private_data_dir=dirs["root"],
            )
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["process_isolation"] is True
        assert call_kwargs["process_isolation_executable"] == "bwrap"
        assert "/secret" in call_kwargs["process_isolation_hide_paths"]

    @patch("ansible_runner.run")
    def test_run_playbook_without_isolation(self, mock_run: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.status = "successful"
        mock_result.rc = 0
        mock_result.events = []
        mock_run.return_value = mock_result

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            dirs = adapter.prepare_job_dirs("JOB-ISO-2")
            adapter.run_playbook(
                playbook_name="noop.yml",
                private_data_dir=dirs["root"],
            )
        call_kwargs = mock_run.call_args[1]
        assert "process_isolation" not in call_kwargs


class TestActionPolicyIsolation:
    def test_policy_accepts_process_isolation(self):
        iso = ProcessIsolationConfig(
            enabled=True,
            block_local_tools=["bash"],
        )
        policy = ActionPolicyConfig(process_isolation=iso)
        assert policy.process_isolation is not None
        assert policy.process_isolation.enabled is True

    def test_validate_action_blocks_isolated_tool(self):
        iso = ProcessIsolationConfig(
            enabled=True,
            block_local_tools=["bash"],
        )
        policy = ActionPolicyConfig(process_isolation=iso)
        manifest = ActionManifest(
            playbook="test.yml",
            modules=["ansible.builtin.shell"],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is False

    def test_validate_action_allows_when_isolation_disabled(self):
        iso = ProcessIsolationConfig(enabled=False)
        policy = ActionPolicyConfig(process_isolation=iso)
        manifest = ActionManifest(
            playbook="test.yml",
            modules=["ansible.builtin.shell"],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is True

    def test_validate_action_allows_non_blocked_tool(self):
        iso = ProcessIsolationConfig(
            enabled=True,
            block_local_tools=["docker"],
        )
        policy = ActionPolicyConfig(process_isolation=iso)
        manifest = ActionManifest(
            playbook="test.yml",
            modules=["ansible.builtin.debug"],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is True
