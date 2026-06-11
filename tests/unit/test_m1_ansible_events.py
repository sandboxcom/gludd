"""Tests for M1: Ansible events real — callback plugin + real stats.

Verifies that CoreAnsibleRunner._execute_with_core:
1. Registers a callback plugin that collects real events
2. Returns real run statistics (ok/changed/failed/skipped) not host vars
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class MockStats:
    def __init__(self, processed=None):
        self.processed = processed or {"localhost": {"ok": 1, "changed": 0, "failures": 0, "skipped": 0}}


class MockTQM:
    def __init__(self):
        self._stats = MockStats()
        self._callback_plugins = []

    def send_callback(self, event_name, *args, **kwargs):
        pass

    def load_callbacks(self):
        pass

    def cleanup(self):
        pass


class MockPlaybookExecutor:
    def __init__(self, playbooks, inventory, variable_manager, loader, passwords):
        self._tqm = MockTQM()
        self.playbooks = playbooks

    def run(self):
        for cb in self._tqm._callback_plugins:
            if hasattr(cb, "v2_playbook_on_start"):
                cb.v2_playbook_on_start(self.playbooks[0])
            if hasattr(cb, "v2_runner_on_ok"):
                mock_result = MagicMock()
                mock_result._host = MagicMock(__str__=lambda s: "localhost")
                mock_result._task = MagicMock(__str__=lambda s: "test task")
                mock_result._result = {"changed": False}
                cb.v2_runner_on_ok(mock_result)
            if hasattr(cb, "v2_playbook_on_stats"):
                cb.v2_playbook_on_stats(self._tqm._stats)


@pytest.fixture
def mock_ansible_core():
    with patch(
        "ansible.executor.playbook_executor.PlaybookExecutor",
        side_effect=MockPlaybookExecutor,
    ), patch("ansible.parsing.dataloader.DataLoader"), patch("ansible.inventory.manager.InventoryManager") as mock_inv:
        mock_inv.return_value.get_hosts.return_value = [MagicMock()]
        mock_inv.return_value.list_hosts.return_value = ["localhost"]
        with patch("ansible.vars.manager.VariableManager") as mock_var:
            mock_var.return_value.get_vars.return_value = {}
            yield


class TestM1CallbackRegistered:
    def test_events_are_collected_not_empty(self, mock_ansible_core):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        result = runner._execute_with_core(playbook_path="/tmp/test.yml")
        assert isinstance(result.events, list)
        assert len(result.events) > 0

    def test_stats_are_run_stats_not_host_vars(self, mock_ansible_core):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        result = runner._execute_with_core(playbook_path="/tmp/test.yml")
        assert "ok" in result.stats
        assert "changed" in result.stats
        assert result.stats.get("ok") == 1

    def test_failure_stats_captured(self, mock_ansible_core):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        result = runner._execute_with_core(playbook_path="/tmp/test.yml")
        assert "failures" in result.stats
        assert result.stats.get("failures") == 0


class TestM1CallbackGranularity:
    def test_multiple_task_events_collected(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with patch.object(runner, "_execute_with_core") as mock_exec:
            mock_result = MagicMock()
            mock_result.events = [
                {"event": "playbook_on_start", "playbook": "/tmp/test.yml"},
                {"event": "runner_on_ok", "task": "Task 1", "host": "localhost"},
                {"event": "runner_on_ok", "task": "Task 2", "host": "localhost"},
                {"event": "playbook_on_stats", "stats": {"ok": 2, "changed": 0}},
            ]
            mock_result.stats = {"ok": 2, "changed": 0, "failures": 0, "skipped": 0}
            mock_result.status = "successful"
            mock_result.rc = 0
            mock_exec.return_value = mock_result
            result = runner.run_playbook("/tmp/test.yml", inventory=["localhost,"])

        assert len(result.events) == 4
        assert result.events[0]["event"] == "playbook_on_start"
        assert result.events[1]["event"] == "runner_on_ok"

    def test_stat_keys_are_standard_ansible_keys(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with patch.object(runner, "_execute_with_core") as mock_exec:
            mock_result = MagicMock()
            mock_result.events = []
            mock_result.stats = {
                "ok": 5, "changed": 2, "failures": 0, "skipped": 1,
                "unreachable": 0, "rescued": 0, "ignored": 0,
            }
            mock_result.status = "successful"
            mock_result.rc = 0
            mock_exec.return_value = mock_result
            result = runner.run_playbook("/tmp/test.yml", inventory=["localhost,"])

        for key in ("ok", "changed", "failures"):
            assert key in result.stats, f"Missing stat key: {key}"
        assert result.stats["ok"] == 5
        assert result.stats["changed"] == 2

    def test_events_not_empty_after_run(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with patch.object(runner, "_execute_with_core") as mock_exec:
            mock_result = MagicMock()
            mock_result.events = [
                {"event": "playbook_on_start"},
                {"event": "runner_on_ok", "host": "localhost"},
            ]
            mock_result.stats = {"ok": 1, "changed": 0, "failures": 0, "skipped": 0}
            mock_result.status = "successful"
            mock_result.rc = 0
            mock_exec.return_value = mock_result
            result = runner.run_playbook("/tmp/test.yml", inventory=["localhost,"])

        assert result.events is not None
        assert len(result.events) == 2
        assert result.events != []


class TestM1RunPlaybookIntegration:
    def test_run_playbook_returns_stats_key(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with patch.object(runner, "_execute_with_core") as mock_exec:
            mock_result = MagicMock()
            mock_result.events = [{"event": "playbook_on_start"}]
            mock_result.stats = {"ok": 1, "changed": 0, "failures": 0, "skipped": 0}
            mock_result.status = "successful"
            mock_result.rc = 0
            mock_exec.return_value = mock_result
            result = runner.run_playbook("/tmp/test.yml", inventory=["localhost,"])

        assert result.stats is not None
        assert isinstance(result.stats, dict)
