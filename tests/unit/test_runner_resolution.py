from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from general_ludd.ansible.runner import AnsibleRunnerAdapter


class TestRunnerResolution:
    def test_runner_discovers_playbooks_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook_a = Path(tmpdir) / "a.yml"
            playbook_b = Path(tmpdir) / "b.yml"
            playbook_a.write_text("- hosts: localhost\n  tasks: []\n")
            playbook_b.write_text("- hosts: localhost\n  tasks: []\n")

            runner = AnsibleRunnerAdapter(playbooks_dir=tmpdir)
            playbooks = runner.list_playbooks()
            assert "a.yml" in playbooks
            assert "b.yml" in playbooks

    def test_unknown_playbook_returns_failed_result_not_raise(self):
        runner = AnsibleRunnerAdapter(playbooks_dir="/nonexistent")
        result = runner.run_playbook("nonexistent.yml")
        assert result["status"] == "failed"
        assert result["rc"] != 0
        assert "not registered" in result["error"]

    def test_noop_registered_by_default(self):
        runner = AnsibleRunnerAdapter()
        playbooks = runner.list_playbooks()
        assert "noop.yml" in playbooks

    def test_run_playbook_catches_core_runner_failure(self):
        runner = AnsibleRunnerAdapter()
        result = runner.run_playbook("noop.yml")
        assert "status" in result

    def test_resolve_playbook_raises_for_unknown(self):
        runner = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="not registered"):
            runner.resolve_playbook("nonexistent.yml")


class TestRunnerConstruction:
    def test_runner_constructed_with_playbooks_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "x.yml"
            pf.write_text("- hosts: localhost\n  tasks: []\n")
            runner = AnsibleRunnerAdapter(playbooks_dir=tmpdir)
            assert "x.yml" in runner.list_playbooks()

    def test_runner_constructed_without_playbooks_dir(self):
        runner = AnsibleRunnerAdapter()
        assert "noop.yml" in runner.list_playbooks()
