"""Coverage-gap tests for runner.py and core_runner.py.

Targets uncovered paths: _json_safe, _env_default_timeout, _convert_role_args,
_normalize_role_output, _build_registry, close(), set_project_root, AnsibleResult._strip,
_EventCollectorCallback, _run_with_timeout boundary cases, ansible-runner path,
validate_playbook_syntax edge cases, list_tasks edge cases, run_role error paths,
and CoreAnsibleRunner init + close.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from general_ludd.ansible.core_runner import (
    AnsibleOptions,
    AnsibleResult,
    CoreAnsibleRunner,
    _env_default_timeout,
    _EventCollectorCallback,
    _json_safe,
)
from general_ludd.ansible.runner import (
    AnsibleRunnerAdapter,
    _build_registry,
    _convert_role_args,
    _normalize_role_output,
)

pytestmark = pytest.mark.xdist_group("ansible_runner_coverage_gap")

# ---------------------------------------------------------------------------
# _json_safe coverage
# ---------------------------------------------------------------------------


class TestJsonSafe:
    def test_scalars_pass_through(self):
        assert _json_safe(None) is None
        assert _json_safe(True) is True
        assert _json_safe(42) == 42
        assert _json_safe(3.14) == 3.14
        assert _json_safe("hello") == "hello"

    def test_list_recurses(self):
        assert _json_safe([1, {"k": "v"}, None]) == [1, {"k": "v"}, None]

    def test_tuple_recurses_as_list(self):
        result = _json_safe((1, 2, 3))
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_dict_coerces_nonstring_keys(self):
        result = _json_safe({1: "a", 2.0: "b"})
        assert result == {"1": "a", "2.0": "b"}

    def test_unserializable_falls_back_to_repr(self):
        obj = object()
        assert _json_safe(obj) == repr(obj)


# ---------------------------------------------------------------------------
# _env_default_timeout coverage
# ---------------------------------------------------------------------------


class TestEnvDefaultTimeout:
    def test_env_not_set_returns_default(self, monkeypatch):
        monkeypatch.delenv("GLUDD_PLAYBOOK_TIMEOUT", raising=False)
        assert _env_default_timeout() == 300.0

    def test_env_set_valid(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "120")
        assert _env_default_timeout() == 120.0

    def test_env_set_invalid(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "not-a-number")
        assert _env_default_timeout() == 300.0

    def test_env_set_zero_falls_back(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "0")
        assert _env_default_timeout() == 300.0

    def test_env_set_negative_falls_back(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "-5")
        assert _env_default_timeout() == 300.0

    def test_env_set_empty_falls_back(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "")
        assert _env_default_timeout() == 300.0


# ---------------------------------------------------------------------------
# AnsibleResult._strip validator
# ---------------------------------------------------------------------------


class TestAnsibleResultStrip:
    def test_strips_whitespace(self):
        result = AnsibleResult(status="  successful  ")
        assert result.status == "successful"

    def test_int_coerced_to_string(self):
        result = AnsibleResult(status="123")
        assert result.status == "123"


# ---------------------------------------------------------------------------
# AnsibleOptions full coverage
# ---------------------------------------------------------------------------


class TestAnsibleOptionsExtended:
    def test_all_fields_default(self):
        opts = AnsibleOptions()
        assert opts.inventory == ["localhost,"]
        assert opts.extravars is None
        assert opts.verbosity == 0
        assert opts.check is False
        assert opts.diff is False
        assert opts.forks == 5
        assert opts.become is False
        assert opts.become_method is None
        assert opts.become_user is None
        assert opts.connection == "local"
        assert opts.module_path == []
        assert opts.tags == ["all"]
        assert opts.skip_tags == []
        assert opts.start_at_task is None

    def test_start_at_task_set(self):
        opts = AnsibleOptions(start_at_task="some task")
        assert opts.start_at_task == "some task"

    def test_module_path_set(self):
        opts = AnsibleOptions(module_path=["/a", "/b"])
        assert opts.module_path == ["/a", "/b"]

    def test_become_full(self):
        opts = AnsibleOptions(become=True, become_method="sudo", become_user="admin")
        assert opts.become is True
        assert opts.become_method == "sudo"
        assert opts.become_user == "admin"


# ---------------------------------------------------------------------------
# _EventCollectorCallback all callback methods
# ---------------------------------------------------------------------------


class TestEventCollectorCallback:
    def test_all_events_appended(self):
        cb = _EventCollectorCallback()

        class FakeHost:
            def __str__(self) -> str:
                return "localhost"

        class FakeTask:
            def __str__(self) -> str:
                return "debug"

        class FakeResult:
            def __init__(self) -> None:
                self._host: Any = FakeHost()
                self._task: Any = FakeTask()
                self._result: Any = {"changed": False}

        cb.v2_runner_on_start(FakeHost(), FakeTask())
        cb.v2_runner_on_ok(FakeResult())
        cb.v2_runner_on_failed(FakeResult(), ignore_errors=False)
        cb.v2_runner_on_skipped(FakeResult())
        cb.v2_runner_on_unreachable(FakeResult())
        cb.v2_playbook_on_start("test.yml")

        assert len(cb._events) == 6
        assert cb._events[0]["event"] == "runner_on_start"
        assert cb._events[1]["event"] == "runner_on_ok"
        assert cb._events[2]["event"] == "runner_on_failed"
        assert cb._events[3]["event"] == "runner_on_skipped"
        assert cb._events[4]["event"] == "runner_on_unreachable"
        assert cb._events[5]["event"] == "playbook_on_start"

    def test_playbook_on_stats_populates_host_stats(self):
        cb = _EventCollectorCallback()

        class FakeStats:
            def __init__(self) -> None:
                self.processed: dict[str, Any] = {}

        stats = FakeStats()
        stats.processed = {"localhost": {"ok": 1, "changed": 0}}
        cb.v2_playbook_on_stats(stats)

        assert len(cb._events) == 1
        assert cb._events[0]["event"] == "playbook_on_stats"
        assert cb._host_stats == {"localhost": {"ok": 1, "changed": 0}}


# ---------------------------------------------------------------------------
# CoreAnsibleRunner init extended
# ---------------------------------------------------------------------------


class TestCoreAnsibleRunnerInitExtended:
    def test_init_with_seccomp_filter(self):
        mock_filter = MagicMock()
        runner = CoreAnsibleRunner(seccomp_filter=mock_filter)
        assert runner._seccomp_filter is mock_filter

    def test_init_with_network_policy(self):
        mock_policy = MagicMock()
        runner = CoreAnsibleRunner(network_policy=mock_policy)
        assert runner._network_policy is mock_policy

    def test_init_with_private_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = CoreAnsibleRunner(private_data_dir=tmp)
            assert runner._private_data_dir == tmp

    def test_close_removes_private_data_dir(self):
        tmp = tempfile.mkdtemp()
        runner = CoreAnsibleRunner(private_data_dir=tmp)
        assert os.path.isdir(tmp)
        runner.close()
        assert not os.path.isdir(tmp)
        assert runner._private_data_dir == ""

    def test_close_idempotent(self):
        runner = CoreAnsibleRunner()
        runner.close()  # should not raise


# ---------------------------------------------------------------------------
# _convert_role_args all role types
# ---------------------------------------------------------------------------


class TestConvertRoleArgs:
    def test_bom_detect_with_file_path(self):
        result = _convert_role_args("bom_detect", {"file_path": "/tmp/foo.txt"})
        assert result == ["--input-file", "/tmp/foo.txt"]

    def test_bom_detect_without_file_path(self):
        result = _convert_role_args("bom_detect", {})
        assert result == []

    def test_encoding_detect_with_file_path(self):
        result = _convert_role_args("encoding_detect", {"file_path": "/tmp/bar.txt"})
        assert result == ["--input-file", "/tmp/bar.txt"]

    def test_font_analyze_with_file_path(self):
        result = _convert_role_args("font_analyze", {"file_path": "/tmp/font.ttf"})
        assert result == ["--input", "/tmp/font.ttf"]

    def test_homoglyph_scan_with_text(self):
        result = _convert_role_args("homoglyph_scan", {"text": "abc"})
        assert result == ["--input", "abc"]

    def test_unicode_analyze_with_text(self):
        result = _convert_role_args("unicode_analyze", {"text": "xyz"})
        assert result == ["--input", "xyz"]

    def test_i18n_extract_with_directory(self):
        with patch("tempfile.gettempdir", return_value="/tmp"):
            result = _convert_role_args("i18n_extract", {"directory": "/src"})
        assert result[:2] == ["--source-dir", "/src"]
        assert result[2] == "--output-dir"
        assert result[3].startswith("/tmp/gludd-i18n-extract-")
        suffix = result[3].rsplit("-", 1)[-1]
        assert len(suffix) == 12
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_locale_format_with_locale(self):
        result = _convert_role_args("locale_format", {"locale": "en_US.UTF-8"})
        assert result == ["--locale", "en_US.UTF-8"]

    def test_phonetic_transcribe_basic(self):
        result = _convert_role_args("phonetic_transcribe", {})
        assert result == ["--method", "ipa"]

    def test_phonetic_transcribe_with_text(self):
        result = _convert_role_args("phonetic_transcribe", {"text": "hello"})
        assert result == ["--input", "hello", "--method", "ipa"]

    def test_unknown_role_returns_empty(self):
        result = _convert_role_args("unknown_role", {"file_path": "/tmp/x"})
        assert result == []


# ---------------------------------------------------------------------------
# _normalize_role_output all role types
# ---------------------------------------------------------------------------


class TestNormalizeRoleOutput:
    def test_bom_detect_with_bom(self):
        raw = {"bom_detected": True, "encoding": "UTF-8"}
        result = _normalize_role_output("bom_detect", dict(raw))
        assert result["has_bom"] is True
        assert result["encoding"] == "utf-8"

    def test_bom_detect_without_bom(self):
        raw: dict[str, Any] = {}
        result = _normalize_role_output("bom_detect", dict(raw))
        assert result.get("has_bom") is False

    def test_encoding_detect_sets_encoding_from_detected(self):
        result = _normalize_role_output("encoding_detect", {"detected_encoding": "latin1"})
        assert result["encoding"] == "latin1"

    def test_font_analyze_ttf_format(self):
        result = _normalize_role_output("font_analyze", {"format": "ttf", "file": "/fonts/arial.ttf"})
        assert result["font_name"] == "arial"

    def test_font_analyze_unrecognized_format(self):
        result = _normalize_role_output("font_analyze", {"format": "xyz"})
        assert "error" in result
        assert "Unrecognized font format" in result["error"]

    def test_homoglyph_scan_counts_confusables(self):
        findings = [
            {"type": "confusable", "char": "a"},
            {"type": "confusable", "char": "b"},
            {"type": "other", "char": "c"},
        ]
        result = _normalize_role_output("homoglyph_scan", {"findings": findings})
        assert result["confusable_count"] == 2
        assert len(result["confusables"]) == 2

    def test_homoglyph_scan_non_list_findings(self):
        result = _normalize_role_output("homoglyph_scan", {"findings": "not-a-list"})
        assert result["confusable_count"] == 0
        assert result["confusables"] == []

    def test_locale_format_parses_locale(self):
        result = _normalize_role_output("locale_format", {"locale": "en_US.UTF-8"})
        assert result["language"] == "en"
        assert result["territory"] == "US"
        assert result["codeset"] == "UTF-8"

    def test_locale_format_no_codeset(self):
        result = _normalize_role_output("locale_format", {"locale": "fr_FR"})
        assert result["language"] == "fr"
        assert result["territory"] == "FR"
        assert "codeset" not in result

    def test_phonetic_transcribe_joins_ipa(self):
        words = [
            {"transcription": "helo"},
            {"transcription": "wrld"},
        ]
        result = _normalize_role_output("phonetic_transcribe", {"words": words})
        assert result["ipa"] == "helo wrld"

    def test_phonetic_transcribe_non_list_words(self):
        result = _normalize_role_output("phonetic_transcribe", {"words": "bad"})
        assert result["ipa"] == ""

    def test_unicode_analyze_populates_from_codepoints(self):
        codepoints = [{"codepoint": "U+0041", "name": "LATIN CAPITAL LETTER A", "category": "Lu"}]
        result = _normalize_role_output("unicode_analyze", {"codepoints": codepoints})
        assert result["codepoint"] == "U+0041"
        assert result["name"] == "LATIN CAPITAL LETTER A"
        assert result["category"] == "Lu"

    def test_unicode_analyze_empty_codepoints(self):
        result = _normalize_role_output("unicode_analyze", {"codepoints": []})
        assert result["character_count"] == 0


# ---------------------------------------------------------------------------
# _build_registry
# ---------------------------------------------------------------------------


class TestBuildRegistry:
    def test_no_extra_returns_default(self):
        reg = _build_registry()
        assert isinstance(reg, dict)
        assert "noop.yml" in reg

    def test_extra_merges_in(self):
        reg = _build_registry({"custom.yml": "/tmp/custom.yml"})
        assert "custom.yml" in reg
        assert reg["custom.yml"] == "/tmp/custom.yml"


# ---------------------------------------------------------------------------
# AnsibleRunnerAdapter close() / set_project_root
# ---------------------------------------------------------------------------


class TestAdapterClose:
    def test_close_removes_private_data_dir(self):
        tmp = tempfile.mkdtemp()
        adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
        assert os.path.isdir(tmp)
        adapter.close()
        assert not os.path.isdir(tmp)
        assert adapter.private_data_dir == ""

    def test_close_idempotent(self):
        adapter = AnsibleRunnerAdapter()
        adapter.close()
        adapter.close()  # second call should not raise

    def test_set_project_root_clears_with_none(self):
        adapter = AnsibleRunnerAdapter(project_root="/tmp/proj")
        adapter.set_project_root(None)
        assert adapter._project_root is None


# ---------------------------------------------------------------------------
# CoreAnsibleRunner list_tasks edge cases
# ---------------------------------------------------------------------------


class TestListTasksEdgeCases:
    def test_non_dict_play_skipped(self):
        runner = CoreAnsibleRunner()
        playbook = ["not a dict"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            tasks = runner.list_tasks(path)
            assert tasks == []
        finally:
            os.unlink(path)

    def test_non_dict_task_skipped(self):
        runner = CoreAnsibleRunner()
        playbook = [{"hosts": "localhost", "tasks": ["not a task"]}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            tasks = runner.list_tasks(path)
            assert tasks == []
        finally:
            os.unlink(path)

    def test_bad_yaml_returns_empty(self):
        runner = CoreAnsibleRunner()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("not: valid: [\n")
            path = f.name
        try:
            tasks = runner.list_tasks(path)
            assert tasks == []
        finally:
            os.unlink(path)

    def test_play_without_tasks(self):
        runner = CoreAnsibleRunner()
        playbook = [{"hosts": "localhost"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            tasks = runner.list_tasks(path)
            assert tasks == []
        finally:
            os.unlink(path)

    def test_none_yaml_load(self):
        runner = CoreAnsibleRunner()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("")
            path = f.name
        try:
            tasks = runner.list_tasks(path)
            assert tasks == []
        finally:
            os.unlink(path)

    def test_task_discovers_module_from_non_standard_keys(self):
        runner = CoreAnsibleRunner()
        playbook = [{"hosts": "localhost", "tasks": [{"name": "t1", "custom_module": {"arg": 1}}]}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            tasks = runner.list_tasks(path)
            assert len(tasks) == 1
            assert tasks[0]["module"] == "custom_module"
        finally:
            os.unlink(path)

    def test_task_named_not_a_module(self):
        runner = CoreAnsibleRunner()
        playbook = [{"hosts": "localhost", "tasks": [{"name": "skip", "when": True, "debug": {"msg": "hi"}}]}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            tasks = runner.list_tasks(path)
            assert len(tasks) == 1
            assert tasks[0]["module"] == "debug"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# CoreAnsibleRunner validate_playbook_syntax edge cases
# ---------------------------------------------------------------------------


class TestValidatePlaybookSyntaxEdgeCases:
    def test_non_list_playbook(self):
        runner = CoreAnsibleRunner()
        playbook: dict[str, Any] = {"name": "not a list"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            errors = runner.validate_playbook_syntax(path)
            assert len(errors) == 1
            assert "list of plays" in errors[0]
        finally:
            os.unlink(path)

    def test_non_dict_play_in_list(self):
        runner = CoreAnsibleRunner()
        playbook = ["not a dict either"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            errors = runner.validate_playbook_syntax(path)
            assert len(errors) == 1
            assert "not a mapping" in errors[0]
        finally:
            os.unlink(path)

    def test_play_with_missing_hosts(self):
        runner = CoreAnsibleRunner()
        playbook = [{"name": "no-hosts", "tasks": [{"debug": {"msg": "hi"}}]}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            errors = runner.validate_playbook_syntax(path)
            assert len(errors) == 1
            assert "missing 'hosts'" in errors[0]
        finally:
            os.unlink(path)

    def test_multiple_missing_hosts(self):
        runner = CoreAnsibleRunner()
        playbook = [
            {"name": "p1", "tasks": [{"debug": {"msg": "h"}}]},
            {"name": "p2", "tasks": [{"debug": {"msg": "w"}}]},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            errors = runner.validate_playbook_syntax(path)
            assert len(errors) == 2
        finally:
            os.unlink(path)

    def test_valid_playbook_no_errors(self):
        runner = CoreAnsibleRunner()
        playbook = [{"hosts": "localhost", "tasks": [{"debug": {"msg": "ok"}}]}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            errors = runner.validate_playbook_syntax(path)
            assert errors == []
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# _run_with_timeout boundary cases
# ---------------------------------------------------------------------------


class TestRunWithTimeoutBoundary:
    def test_timeout_negative_runs_inline(self):
        runner = CoreAnsibleRunner()
        mock_result = AnsibleResult(status="successful", rc=0)
        with patch.object(runner, "_execute_with_core", return_value=mock_result) as mock_exec:
            result = runner._run_with_timeout(timeout=-1, playbook_path="/tmp/p.yml")
        assert result == mock_result
        mock_exec.assert_called_once()

    def test_timeout_zero_runs_inline(self):
        runner = CoreAnsibleRunner()
        mock_result = AnsibleResult(status="successful", rc=0)
        with patch.object(runner, "_execute_with_core", return_value=mock_result) as mock_exec:
            result = runner._run_with_timeout(timeout=0, playbook_path="/tmp/p.yml")
        assert result == mock_result
        mock_exec.assert_called_once()

    def test_timeout_just_negative_runs_inline(self):
        runner = CoreAnsibleRunner()
        mock_result = AnsibleResult(status="successful", rc=0)
        with patch.object(runner, "_execute_with_core", return_value=mock_result) as mock_exec:
            result = runner._run_with_timeout(timeout=-5, playbook_path="/tmp/p.yml")
        assert result == mock_result
        mock_exec.assert_called_once()


# ---------------------------------------------------------------------------
# _terminate_tree coverage
# ---------------------------------------------------------------------------


class TestTerminateTree:
    def test_sends_sigterm_then_sigkill(self):
        proc = MagicMock()
        proc.pid = 12345
        proc.is_alive.side_effect = [True, False, True, False]

        with patch("os.killpg") as mock_killpg, patch("os.kill"):
            CoreAnsibleRunner._terminate_tree(proc)

        assert mock_killpg.called

    def test_falls_back_to_kill_on_killpg_error(self):
        proc = MagicMock()
        proc.pid = 12345
        proc.is_alive.return_value = False

        with patch("os.killpg", side_effect=ProcessLookupError), patch("os.kill"):
            CoreAnsibleRunner._terminate_tree(proc)

    def test_sigkill_sent_to_group(self):
        proc = MagicMock()
        proc.pid = 54321
        proc.is_alive.side_effect = [True, True, False]  # alive after SIGTERM, alive after join, dead after SIGKILL

        with patch("os.killpg") as mock_killpg:
            CoreAnsibleRunner._terminate_tree(proc)
        assert mock_killpg.call_count >= 2  # SIGTERM + SIGKILL


# ---------------------------------------------------------------------------
# run_playbook — network_policy and ansible-runner path
# ---------------------------------------------------------------------------


class TestRunPlaybookNetworkPolicy:
    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_network_policy_blocks_execution(self):
        mock_policy = MagicMock()
        mock_policy.fail_closed = True

        with patch(
            "general_ludd.ansible.network_policy.scan_playbook_tasks",
            return_value=["POST /admin not allowed"],
        ):
            runner = CoreAnsibleRunner(network_policy=mock_policy)
            result = runner.run_playbook("/tmp/playbook.yml")
        assert result.status == "failed"
        assert result.rc == 1
        assert "network policy denied" in (result.error or "")

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_network_policy_allows_passthrough(self):
        mock_policy = MagicMock()

        with patch("general_ludd.ansible.network_policy.scan_playbook_tasks", return_value=[]):
            runner = CoreAnsibleRunner(network_policy=mock_policy, private_data_dir="/tmp")
            mock_exec_result = AnsibleResult(status="successful", rc=0)
            with patch.object(runner, "_execute_with_core", return_value=mock_exec_result):
                result = runner.run_playbook("/tmp/playbook.yml")
        assert result.status == "successful"


class TestRunPlaybookIsolationPath:
    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_isolation_enabled_delegates_to_runner_backend(self):
        from general_ludd.ansible.isolation import ProcessIsolationConfig

        iso = ProcessIsolationConfig(
            enabled=True,
            executable="podman",
            container_image="registry.example/gludd-ee:test@sha256:" + "a" * 64,
        )
        runner = CoreAnsibleRunner(process_isolation=iso)
        mock_result = AnsibleResult(status="successful", rc=0)
        with patch.object(runner, "_execute_with_runner", return_value=mock_result) as mock_runner:
            result = runner.run_playbook("/tmp/playbook.yml")
        assert result.status == "successful"
        mock_runner.assert_called_once()

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_RUNNER", False)
    def test_isolation_enabled_no_ansible_runner_package(self):
        from general_ludd.ansible.isolation import ProcessIsolationConfig

        iso = ProcessIsolationConfig(
            enabled=True,
            executable="podman",
            container_image="registry.example/gludd-ee:test@sha256:" + "a" * 64,
        )
        runner = CoreAnsibleRunner(process_isolation=iso)
        result = runner.run_playbook("/tmp/playbook.yml")
        assert result.status == "failed"
        assert "ansible-runner" in (result.error or "")


class TestRunPlaybookTimeoutPath:
    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_explicit_timeout_delegates_to_run_with_timeout(self):
        runner = CoreAnsibleRunner()
        mock_result = AnsibleResult(status="successful", rc=0)
        with patch.object(runner, "_run_with_timeout", return_value=mock_result) as mock_timeout:
            result = runner.run_playbook("/tmp/playbook.yml", timeout=10.0)
        assert result == mock_result
        mock_timeout.assert_called_once()

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True)
    def test_run_playbook_env_default_timeout_delegates_to_timeout(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PLAYBOOK_TIMEOUT", "90")
        runner = CoreAnsibleRunner()
        mock_result = AnsibleResult(status="successful", rc=0)
        with patch.object(runner, "_run_with_timeout", return_value=mock_result) as mock_timeout:
            result = runner.run_playbook("/tmp/playbook.yml")
        assert result == mock_result
        call_kwargs = mock_timeout.call_args.kwargs
        assert call_kwargs["timeout"] == 90.0


# ---------------------------------------------------------------------------
# AnsibleRunnerAdapter.run_playbook extended
# ---------------------------------------------------------------------------


class TestAdapterRunPlaybookExtended:
    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_version_activation_roots_injected(self, mock_core_cls: MagicMock):
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

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            adapter._version_activation_roots = [Path("/tmp/activated")]
            adapter.run_playbook("noop.yml")

        call_kwargs = mock_core.run_playbook.call_args.kwargs
        env = call_kwargs.get("extra_env") or {}
        assert "ANSIBLE_COLLECTIONS_PATH" in env
        assert "/tmp/activated" in env["ANSIBLE_COLLECTIONS_PATH"]
        assert "ANSIBLE_ROLES_PATH" in env

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_version_activation_appends_to_existing_env(self, mock_core_cls: MagicMock):
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

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            adapter._version_activation_roots = [Path("/tmp/activated")]
            adapter._collections_env = {"ANSIBLE_COLLECTIONS_PATH": "/existing"}
            adapter.run_playbook("noop.yml")

        call_kwargs = mock_core.run_playbook.call_args.kwargs
        env = call_kwargs.get("extra_env") or {}
        acp = env.get("ANSIBLE_COLLECTIONS_PATH", "")
        assert acp.startswith("/tmp/activated")
        assert "/existing" in acp

    def test_run_playbook_fails_on_unregistered(self):
        adapter = AnsibleRunnerAdapter()
        result = adapter.run_playbook("nonexistent.yml")
        assert result["status"] == "failed"
        assert "not registered" in result["error"]


# ---------------------------------------------------------------------------
# run_role extended error paths
# ---------------------------------------------------------------------------


class TestRunRoleExtended:
    @pytest.mark.asyncio
    async def test_role_empty_role_name(self):
        adapter = AnsibleRunnerAdapter()
        result = await adapter.run_role({"role": ""})
        assert "error" in result
        assert "No 'role'" in result["error"]

    @pytest.mark.asyncio
    async def test_role_missing_role_key(self):
        adapter = AnsibleRunnerAdapter()
        result = await adapter.run_role({})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_role_script_not_found(self):
        adapter = AnsibleRunnerAdapter()
        result = await adapter.run_role({"role": "nonexistent_module"})
        assert "error" in result
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# write_vars extended
# ---------------------------------------------------------------------------


class TestWriteVarsExtended:
    @pytest.mark.parametrize("evil_id", ["", "/etc/passwd", "a b", "..", "../.."])
    def test_write_vars_rejects_evil_ids(self, evil_id: str):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            with pytest.raises(ValueError):
                adapter.write_vars(evil_id, job_vars={"x": 1})

    def test_write_vars_custom_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            adapter.prepare_job_dirs("JOB-CUSTOM")
            path = adapter.write_vars("JOB-CUSTOM", {"k": "v"}, filename="myvars")
            assert os.path.basename(path) == "myvars"

    def test_write_vars_without_prepare_creates_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            path = adapter.write_vars("JOB-LAZY", {"x": 1})
            assert os.path.isfile(path)
            assert "JOB-LAZY" in path


# ---------------------------------------------------------------------------
# CoreAnsibleRunner run_playbook fallback without core
# ---------------------------------------------------------------------------


class TestCoreRunnerWithoutAnsibleCore:
    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", False)
    def test_run_playbook_raises_import_error(self):
        runner = CoreAnsibleRunner()
        with pytest.raises(ImportError, match="ansible-core"):
            runner.run_playbook("/tmp/p.yml")

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", False)
    def test_resolve_variable_raises_import_error(self):
        runner = CoreAnsibleRunner()
        with pytest.raises(ImportError, match="ansible-core"):
            runner.resolve_variable("x")

    @patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", False)
    def test_render_template_raises_import_error(self):
        runner = CoreAnsibleRunner()
        with pytest.raises(ImportError, match="ansible-core"):
            runner.render_template("{{ x }}")


# ---------------------------------------------------------------------------
# Process isolation config via runner adapter
# ---------------------------------------------------------------------------


class TestAdapterIsolationPassthrough:
    def test_isolation_passed_to_core_runner(self):
        from general_ludd.ansible.isolation import ProcessIsolationConfig

        iso = ProcessIsolationConfig(enabled=False)
        with patch("general_ludd.ansible.runner.CoreAnsibleRunner") as mock_core_cls:
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            with tempfile.TemporaryDirectory() as tmp:
                AnsibleRunnerAdapter(private_data_dir=tmp, isolation_config=iso)
            call_kwargs = mock_core_cls.call_args.kwargs
            assert call_kwargs["process_isolation"] is iso


# ---------------------------------------------------------------------------
# activate_collection edge cases
# ---------------------------------------------------------------------------


class TestActivateCollectionEdgeCases:
    def test_no_project_root_raises(self):
        adapter = AnsibleRunnerAdapter()
        with pytest.raises(FileNotFoundError):
            adapter.activate_collection("test", "ns")

    @patch("general_ludd.ansible.runner.resolve_collections_paths", return_value=[])
    def test_no_collections_dirs_raises(self, _mock_paths):
        adapter = AnsibleRunnerAdapter(project_root="/nonexistent/path")
        with pytest.raises(FileNotFoundError):
            adapter.activate_collection("test", "ns")


# ---------------------------------------------------------------------------
# resolve_playbook error path
# ---------------------------------------------------------------------------


class TestResolvePlaybookError:
    def test_unregistered_playbook_raises(self):
        adapter = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="not registered"):
            adapter.resolve_playbook("evil.yml")


# ---------------------------------------------------------------------------
# event_bus integration via _scan_playbook_dir
# ---------------------------------------------------------------------------


class TestScanPlaybookDir:
    def test_scan_with_event_bus_publishes_events(self):
        bus = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            # Create a valid playbook file
            playbook_path = os.path.join(td, "test-scan.yml")
            with open(playbook_path, "w") as f:
                yaml.dump([{"hosts": "localhost", "tasks": [{"debug": {"msg": "hi"}}]}], f)
            with patch("general_ludd.ansible.runner._PLAYBOOKS_ROOT", Path(td)):
                AnsibleRunnerAdapter(event_bus=bus, playbooks_dir=td)
            assert bus.publish.called

    def test_scan_without_event_bus_no_crash(self):
        with tempfile.TemporaryDirectory() as td:
            playbook_path = os.path.join(td, "test-scan.yml")
            with open(playbook_path, "w") as f:
                yaml.dump([{"hosts": "localhost", "tasks": [{"debug": {"msg": "hi"}}]}], f)
            with patch("general_ludd.ansible.runner._PLAYBOOKS_ROOT", Path(td)):
                adapter = AnsibleRunnerAdapter(playbooks_dir=td)
            assert "test-scan.yml" in adapter.registry
