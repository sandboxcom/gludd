"""Unit tests for CoreAnsibleRunner and AnsibleTemplater (ansible-core native library)."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.ansible.runner import AnsibleRunnerAdapter


class TestAnsibleOptions:
    def test_default_options(self):
        from general_ludd.ansible.core_runner import AnsibleOptions

        opts = AnsibleOptions()
        assert opts.inventory == ["localhost,"]
        assert opts.verbosity == 0
        assert opts.check is False
        assert opts.diff is False
        assert opts.forks == 5
        assert opts.become is False
        assert opts.connection == "local"
        assert opts.module_path == []
        assert opts.tags == ["all"]
        assert opts.skip_tags == []

    def test_custom_options(self):
        from general_ludd.ansible.core_runner import AnsibleOptions

        opts = AnsibleOptions(
            inventory=["web1", "web2"],
            extravars={"env": "prod"},
            verbosity=2,
            check=True,
            diff=True,
            forks=10,
            become=True,
            become_method="sudo",
            become_user="root",
            connection="ssh",
            tags=["deploy"],
            skip_tags=["debug"],
        )
        assert opts.inventory == ["web1", "web2"]
        assert opts.extravars == {"env": "prod"}
        assert opts.verbosity == 2
        assert opts.check is True
        assert opts.diff is True
        assert opts.forks == 10
        assert opts.become is True
        assert opts.become_method == "sudo"
        assert opts.become_user == "root"
        assert opts.connection == "ssh"
        assert opts.tags == ["deploy"]
        assert opts.skip_tags == ["debug"]


class TestAnsibleResult:
    def test_default_result(self):
        from general_ludd.ansible.core_runner import AnsibleResult

        result = AnsibleResult()
        assert result.status == "unknown"
        assert result.rc == 0
        assert result.stats == {}
        assert result.events == []
        assert result.host_results == {}

    def test_successful_result(self):
        from general_ludd.ansible.core_runner import AnsibleResult

        result = AnsibleResult(
            status="successful",
            rc=0,
            stats={"localhost": {"ok": 1, "changed": 0}},
            events=[{"event": "runner_on_ok"}],
        )
        assert result.status == "successful"
        assert result.stats["localhost"]["ok"] == 1
        assert len(result.events) == 1


class TestCoreAnsibleRunnerInit:
    def test_default_init(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        assert runner._module_paths == []
        assert runner._callback_plugins == []

    def test_init_with_module_paths(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner(module_paths=["/custom/modules"])
        assert runner._module_paths == ["/custom/modules"]

    def test_init_with_callback_plugins(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner(callback_plugins=["/plugins/callback"])
        assert runner._callback_plugins == ["/plugins/callback"]

    def test_accepts_process_isolation_config(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        iso = ProcessIsolationConfig(enabled=True, executable="bwrap")
        runner = CoreAnsibleRunner(process_isolation=iso)
        assert runner._process_isolation is iso


class TestCoreAnsibleRunnerRenderTemplate:
    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_render_simple_variable(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        mock_templer = MagicMock()
        mock_templer.template.return_value = "hello world"
        with patch("general_ludd.ansible.core_runner._get_templar", return_value=mock_templer):
            result = runner.render_template("{{ msg }}", variables={"msg": "hello world"})
        assert result == "hello world"
        mock_templer.template.assert_called_once_with("{{ msg }}")

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_render_with_filter(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        mock_templer = MagicMock()
        mock_templer.template.return_value = "HELLO"
        with patch("general_ludd.ansible.core_runner._get_templar", return_value=mock_templer):
            result = runner.render_template("{{ name | upper }}", variables={"name": "hello"})
        assert result == "HELLO"

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", False)
    def test_render_template_fallback_without_ansible_core(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with pytest.raises(ImportError, match="ansible-core"):
            runner.render_template("{{ msg }}", variables={"msg": "hello"})


class TestCoreAnsibleRunnerListTasks:
    def test_list_tasks_from_playbook(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        playbook = [
            {
                "name": "Test play",
                "hosts": "localhost",
                "tasks": [
                    {"name": "First task", "ansible.builtin.debug": {"msg": "hi"}},
                    {"name": "Second task", "ansible.builtin.copy": {"src": "a", "dest": "b"}},
                ],
            }
        ]
        runner = CoreAnsibleRunner()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            tasks = runner.list_tasks(path)
            assert len(tasks) == 2
            assert tasks[0]["name"] == "First task"
            assert tasks[0]["module"] == "ansible.builtin.debug"
            assert tasks[1]["name"] == "Second task"
            assert tasks[1]["module"] == "ansible.builtin.copy"
        finally:
            os.unlink(path)

    def test_list_tasks_empty_playbook(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump([], f)
            path = f.name
        try:
            tasks = runner.list_tasks(path)
            assert tasks == []
        finally:
            os.unlink(path)

    def test_list_tasks_multiple_plays(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        playbook = [
            {
                "name": "Play 1",
                "hosts": "localhost",
                "tasks": [
                    {"name": "Task A", "debug": {"msg": "a"}},
                ],
            },
            {
                "name": "Play 2",
                "hosts": "all",
                "tasks": [
                    {"name": "Task B", "shell": "echo hi"},
                ],
            },
        ]
        runner = CoreAnsibleRunner()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            tasks = runner.list_tasks(path)
            assert len(tasks) == 2
            assert tasks[0]["hosts"] == "localhost"
            assert tasks[1]["hosts"] == "all"
        finally:
            os.unlink(path)


class TestCoreAnsibleRunnerValidatePlaybookSyntax:
    def test_validate_valid_playbook(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        playbook = [
            {
                "name": "Valid play",
                "hosts": "localhost",
                "tasks": [
                    {"name": "Debug", "debug": {"msg": "ok"}},
                ],
            }
        ]
        runner = CoreAnsibleRunner()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            errors = runner.validate_playbook_syntax(path)
            assert errors == []
        finally:
            os.unlink(path)

    def test_validate_invalid_yaml(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("not: valid: yaml: [\n")
            path = f.name
        try:
            errors = runner.validate_playbook_syntax(path)
            assert len(errors) > 0
        finally:
            os.unlink(path)

    def test_validate_missing_file(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax("/nonexistent/path/playbook.yml")
        assert len(errors) > 0

    def test_validate_playbook_missing_hosts(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        playbook = [
            {
                "name": "No hosts",
                "tasks": [
                    {"debug": {"msg": "ok"}},
                ],
            }
        ]
        runner = CoreAnsibleRunner()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            errors = runner.validate_playbook_syntax(path)
            assert len(errors) > 0
        finally:
            os.unlink(path)


class TestCoreAnsibleRunnerRunPlaybook:
    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_run_playbook_delegates_to_executor(self):
        from general_ludd.ansible.core_runner import AnsibleResult, CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        mock_result = AnsibleResult(status="successful", rc=0)
        with patch.object(runner, "_execute_with_core", return_value=mock_result) as mock_exec:
            result = runner.run_playbook("/path/to/playbook.yml")
        assert result.status == "successful"
        assert result.rc == 0
        mock_exec.assert_called_once()

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", False)
    def test_run_playbook_fallback_without_ansible_core(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with pytest.raises(ImportError, match="ansible-core"):
            runner.run_playbook("/path/to/playbook.yml")


class TestCoreAnsibleRunnerResolveVariable:
    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_resolve_variable_delegates(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with patch.object(runner, "_resolve_with_variable_manager", return_value="resolved_val") as mock_resolve:
            result = runner.resolve_variable("my_var", host="web1")
        assert result == "resolved_val"
        mock_resolve.assert_called_once()


class TestAnsibleTemplater:
    def test_templater_init_default(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        templater = AnsibleTemplater()
        assert templater._extra_vars == {}

    def test_templater_init_with_vars(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        templater = AnsibleTemplater(extra_vars={"env": "prod"})
        assert templater._extra_vars == {"env": "prod"}

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_templater_render_delegates_to_core_runner(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        mock_core = MagicMock()
        mock_core.render_template.return_value = "test"
        with patch("general_ludd.ansible.templating.CoreAnsibleRunner", return_value=mock_core):
            templater = AnsibleTemplater(extra_vars={"name": "test"})
            result = templater.render("{{ name }}")
        assert result == "test"

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_templater_render_with_kwargs(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        mock_core = MagicMock()
        mock_core.render_template.return_value = "hello"
        with patch("general_ludd.ansible.templating.CoreAnsibleRunner", return_value=mock_core):
            templater = AnsibleTemplater()
            result = templater.render("{{ msg }}", msg="hello")
        assert result == "hello"

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_templater_resolve_fact(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        mock_core = MagicMock()
        mock_core.resolve_variable.return_value = "192.168.1.1"
        with patch("general_ludd.ansible.templating.CoreAnsibleRunner", return_value=mock_core):
            templater = AnsibleTemplater()
            result = templater.resolve_fact("ansible_default_ipv4.address", host="web1")
        assert result == "192.168.1.1"


class TestRunnerAdapterUsesCoreRunner:
    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_adapter_delegates_run_to_core_runner(self, mock_core_cls: MagicMock):
        mock_core_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.status = "successful"
        mock_result.rc = 0
        mock_result.events = []
        mock_result.stats = {}
        mock_result.host_results = {}
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core_instance.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core_instance

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            result = adapter.run_playbook(
                playbook_name="noop.yml",
                private_data_dir=tmp,
            )
        mock_core_instance.run_playbook.assert_called_once()
        assert result["status"] == "successful"

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_adapter_passes_extravars_to_core_runner(self, mock_core_cls: MagicMock):
        mock_core_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.status = "successful"
        mock_result.rc = 0
        mock_result.events = []
        mock_result.stats = {}
        mock_result.host_results = {}
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core_instance.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core_instance

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            adapter.run_playbook(
                playbook_name="noop.yml",
                private_data_dir=tmp,
                extravars={"env": "staging"},
            )
        call_kwargs = mock_core_instance.run_playbook.call_args
        assert call_kwargs[1]["extravars"] == {"env": "staging"} or \
               (len(call_kwargs[0]) > 1 and call_kwargs[1].get("extravars") == {"env": "staging"})

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_adapter_handles_core_runner_failure(self, mock_core_cls: MagicMock):
        mock_core_instance = MagicMock()
        mock_core_instance.run_playbook.side_effect = RuntimeError("executor crashed")
        mock_core_cls.return_value = mock_core_instance

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            result = adapter.run_playbook(
                playbook_name="noop.yml",
                private_data_dir=tmp,
            )
        assert result["status"] == "failed"
        assert result["rc"] == 1

    def test_adapter_registry_unchanged(self):
        adapter = AnsibleRunnerAdapter()
        assert "noop.yml" in adapter.registry
        assert adapter.resolve_playbook("noop.yml").endswith("noop.yml")

    def test_adapter_prepare_dirs_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            dirs = adapter.prepare_job_dirs("JOB-TEST")
            assert os.path.isdir(dirs["root"])

    def test_adapter_write_vars_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            adapter.prepare_job_dirs("JOB-WV")
            path = adapter.write_vars("JOB-WV", job_vars={"x": 1})
            assert os.path.isfile(path)


class TestProcessIsolationWithCoreRunner:
    def test_isolation_config_produces_core_options(self):
        iso = ProcessIsolationConfig(
            enabled=True,
            executable="bwrap",
            hide_paths=["/secret"],
        )
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner(process_isolation=iso)
        assert runner._process_isolation is iso
        assert runner._process_isolation.enabled is True

    def test_isolation_to_core_kwargs(self):
        iso = ProcessIsolationConfig(
            enabled=True,
            executable="bwrap",
            hide_paths=["/secret"],
        )
        kwargs = iso.to_core_runner_kwargs()
        assert kwargs["connection"] == "local"
        assert "/secret" in kwargs["hide_paths"]
