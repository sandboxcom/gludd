"""Unit tests for Ansible runner adapter."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from general_ludd.ansible.runner import AnsibleRunnerAdapter


class TestRunnerAdapterRegistry:
    def test_default_registry_contains_noop(self):
        adapter = AnsibleRunnerAdapter()
        assert "noop.yml" in adapter.registry

    def test_registry_maps_to_filesystem_paths(self):
        adapter = AnsibleRunnerAdapter()
        path = adapter.registry["noop.yml"]
        assert path.endswith("playbooks/noop.yml")

    def test_registry_rejects_unregistered_playbook(self):
        adapter = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="not registered"):
            adapter.resolve_playbook("evil.yml")

    def test_resolve_playbook_returns_path(self):
        adapter = AnsibleRunnerAdapter()
        path = adapter.resolve_playbook("noop.yml")
        assert "noop.yml" in path


class TestRunnerPrepareDirs:
    def test_prepare_dirs_creates_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            dirs = adapter.prepare_job_dirs("JOB-100")
            for key in ("root", "env", "project", "inventory", "artifacts"):
                assert key in dirs
                assert os.path.isdir(dirs[key])

    def test_prepare_dirs_root_contains_job_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            dirs = adapter.prepare_job_dirs("JOB-200")
            assert "JOB-200" in dirs["root"]


class TestRunnerWriteVars:
    def test_write_vars_creates_yaml_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            adapter.prepare_job_dirs("JOB-300")
            path = adapter.write_vars(
                "JOB-300",
                job_vars={"job_id": "JOB-300", "target": "db"},
                shared_vars={"env": "staging"},
            )
            assert os.path.isfile(path)
            with open(path) as f:
                content = yaml.safe_load(f)
            assert content["job_vars"]["job_id"] == "JOB-300"
            assert content["shared_vars"]["env"] == "staging"

    def test_write_vars_extravars_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            adapter.prepare_job_dirs("JOB-301")
            path = adapter.write_vars(
                "JOB-301",
                job_vars={"x": 1},
                shared_vars=None,
                filename="extravars",
            )
            assert os.path.basename(path) == "extravars"


class TestRunnerRunPlaybook:
    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_calls_core_runner(self, mock_core_cls: MagicMock) -> None:
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [{}],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            result = adapter.run_playbook(
                playbook_name="noop.yml",
                private_data_dir=tmp,
            )
        mock_core.run_playbook.assert_called_once()
        assert result["status"] == "successful"
        assert result["rc"] == 0

    def test_run_playbook_rejects_unregistered(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            result = adapter.run_playbook(playbook_name="evil.yml")
        assert result["status"] == "failed"
        assert "not registered" in result["error"]

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_captures_events(self, mock_core_cls: MagicMock) -> None:
        events = [
            {"event": "playbook_on_start"},
            {"event": "runner_on_ok", "event_data": {"task": "debug"}},
        ]
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

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            result = adapter.run_playbook(
                playbook_name="noop.yml",
                private_data_dir=tmp,
            )
        assert len(result["events"]) == 2
