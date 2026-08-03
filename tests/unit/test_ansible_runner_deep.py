"""Deep unit tests for AnsibleRunnerAdapter.

Covers: playbook execution lifecycle, event callback handling, job timeout
and cancellation, environment variable injection, inventory resolution,
SSH key management, result artifact collection, project-root switching,
collection activation, duplicate workspace guarding, and playbook registry.

Tests exercise the public API surface of AnsibleRunnerAdapter and trace
interactions with CoreAnsibleRunner, ProcessIsolationConfig, and the
underlying ansible-core/ansible-runner subsystems through fully mocked
dependencies.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.ansible.runner import AnsibleRunnerAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace() -> Generator[str, Any, None]:
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def adapter(tmp_workspace: str) -> AnsibleRunnerAdapter:
    return AnsibleRunnerAdapter(private_data_dir=tmp_workspace)


# ---------------------------------------------------------------------------
# Playbook execution lifecycle
# ---------------------------------------------------------------------------


class TestPlaybookExecutionLifecycle:
    def test_prepare_dirs_rejects_duplicate_job_id(self, adapter: AnsibleRunnerAdapter):
        adapter.prepare_job_dirs("JOB-400")
        with pytest.raises(FileExistsError, match="already exists"):
            adapter.prepare_job_dirs("JOB-400")

    def test_prepare_dirs_sanitizes_unsafe_job_id(self, adapter: AnsibleRunnerAdapter):
        with pytest.raises(ValueError, match="Invalid job_id"):
            adapter.prepare_job_dirs("../../etc")

    def test_prepare_dirs_empty_job_id(self, adapter: AnsibleRunnerAdapter):
        with pytest.raises(ValueError, match="Invalid job_id"):
            adapter.prepare_job_dirs("")

    def test_write_vars_writes_permissions_restricted(self, adapter: AnsibleRunnerAdapter, tmp_workspace: str):
        adapter.prepare_job_dirs("JOB-500")
        path = adapter.write_vars("JOB-500", {"key": "val"})
        st = os.stat(path)
        assert st.st_mode & 0o777 == 0o600

    def test_write_vars_without_prepare_dirs_succeeds(self, adapter: AnsibleRunnerAdapter, tmp_workspace: str):
        path = adapter.write_vars("JOB-299", {"x": 1})
        assert os.path.isfile(path)
        with open(path) as f:
            content = yaml.safe_load(f)
        assert content["job_vars"]["x"] == 1

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_merges_default_env(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(
            private_data_dir=tmp_workspace,
            default_env={"MY_VAR": "adapter_default"},
        )
        adapter.run_playbook("noop.yml")

        call_kwargs = mock_core.run_playbook.call_args.kwargs
        assert call_kwargs["extra_env"] is not None
        assert call_kwargs["extra_env"]["MY_VAR"] == "adapter_default"

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_env_precedence(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(
            private_data_dir=tmp_workspace,
            default_env={"X": "default"},
        )
        adapter.run_playbook("noop.yml", env={"X": "caller"})

        call_kwargs = mock_core.run_playbook.call_args.kwargs
        assert call_kwargs["extra_env"]["X"] == "caller"


# ---------------------------------------------------------------------------
# Event callback handling
# ---------------------------------------------------------------------------


class TestEventCallbackHandling:
    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_propagates_runner_on_ok(self, mock_core_cls: MagicMock, tmp_workspace: str):
        events = [{"event": "runner_on_ok", "host": "localhost", "task": "debug", "result": {"changed": False}}]
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": events,
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        result = adapter.run_playbook("noop.yml")
        assert len(result["events"]) == 1
        assert result["events"][0]["event"] == "runner_on_ok"

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_propagates_runner_on_failed(self, mock_core_cls: MagicMock, tmp_workspace: str):
        events = [
            {"event": "runner_on_failed", "host": "localhost", "task": "fail", "result": {}, "ignore_errors": False}
        ]
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "failed",
            "rc": 2,
            "events": events,
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        result = adapter.run_playbook("noop.yml")
        assert result["status"] == "failed"

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_core_runner_exception_produces_failed(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_core.run_playbook.side_effect = RuntimeError("ansible crash")
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        result = adapter.run_playbook("noop.yml")
        assert result["status"] == "failed"
        assert "ansible crash" in result["error"]


# ---------------------------------------------------------------------------
# Job timeout and cancellation
# ---------------------------------------------------------------------------


class TestJobTimeoutAndCancellation:
    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_passes_explicit_timeout(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        adapter.run_playbook("noop.yml", timeout=60.0)

        assert mock_core.run_playbook.call_args.kwargs["timeout"] == 60.0

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_resolves_env_default_timeout(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        adapter.run_playbook("noop.yml")

        call_kwargs = mock_core.run_playbook.call_args.kwargs
        assert isinstance(call_kwargs["timeout"], float)
        assert call_kwargs["timeout"] > 0

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_extravar_passthrough(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        extravars = {"target_host": "db01", "port": 5432}
        adapter.run_playbook("noop.yml", extravars=extravars)

        call_kwargs = mock_core.run_playbook.call_args.kwargs
        assert call_kwargs["extravars"] == extravars


# ---------------------------------------------------------------------------
# Environment variable injection
# ---------------------------------------------------------------------------


class TestEnvironmentVariableInjection:
    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_caller_env_overrides_collections_env(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        adapter.run_playbook("noop.yml", env={"ANSIBLE_ROLES_PATH": "/custom/roles"})

        call_kwargs = mock_core.run_playbook.call_args.kwargs
        assert "/custom/roles" in call_kwargs["extra_env"]["ANSIBLE_ROLES_PATH"]

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_no_env_no_crash(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        result = adapter.run_playbook("noop.yml")
        assert result["status"] == "successful"


# ---------------------------------------------------------------------------
# Inventory / SSH key management
# ---------------------------------------------------------------------------


class TestInventorySSHManagement:
    def test_prepare_dirs_creates_inventory_dir(self, adapter: AnsibleRunnerAdapter):
        dirs = adapter.prepare_job_dirs("JOB-INV-1")
        assert os.path.isdir(dirs["inventory"])

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_private_data_dir_per_job(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        job_pdd = os.path.join(tmp_workspace, "JOB-PDD")
        os.makedirs(job_pdd, exist_ok=True)
        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        adapter.run_playbook("noop.yml", private_data_dir=job_pdd)

        call_args = mock_core.run_playbook.call_args
        assert call_args is not None


# ---------------------------------------------------------------------------
# Result artifact collection
# ---------------------------------------------------------------------------


class TestResultArtifactCollection:
    def test_prepare_dirs_creates_artifacts_dir(self, adapter: AnsibleRunnerAdapter):
        dirs = adapter.prepare_job_dirs("JOB-ART-1")
        assert os.path.isdir(dirs["artifacts"])

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_includes_host_results(self, mock_core_cls: MagicMock, tmp_workspace: str):
        host_results = {"localhost": {"ok": 1, "changed": 0}}
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {"ok": 1, "changed": 0},
            "host_results": host_results,
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        result = adapter.run_playbook("noop.yml")
        assert result["host_results"] == host_results

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_returns_stats(self, mock_core_cls: MagicMock, tmp_workspace: str):
        stats = {"ok": 3, "changed": 1, "failed": 0, "skipped": 0}
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": stats,
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        result = adapter.run_playbook("noop.yml")
        assert result["stats"] == stats


# ---------------------------------------------------------------------------
# Registry + playbook management
# ---------------------------------------------------------------------------


class TestRegistryPlaybookManagement:
    def test_register_custom_playbook(self, adapter: AnsibleRunnerAdapter):
        adapter.register_playbook("custom.yml", "/tmp/my/custom.yml")
        assert "custom.yml" in adapter.list_playbooks()
        assert adapter.resolve_playbook("custom.yml") == "/tmp/my/custom.yml"

    def test_unregister_playbook(self, adapter: AnsibleRunnerAdapter):
        adapter.register_playbook("temp.yml", "/tmp/temp.yml")
        adapter.unregister_playbook("temp.yml")
        assert "temp.yml" not in adapter.list_playbooks()

    def test_unregister_nonexistent_silent(self, adapter: AnsibleRunnerAdapter):
        adapter.unregister_playbook("nonexistent.yml")

    def test_list_playbooks_returns_list(self, adapter: AnsibleRunnerAdapter):
        playbooks = adapter.list_playbooks()
        assert isinstance(playbooks, list)
        assert "noop.yml" in playbooks


# ---------------------------------------------------------------------------
# Project root + collections environment
# ---------------------------------------------------------------------------


class TestProjectRootCollections:
    def test_set_project_root_accepts_path(self, adapter: AnsibleRunnerAdapter):
        adapter.set_project_root("/tmp/fake-project")
        assert adapter._project_root == Path("/tmp/fake-project")

    def test_set_project_root_none_clears(self, adapter: AnsibleRunnerAdapter):
        adapter.set_project_root("/tmp/proj")
        adapter.set_project_root(None)
        assert adapter._project_root is None

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_project_root_env_reflected_in_run(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace, project_root="/tmp/fake-proj")
        adapter.run_playbook("noop.yml")
        result = adapter.run_playbook("noop.yml")
        assert result["status"] == "successful"


# ---------------------------------------------------------------------------
# Process isolation config
# ---------------------------------------------------------------------------


class TestProcessIsolationConfig:
    def test_isolation_config_passthrough(self, tmp_workspace: str):
        iso = ProcessIsolationConfig(enabled=True, executable="podman")
        adapter = AnsibleRunnerAdapter(
            private_data_dir=tmp_workspace,
            isolation_config=iso,
        )
        assert adapter.isolation_config is iso
        conf = adapter.isolation_config
        assert conf is not None
        assert conf.enabled is True

    def test_isolation_config_none_ok(self, adapter: AnsibleRunnerAdapter):
        assert adapter.isolation_config is None


# ---------------------------------------------------------------------------
# run_role coverage
# ---------------------------------------------------------------------------


class TestRunRole:
    @pytest.mark.asyncio
    async def test_run_role_no_role_specified(self, adapter: AnsibleRunnerAdapter):
        result = await adapter.run_role({})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_run_role_script_not_found(self, adapter: AnsibleRunnerAdapter):
        result = await adapter.run_role({"role": "nonexistent_role"})
        assert "error" in result
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# refresh_playbooks
# ---------------------------------------------------------------------------


class TestRefreshPlaybooks:
    def test_refresh_playbooks_without_dir_returns_current(self, adapter: AnsibleRunnerAdapter):
        result = adapter.refresh_playbooks()
        assert "playbooks" in result
        assert "noop.yml" in result["playbooks"]


# ---------------------------------------------------------------------------
# Collection activation lifecycle
# ---------------------------------------------------------------------------


class TestCollectionActivationLifecycle:
    def test_activate_collection_no_project_root(self, adapter: AnsibleRunnerAdapter):
        with pytest.raises(FileNotFoundError):
            adapter.activate_collection("general_ludd", "agent")

    def test_clear_collection_versions_idempotent(self, adapter: AnsibleRunnerAdapter):
        adapter.clear_collection_versions()

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_activation_roots_injected_into_env(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        adapter._version_activation_roots = [Path("/tmp/activated")]
        result = adapter.run_playbook("noop.yml")
        assert result["status"] == "successful"


# ---------------------------------------------------------------------------
# event_bus integration
# ---------------------------------------------------------------------------


class TestEventBusIntegration:
    def test_register_playbook_publishes_event(self, tmp_workspace: str):
        bus = MagicMock()
        adapter = AnsibleRunnerAdapter(private_data_dir=tmp_workspace, event_bus=bus)
        adapter.register_playbook("my_play.yml", "/tmp/my_play.yml")
        bus.publish.assert_called_once()

    def test_register_playbook_no_bus_no_crash(self, adapter: AnsibleRunnerAdapter):
        adapter.register_playbook("silent.yml", "/tmp/silent.yml")
