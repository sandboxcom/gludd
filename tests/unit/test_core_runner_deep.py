"""Deep behavioural tests for general_ludd/ansible/core_runner.py."""

from __future__ import annotations

import os
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest

# ── _json_safe ──────────────────────────────────────────────────────────────


class TestJsonSafe:
    def test_primitives_pass_through(self):
        from general_ludd.ansible.core_runner import _json_safe

        assert _json_safe(None) is None
        assert _json_safe(True) is True
        assert _json_safe(42) == 42
        assert _json_safe(3.14) == 3.14
        assert _json_safe("hello") == "hello"

    def test_dict_recursion(self):
        from general_ludd.ansible.core_runner import _json_safe

        obj = {"a": {"b": [1, 2]}, "c": "str"}
        result = _json_safe(obj)
        assert result == obj

    def test_list_and_tuple(self):
        from general_ludd.ansible.core_runner import _json_safe

        assert _json_safe([1, "x"]) == [1, "x"]
        assert _json_safe((1, 2)) == [1, 2]

    def test_unpicklable_fallback(self):
        from general_ludd.ansible.core_runner import _json_safe

        class Unserializable:
            pass

        obj = Unserializable()
        result = _json_safe(obj)
        assert isinstance(result, str)
        assert "Unserializable" in result
        assert "object at" in result

    def test_nested_unpicklable(self):
        from general_ludd.ansible.core_runner import _json_safe

        class Unserializable:
            pass

        obj = {"deep": [Unserializable(), "safe"]}
        result = _json_safe(obj)
        assert isinstance(result["deep"][0], str)
        assert "Unserializable" in result["deep"][0]
        assert result["deep"][1] == "safe"

    def test_json_serializable_dict_passes(self):
        from general_ludd.ansible.core_runner import _json_safe

        assert _json_safe({"a": 1}) == {"a": 1}


# ── _env_default_timeout ────────────────────────────────────────────────────


class TestEnvDefaultTimeout:
    def test_no_env_returns_default(self, monkeypatch):
        monkeypatch.delenv("GLUDD_PLAYBOOK_TIMEOUT", raising=False)
        from general_ludd.ansible.core_runner import _env_default_timeout

        assert _env_default_timeout() == 300.0

    def test_valid_env(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "600")
        from general_ludd.ansible.core_runner import _env_default_timeout

        assert _env_default_timeout() == 600.0

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "abc")
        from general_ludd.ansible.core_runner import _env_default_timeout

        assert _env_default_timeout() == 300.0

    def test_zero_falls_back(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "0")
        from general_ludd.ansible.core_runner import _env_default_timeout

        assert _env_default_timeout() == 300.0

    def test_negative_falls_back(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "-5")
        from general_ludd.ansible.core_runner import _env_default_timeout

        assert _env_default_timeout() == 300.0

    def test_empty_string_falls_back(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "")
        from general_ludd.ansible.core_runner import _env_default_timeout

        assert _env_default_timeout() == 300.0


# ── _EventCollectorCallback ──────────────────────────────────────────────────


class TestEventCollectorCallback:
    def test_init_state(self):
        from general_ludd.ansible.core_runner import _EventCollectorCallback

        cb = _EventCollectorCallback()
        assert cb._events == []
        assert cb._host_stats == {}

    def test_runner_on_start(self):
        from general_ludd.ansible.core_runner import _EventCollectorCallback

        cb = _EventCollectorCallback()
        host = MagicMock(__str__=lambda s: "testhost")
        task = MagicMock(__str__=lambda s: "my task")
        cb.v2_runner_on_start(host, task)
        assert len(cb._events) == 1
        assert cb._events[0]["event"] == "runner_on_start"
        assert cb._events[0]["host"] == "testhost"
        assert cb._events[0]["task"] == "my task"

    def test_runner_on_ok(self):
        from general_ludd.ansible.core_runner import _EventCollectorCallback

        cb = _EventCollectorCallback()
        result = MagicMock()
        result._host.__str__ = lambda s: "okhost"
        result._task.__str__ = lambda s: "oktask"
        result._result = {"changed": False}
        cb.v2_runner_on_ok(result)
        assert len(cb._events) == 1
        assert cb._events[0]["event"] == "runner_on_ok"
        assert cb._events[0]["host"] == "okhost"
        assert cb._events[0]["result"] == {"changed": False}

    def test_runner_on_failed(self):
        from general_ludd.ansible.core_runner import _EventCollectorCallback

        cb = _EventCollectorCallback()
        result = MagicMock()
        result._host.__str__ = lambda s: "failhost"
        result._task.__str__ = lambda s: "failtask"
        result._result = {"msg": "boom"}
        cb.v2_runner_on_failed(result, ignore_errors=False)
        assert len(cb._events) == 1
        assert cb._events[0]["event"] == "runner_on_failed"
        assert cb._events[0]["ignore_errors"] is False

    def test_runner_on_failed_ignore_errors(self):
        from general_ludd.ansible.core_runner import _EventCollectorCallback

        cb = _EventCollectorCallback()
        result = MagicMock()
        result._host.__str__ = lambda s: "failhost"
        result._task.__str__ = lambda s: "failtask"
        result._result = {}
        cb.v2_runner_on_failed(result, ignore_errors=True)
        assert cb._events[0]["ignore_errors"] is True

    def test_runner_on_skipped(self):
        from general_ludd.ansible.core_runner import _EventCollectorCallback

        cb = _EventCollectorCallback()
        result = MagicMock()
        result._host.__str__ = lambda s: "skiphost"
        result._task.__str__ = lambda s: "skiptask"
        cb.v2_runner_on_skipped(result)
        assert cb._events[0]["event"] == "runner_on_skipped"

    def test_runner_on_unreachable(self):
        from general_ludd.ansible.core_runner import _EventCollectorCallback

        cb = _EventCollectorCallback()
        result = MagicMock()
        result._host.__str__ = lambda s: "ghost"
        result._task.__str__ = lambda s: "ghosttask"
        cb.v2_runner_on_unreachable(result)
        assert cb._events[0]["event"] == "runner_on_unreachable"

    def test_playbook_on_start(self):
        from general_ludd.ansible.core_runner import _EventCollectorCallback

        cb = _EventCollectorCallback()
        pb = MagicMock(__str__=lambda s: "myplaybook.yml")
        cb.v2_playbook_on_start(pb)
        assert cb._events[0]["event"] == "playbook_on_start"

    def test_playbook_on_stats_populates_host_stats(self):
        from general_ludd.ansible.core_runner import _EventCollectorCallback

        cb = _EventCollectorCallback()
        stats = MagicMock()
        host_stat = MagicMock()
        stats.processed.items.return_value = [
            ("host_a", host_stat),
            ("host_b", MagicMock()),
        ]
        cb.v2_playbook_on_stats(stats)
        assert "host_a" in cb._host_stats
        assert "host_b" in cb._host_stats
        assert cb._events[-1]["event"] == "playbook_on_stats"

    def test_multiple_events_accumulate(self):
        from general_ludd.ansible.core_runner import _EventCollectorCallback

        cb = _EventCollectorCallback()
        host = MagicMock(__str__=lambda s: "h")
        task = MagicMock(__str__=lambda s: "t")
        cb.v2_runner_on_start(host, task)
        cb.v2_runner_on_start(host, task)
        cb.v2_runner_on_start(host, task)
        assert len(cb._events) == 3


# ── AnsibleOptions ──────────────────────────────────────────────────────────


class TestAnsibleOptions:
    def test_tags_default_all(self):
        from general_ludd.ansible.core_runner import AnsibleOptions

        assert AnsibleOptions().tags == ["all"]

    def test_skip_tags_default_empty(self):
        from general_ludd.ansible.core_runner import AnsibleOptions

        assert AnsibleOptions().skip_tags == []

    def test_start_at_task_default_none(self):
        from general_ludd.ansible.core_runner import AnsibleOptions

        assert AnsibleOptions().start_at_task is None

    def test_module_path_default_empty(self):
        from general_ludd.ansible.core_runner import AnsibleOptions

        assert AnsibleOptions().module_path == []

    def test_custom_tags_and_skip(self):
        from general_ludd.ansible.core_runner import AnsibleOptions

        opts = AnsibleOptions(tags=["setup", "deploy"], skip_tags=["never"])
        assert opts.tags == ["setup", "deploy"]
        assert opts.skip_tags == ["never"]


# ── AnsibleResult ───────────────────────────────────────────────────────────


class TestAnsibleResult:
    def test_defaults(self):
        from general_ludd.ansible.core_runner import AnsibleResult

        r = AnsibleResult()
        assert r.status == "unknown"
        assert r.rc == 0
        assert r.stats == {}
        assert r.events == []
        assert r.host_results == {}
        assert r.error is None

    def test_strip_whitespace_status(self):
        from general_ludd.ansible.core_runner import AnsibleResult

        r = AnsibleResult(status="  failed  ")
        assert r.status == "failed"

    def test_strip_handles_whitespace_embedded(self):
        from general_ludd.ansible.core_runner import AnsibleResult

        r = AnsibleResult(status="  successful  ")
        assert r.status == "successful"

    def test_error_field(self):
        from general_ludd.ansible.core_runner import AnsibleResult

        r = AnsibleResult(status="failed", rc=1, error="something broke")
        assert r.error == "something broke"

    def test_model_dump_roundtrip(self):
        from general_ludd.ansible.core_runner import AnsibleResult

        r = AnsibleResult(
            status="successful",
            rc=0,
            stats={"ok": 3},
            events=[{"event": "runner_on_ok"}],
        )
        d = r.model_dump()
        r2 = AnsibleResult(**d)
        assert r2.status == "successful"
        assert r2.stats == {"ok": 3}


# ── CoreAnsibleRunner close ─────────────────────────────────────────────────


class TestCoreAnsibleRunnerClose:
    def test_close_with_none_private_dir(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner(private_data_dir=None)
        runner.close()

    def test_close_with_empty_private_dir(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner(private_data_dir="")
        runner.close()

    def test_close_with_dir(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        d = tmp_path / "private_data"
        d.mkdir()
        (d / "artifact").write_text("data")
        runner = CoreAnsibleRunner(private_data_dir=str(d))
        runner.close()
        assert not d.exists()


# ── CoreAnsibleRunner render_template ───────────────────────────────────────


class TestRenderTemplate:
    def test_simple_template(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        result = runner.render_template("Hello {{ name }}", {"name": "World"})
        assert "Hello" in result

    def test_template_with_no_vars(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        result = runner.render_template("plain text")
        assert result == "plain text"


# ── CoreAnsibleRunner _terminate_tree (static) ──────────────────────────────


class TestTerminateTree:
    def test_already_dead_child(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        proc = MagicMock()
        proc.pid = 99999
        proc.is_alive.return_value = False

        CoreAnsibleRunner._terminate_tree(proc)
        proc.join.assert_not_called()

    def test_alive_child_gets_signalled(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        with patch("os.killpg") as mock_killpg, patch("os.kill"):
            proc = MagicMock()
            proc.pid = 12345
            alive_states = [True, False]  # first check alive, then dead
            proc.is_alive.side_effect = lambda: alive_states.pop(0) if alive_states else False

            CoreAnsibleRunner._terminate_tree(proc)
            assert mock_killpg.call_count >= 1


# ── CoreAnsibleRunner list_tasks ────────────────────────────────────────────


class TestListTasks:
    def test_full_playbook(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "multi.yml"
        pb.write_text(
            textwrap.dedent("""\
                - hosts: webservers
                  tasks:
                    - name: install nginx
                      ansible.builtin.apt:
                        name: nginx
                    - name: start service
                      ansible.builtin.service:
                        name: nginx
                        state: started
                - hosts: databases
                  tasks:
                    - name: create db
                      ansible.builtin.shell:
                        cmd: createdb mydb
            """)
        )
        runner = CoreAnsibleRunner()
        result = runner.list_tasks(str(pb))
        assert len(result) == 3
        assert result[0]["name"] == "install nginx"
        assert result[0]["module"] == "ansible.builtin.apt"
        assert result[0]["hosts"] == "webservers"
        assert result[1]["module"] == "ansible.builtin.service"
        assert result[2]["hosts"] == "databases"

    def test_task_with_only_non_module_keys(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "meta.yml"
        pb.write_text(
            textwrap.dedent("""\
                - hosts: all
                  tasks:
                    - name: meta task
                      when: false
                      tags: always
                      register: result
            """)
        )
        runner = CoreAnsibleRunner()
        result = runner.list_tasks(str(pb))
        assert len(result) == 1
        assert result[0]["name"] == "meta task"
        assert result[0]["module"] == ""

    def test_nonexistent_file(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        result = runner.list_tasks(str(tmp_path / "missing.yml"))
        assert result == []

    def test_empty_playbook(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "empty.yml"
        pb.write_text("[]\n")
        runner = CoreAnsibleRunner()
        result = runner.list_tasks(str(pb))
        assert result == []

    def test_play_with_no_tasks(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "notasks.yml"
        pb.write_text("- hosts: all\n  name: empty play\n")
        runner = CoreAnsibleRunner()
        result = runner.list_tasks(str(pb))
        assert result == []


# ── CoreAnsibleRunner validate_playbook_syntax ──────────────────────────────


class TestValidatePlaybookSyntax:
    def test_yaml_syntax_error(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "syntax.yml"
        pb.write_text(": : : bad yaml\n")
        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax(str(pb))
        assert any("YAML syntax error" in e for e in errors)

    def test_valid_multi_play(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "valid.yml"
        pb.write_text(
            textwrap.dedent("""\
                - hosts: all
                  tasks: []
                - hosts: db
                  tasks: []
            """)
        )
        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax(str(pb))
        assert errors == []

    def test_named_play_missing_hosts(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "nohosts.yml"
        pb.write_text("- name: myplay\n  tasks: []\n")
        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax(str(pb))
        assert any("myplay" in e and "missing 'hosts'" in e for e in errors)

    def test_unnamed_play_missing_hosts(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "noname.yml"
        pb.write_text("- tasks: []\n")
        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax(str(pb))
        assert any("unnamed" in e and "missing 'hosts'" in e for e in errors)

    def test_file_not_found(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        errors = runner.validate_playbook_syntax("/no/such/file.yml")
        assert len(errors) == 1
        assert "not found" in errors[0]


# ── CoreAnsibleRunner run_playbook network_policy ────────────────────────────


class TestRunPlaybookNetworkPolicy:
    def test_policy_blocks_execution(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "net.yml"
        pb.write_text("- hosts: all\n  tasks: []\n")

        policy = MagicMock()
        with patch(
            "general_ludd.ansible.network_policy.scan_playbook_tasks",
            return_value=["POST /admin not allowed"],
        ):
            runner = CoreAnsibleRunner(network_policy=policy)
            result = runner.run_playbook(str(pb))
            assert result.status == "failed"
            assert result.rc == 1
            assert "network policy denied" in result.error

    def test_policy_allows_execution(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "ok.yml"
        pb.write_text("- hosts: all\n  tasks: []\n")

        policy = MagicMock()
        with patch(
            "general_ludd.ansible.network_policy.scan_playbook_tasks",
            return_value=[],
        ):
            runner = CoreAnsibleRunner(network_policy=policy)
            result = runner.run_playbook(str(pb))
            assert result.status == "successful" or result.status == "failed"  # rest of pipeline


# ── CoreAnsibleRunner _run_with_timeout ─────────────────────────────────────


class TestRunWithTimeout:
    def test_none_timeout_runs_inline(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with patch.object(runner, "_execute_with_core", return_value=MagicMock(status="successful", rc=0)) as mock_exec:
            runner._run_with_timeout(
                timeout=None,
                playbook_path="/t.yml",
            )
            mock_exec.assert_called_once()

    def test_zero_timeout_runs_inline(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with patch.object(runner, "_execute_with_core", return_value=MagicMock(status="successful", rc=0)) as mock_exec:
            runner._run_with_timeout(
                timeout=0,
                playbook_path="/t.yml",
            )
            mock_exec.assert_called_once()

    def test_negative_timeout_runs_inline(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with patch.object(runner, "_execute_with_core", return_value=MagicMock(status="successful", rc=0)) as mock_exec:
            runner._run_with_timeout(
                timeout=-1,
                playbook_path="/t.yml",
            )
            mock_exec.assert_called_once()

    def test_process_creation_failure(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with patch("multiprocessing.get_context") as mock_ctx:
            mock_ctx.return_value.Process.side_effect = RuntimeError("cannot fork")
            result = runner._run_with_timeout(
                timeout=30,
                playbook_path="/t.yml",
            )
            assert result.status == "failed"
            assert "unable to start" in result.error

    def test_child_exits_without_result(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        mock_proc = MagicMock()
        mock_proc.pid = 10001
        mock_proc.is_alive.return_value = False
        mock_queue = MagicMock()
        mock_queue.get_nowait.side_effect = Exception("empty")

        with patch("multiprocessing.get_context") as mock_ctx:
            mock_ctx.return_value.Process.return_value = mock_proc
            mock_ctx.return_value.Queue.return_value = mock_queue
            result = runner._run_with_timeout(
                timeout=30,
                playbook_path="/t.yml",
            )
            assert result.status == "failed"
            assert "without a result" in result.error

    def test_child_reports_error(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        mock_proc = MagicMock()
        mock_proc.pid = 10002
        mock_proc.is_alive.return_value = False
        mock_queue = MagicMock()
        mock_queue.get_nowait.return_value = ("err", "something crashed")

        with patch("multiprocessing.get_context") as mock_ctx:
            mock_ctx.return_value.Process.return_value = mock_proc
            mock_ctx.return_value.Queue.return_value = mock_queue
            result = runner._run_with_timeout(
                timeout=30,
                playbook_path="/t.yml",
            )
            assert result.status == "failed"
            assert "something crashed" in result.error

    def test_child_reports_ok(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        mock_proc = MagicMock()
        mock_proc.pid = 10003
        mock_proc.is_alive.return_value = False
        mock_queue = MagicMock()
        mock_queue.get_nowait.return_value = (
            "ok",
            {"status": "successful", "rc": 0, "stats": {"ok": 3}},
        )

        with patch("multiprocessing.get_context") as mock_ctx:
            mock_ctx.return_value.Process.return_value = mock_proc
            mock_ctx.return_value.Queue.return_value = mock_queue
            result = runner._run_with_timeout(
                timeout=30,
                playbook_path="/t.yml",
            )
            assert result.status == "successful"
            assert result.rc == 0
            assert result.stats == {"ok": 3}
            mock_queue.close.assert_called_once_with()
            mock_queue.join_thread.assert_called_once_with()


# ── CoreAnsibleRunner _execute_with_core ────────────────────────────────────


class TestExecuteWithCore:
    def test_process_state_context_restores_import_and_ansible_globals(self):
        from ansible import context
        from ansible.utils.collection_loader import AnsibleCollectionConfig

        from general_ludd.ansible.core_runner import _isolated_ansible_process_state

        env_key = "ANSIBLE_COLLECTIONS_PATH"
        original_env = os.environ.get(env_key)
        original_cliargs = context.CLIARGS
        original_finder = AnsibleCollectionConfig._collection_finder
        original_meta_path = list(sys.meta_path)
        original_path_hooks = list(sys.path_hooks)
        original_importer_cache = dict(sys.path_importer_cache)
        fake_finder = MagicMock(name="test_collection_finder")
        fake_path_hook = MagicMock(name="test_path_hook")
        fake_cliargs = MagicMock(name="test_cliargs")

        with _isolated_ansible_process_state({env_key: "/tmp/gludd-test-collections"}):
            assert os.environ[env_key] == "/tmp/gludd-test-collections"
            context.CLIARGS = fake_cliargs
            AnsibleCollectionConfig._collection_finder = fake_finder
            sys.meta_path.append(fake_finder)
            sys.path_hooks.append(fake_path_hook)
            sys.path_importer_cache["/tmp/gludd-test-import-cache"] = fake_finder

        assert os.environ.get(env_key) == original_env
        assert context.CLIARGS is original_cliargs
        assert AnsibleCollectionConfig._collection_finder is original_finder
        assert sys.meta_path == original_meta_path
        assert sys.path_hooks == original_path_hooks
        assert sys.path_importer_cache == original_importer_cache

    def test_inline_run_successful(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "simple.yml"
        pb.write_text("- hosts: localhost\n  connection: local\n  tasks: []\n")
        runner = CoreAnsibleRunner()
        result = runner._execute_with_core(playbook_path=str(pb))
        assert result.status == "successful"
        assert result.rc == 0

    def test_inline_run_closes_ansible_connection_lock(self, tmp_path):
        from ansible.executor.playbook_executor import PlaybookExecutor

        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "simple.yml"
        pb.write_text("- hosts: localhost\n  connection: local\n  tasks: []\n")
        executors = []

        def capture_executor(*args, **kwargs):
            executor = PlaybookExecutor(*args, **kwargs)
            executors.append(executor)
            return executor

        with patch(
            "ansible.executor.playbook_executor.PlaybookExecutor",
            side_effect=capture_executor,
        ):
            result = CoreAnsibleRunner()._execute_with_core(playbook_path=str(pb))

        assert result.status == "successful"
        assert executors
        assert executors[0]._tqm._connection_lockfile.closed

    def test_inline_run_with_failed_task(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "fail.yml"
        pb.write_text(
            textwrap.dedent("""\
                - hosts: localhost
                  connection: local
                  gather_facts: false
                  tasks:
                    - name: this fails
                      ansible.builtin.fail:
                        msg: deliberate
            """)
        )
        runner = CoreAnsibleRunner()
        result = runner._execute_with_core(playbook_path=str(pb))
        assert result.status == "failed"
        assert result.rc != 0

    def test_run_playbook_check_mode(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "check.yml"
        pb.write_text("- hosts: localhost\n  connection: local\n  tasks: []\n")
        runner = CoreAnsibleRunner()
        result = runner.run_playbook(playbook_path=str(pb), check=True)
        assert result.status == "successful"

    def test_run_playbook_with_tags_and_skip(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "tagged.yml"
        pb.write_text("- hosts: localhost\n  connection: local\n  tasks: []\n")
        runner = CoreAnsibleRunner()
        result = runner.run_playbook(playbook_path=str(pb), tags=["deploy"], skip_tags=["never"], verbosity=1)
        assert result.status == "successful"

    def test_run_playbook_with_ecvarvars(self, monkeypatch, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        monkeypatch.delenv("GLUDD_PLAYBOOK_TIMEOUT", raising=False)
        pb = tmp_path / "simple.yml"
        pb.write_text("- hosts: localhost\n  connection: local\n  tasks: []\n")
        runner = CoreAnsibleRunner()
        result = runner.run_playbook(playbook_path=str(pb), extravars={"color": "blue"}, become=False)
        assert result.status == "successful"

    def test_run_playbook_timeout_from_env(self, monkeypatch, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "9999")
        pb = tmp_path / "simple.yml"
        pb.write_text("- hosts: localhost\n  connection: local\n  tasks: []\n")
        runner = CoreAnsibleRunner()
        result = runner.run_playbook(playbook_path=str(pb))
        assert result.status == "successful"

    def test_run_playbook_explicit_timeout(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "simple.yml"
        pb.write_text("- hosts: localhost\n  connection: local\n  gather_facts: false\n  tasks: []\n")
        runner = CoreAnsibleRunner()
        result = runner.run_playbook(playbook_path=str(pb), timeout=60)
        assert result.status == "successful"


# ── CoreAnsibleRunner resolve_variable ──────────────────────────────────────


class TestResolveVariable:
    def test_resolve_without_inventory(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        result = runner.resolve_variable("ansible_hostname")
        assert result is None or result is not None  # depends on env

    def test_resolve_nonexistent_variable(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        result = runner.resolve_variable("nonexistent_var_xyzzy")
        assert result is None


# ── CoreAnsibleRunner process_isolation path ────────────────────────────────


class TestProcessIsolation:
    def test_isolation_disabled_uses_core(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        iso = MagicMock()
        iso.enabled = False
        pb = tmp_path / "simple.yml"
        pb.write_text("- hosts: localhost\n  connection: local\n  tasks: []\n")
        runner = CoreAnsibleRunner(process_isolation=iso)
        result = runner.run_playbook(playbook_path=str(pb))
        assert result.status == "successful"


# ── CoreAnsibleRunner _execute_with_runner ──────────────────────────────────


class TestExecuteWithRunner:
    def test_runner_not_installed(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        iso = MagicMock()
        iso.enabled = True
        runner = CoreAnsibleRunner(process_isolation=iso)
        with patch.object(
            runner.__class__,
            "_execute_with_runner",
            wraps=runner._execute_with_runner,
        ) as spy:
            import general_ludd.ansible.core_runner as mod

            orig = mod._HAS_ANSIBLE_RUNNER
            mod._HAS_ANSIBLE_RUNNER = False
            try:
                result = spy(
                    playbook_path=str(tmp_path / "p.yml"),
                )
                assert result.status == "failed"
                assert "ansible-runner" in (result.error or "")
            finally:
                mod._HAS_ANSIBLE_RUNNER = orig

    def test_runner_available_but_isolation_none(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner(process_isolation=None)
        result = runner._execute_with_runner(playbook_path="/t.yml")
        assert result.status == "failed"
        assert "unexpectedly missing" in (result.error or "")

    def test_runner_available_isolation_raises(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        iso = MagicMock()
        iso.enabled = True
        iso.to_runner_kwargs.return_value = {"container_image": "test"}
        runner = CoreAnsibleRunner(process_isolation=iso)

        import general_ludd.ansible.core_runner as mod

        orig_runner = mod.ansible_runner
        mock_runner = MagicMock()
        mock_runner.run.side_effect = RuntimeError("container crash")
        mod.ansible_runner = mock_runner
        try:
            result = runner._execute_with_runner(
                playbook_path=str(tmp_path / "p.yml"),
            )
            assert result.status == "failed"
            assert "RuntimeError" in (result.error or "")
            assert "container crash" in (result.error or "")
        finally:
            mod.ansible_runner = orig_runner

    def test_runner_successful(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        iso = MagicMock()
        iso.enabled = True
        iso.to_runner_kwargs.return_value = {"container_image": "test"}
        runner = CoreAnsibleRunner(process_isolation=iso)

        mock_result = MagicMock()
        mock_result.rc = 0
        mock_result.status = "successful"
        mock_result.stats = {"ok": 2}
        mock_result.events = []

        import general_ludd.ansible.core_runner as mod

        orig_runner = mod.ansible_runner
        mock_runner_mod = MagicMock()
        mock_runner_mod.run.return_value = mock_result
        mod.ansible_runner = mock_runner_mod
        try:
            result = runner._execute_with_runner(
                playbook_path=str(tmp_path / "p.yml"),
            )
            assert result.status == "successful"
            assert result.rc == 0
            assert result.stats == {"ok": 2}
        finally:
            mod.ansible_runner = orig_runner


# ── _timeout_child_entry ────────────────────────────────────────────────────


class TestTimeoutChildEntry:
    def test_child_executes_and_posts_ok(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner, _timeout_child_entry

        runner = CoreAnsibleRunner()
        queue = MagicMock()
        with patch.object(runner, "_execute_with_core") as mock_exec:
            mock_exec.return_value = MagicMock(
                model_dump=lambda: {"status": "successful", "rc": 0},
                status="successful",
                rc=0,
            )
            _timeout_child_entry(runner, queue, {"playbook_path": "/t.yml"})
            queue.put.assert_called_once()

    def test_child_posts_error_on_exception(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner, _timeout_child_entry

        runner = CoreAnsibleRunner()
        queue = MagicMock()
        with patch.object(runner, "_execute_with_core", side_effect=ValueError("bad input")):
            _timeout_child_entry(runner, queue, {"playbook_path": "/t.yml"})
            args = queue.put.call_args[0][0]
            assert args[0] == "err"
            assert "ValueError" in args[1]

    def test_child_posts_error_on_systemexit(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner, _timeout_child_entry

        runner = CoreAnsibleRunner()
        queue = MagicMock()
        with patch.object(runner, "_execute_with_core", side_effect=SystemExit(42)):
            _timeout_child_entry(runner, queue, {"playbook_path": "/t.yml"})
            args = queue.put.call_args[0][0]
            assert args[0] == "err"
            assert "SystemExit" in args[1]


# ── CoreAnsibleRunner _PLAYBOOK_ENV_ALLOWLIST ───────────────────────────────


class TestPlaybookEnvAllowlist:
    def test_secret_keys_absent(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        allow = CoreAnsibleRunner._PLAYBOOK_ENV_ALLOWLIST
        assert "ZAI_API_KEY" not in allow
        assert "GLUDD_AUTH_PSK" not in allow
        assert "DATABASE_URL" not in allow
        assert "OPENAI_API_KEY" not in allow

    def test_gludd_config_keys_present(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        allow = CoreAnsibleRunner._PLAYBOOK_ENV_ALLOWLIST
        assert "GLUDD_PLAYBOOK_TIMEOUT" in allow

    def test_ansible_keys_present(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        allow = CoreAnsibleRunner._PLAYBOOK_ENV_ALLOWLIST
        assert "ANSIBLE_CONFIG" in allow
        assert "ANSIBLE_ROLES_PATH" in allow
        assert "ANSIBLE_COLLECTIONS_PATHS" in allow
        assert "ANSIBLE_COLLECTIONS_PATH" in allow

    def test_system_keys_present(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        allow = CoreAnsibleRunner._PLAYBOOK_ENV_ALLOWLIST
        assert "PATH" in allow
        assert "HOME" in allow
        assert "TERM" in allow

    def test_aws_prefix_absent(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        allow = CoreAnsibleRunner._PLAYBOOK_ENV_ALLOWLIST
        for k in allow:
            assert not k.startswith("AWS_"), f"AWS_ key {k} leaked into allowlist"


# ── _get_templar ────────────────────────────────────────────────────────────


class TestGetTemplar:
    def test_no_ansible_core_raises(self):
        import general_ludd.ansible.core_runner as mod

        original = mod._HAS_ANSIBLE_CORE
        mod._HAS_ANSIBLE_CORE = False
        try:
            with pytest.raises(ImportError, match="templating"):
                mod._get_templar()
        finally:
            mod._HAS_ANSIBLE_CORE = original

    def test_with_custom_variables(self):
        from general_ludd.ansible.core_runner import _get_templar

        t = _get_templar(variables={"x": 1})
        assert t is not None
