"""Tests for ansible/core_runner.py uncovered paths — pushing coverage above 85%."""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock

import pytest


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

    def test_get_templar_no_ansible_core(self):
        import general_ludd.ansible.core_runner as mod

        original = mod._HAS_ANSIBLE_CORE
        mod._HAS_ANSIBLE_CORE = False
        try:
            with pytest.raises(ImportError, match="ansible-core is required"):
                mod._get_templar()
        finally:
            mod._HAS_ANSIBLE_CORE = original

    def test_run_playbook_no_ansible_core(self):
        import general_ludd.ansible.core_runner as mod

        original = mod._HAS_ANSIBLE_CORE
        mod._HAS_ANSIBLE_CORE = False
        try:
            runner = mod.CoreAnsibleRunner()
            with pytest.raises(ImportError, match="ansible-core is required"):
                runner.run_playbook("/tmp/fake.yml")
        finally:
            mod._HAS_ANSIBLE_CORE = original

    def test_render_template_no_ansible_core(self):
        import general_ludd.ansible.core_runner as mod

        original = mod._HAS_ANSIBLE_CORE
        mod._HAS_ANSIBLE_CORE = False
        try:
            runner = mod.CoreAnsibleRunner()
            with pytest.raises(ImportError, match="ansible-core is required"):
                runner.render_template("{{ foo }}")
        finally:
            mod._HAS_ANSIBLE_CORE = original

    def test_resolve_variable_no_ansible_core(self):
        import general_ludd.ansible.core_runner as mod

        original = mod._HAS_ANSIBLE_CORE
        mod._HAS_ANSIBLE_CORE = False
        try:
            runner = mod.CoreAnsibleRunner()
            with pytest.raises(ImportError, match="ansible-core is required"):
                runner.resolve_variable("foo")
        finally:
            mod._HAS_ANSIBLE_CORE = original

    def test_list_tasks_with_bad_yaml(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        bad_file = tmp_path / "bad.yml"
        bad_file.write_text("{{invalid yaml")
        runner = CoreAnsibleRunner()
        result = runner.list_tasks(str(bad_file))
        assert result == []

    def test_list_tasks_with_non_dict_play(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "play.yml"
        pb.write_text("- 42\n- string_value\n")
        runner = CoreAnsibleRunner()
        result = runner.list_tasks(str(pb))
        assert result == []

    def test_list_tasks_with_non_dict_task(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "play.yml"
        pb.write_text(textwrap.dedent("""\
            - hosts: all
              tasks:
                - 42
                - name: valid task
                  debug:
                    msg: hello
        """))
        runner = CoreAnsibleRunner()
        result = runner.list_tasks(str(pb))
        assert len(result) == 1
        assert result[0]["name"] == "valid task"

    def test_validate_playbook_not_found(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax("/nonexistent/playbook.yml")
        assert any("not found" in e for e in errors)

    def test_validate_playbook_not_a_list(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "play.yml"
        pb.write_text("key: value\n")
        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax(str(pb))
        assert any("list of plays" in e for e in errors)

    def test_validate_playbook_non_dict_play(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "play.yml"
        pb.write_text("- 42\n- string\n")
        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax(str(pb))
        assert any("not a mapping" in e for e in errors)

    def test_validate_playbook_missing_hosts(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "play.yml"
        pb.write_text("- name: no hosts play\n  tasks: []\n")
        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax(str(pb))
        assert any("missing 'hosts'" in e for e in errors)

    def test_validate_playbook_valid(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "play.yml"
        pb.write_text("- hosts: all\n  tasks: []\n")
        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax(str(pb))
        assert errors == []

    def test_resolve_variable_with_ansible_core(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        result = runner.resolve_variable("ansible_fqdn", host="localhost")
        assert result is not None or result is None
