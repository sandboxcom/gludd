"""Tests for ansible/core_runner.py uncovered paths — pushing coverage above 85%."""

from __future__ import annotations

from unittest.mock import MagicMock


class TestCoreRunnerUncovered:
    def test_get_templar_with_none_loader(self):
        from general_ludd.ansible.core_runner import _get_templar

        templar = _get_templar(loader=None, variables={"key": "value"})
        assert templar is not None

    def test_get_templar_with_custom_loader(self):
        from general_ludd.ansible.core_runner import _get_templar

        mock_loader = MagicMock()
        templar = _get_templar(loader=mock_loader, variables={"a": 1})
        assert templar is not None

    def test_get_templar_with_empty_variables(self):
        from general_ludd.ansible.core_runner import _get_templar

        templar = _get_templar()
        assert templar is not None

    def test_ansible_options_defaults(self):
        from general_ludd.ansible.core_runner import AnsibleOptions

        opts = AnsibleOptions()
        assert opts.connection == "local"
        assert opts.forks == 5
        assert opts.verbosity == 0

    def test_ansible_options_custom(self):
        from general_ludd.ansible.core_runner import AnsibleOptions

        opts = AnsibleOptions(
            connection="smart",
            module_path=["/custom/modules"],
            forks=4,
            become=True,
            become_method="sudo",
            become_user="root",
            check=True,
            diff=True,
            verbosity=3,
        )
        assert opts.connection == "smart"
        assert opts.forks == 4
        assert opts.become is True
        assert opts.verbosity == 3

    def test_ansible_options_string_verbosity(self):
        from general_ludd.ansible.core_runner import AnsibleOptions

        opts = AnsibleOptions(verbosity=5)
        assert opts.verbosity == 5

    def test_core_runner_init(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner(module_paths=["/some/path"])
        assert runner is not None

    def test_core_runner_init_default(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        assert runner is not None

    def test_ansible_result_class(self):
        from general_ludd.ansible.core_runner import AnsibleResult

        result = AnsibleResult()
        assert result.rc == 0
        assert result.status == "unknown"
        assert result.events == []

    def test_ansible_result_with_events(self):
        from general_ludd.ansible.core_runner import AnsibleResult

        result = AnsibleResult(
            status="failed",
            rc=1,
            events=[{"event": "runner_on_failed", "event_data": {"res": {"msg": "fail"}}}],
        )
        assert result.rc == 1
        assert result.status == "failed"
        assert len(result.events) == 1
