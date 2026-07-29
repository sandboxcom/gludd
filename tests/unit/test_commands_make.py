"""Structural tests for commands/make.py — structured subprocess runner for make targets."""

from __future__ import annotations

import io
import signal
import subprocess
import threading
from unittest.mock import MagicMock, call, patch

import pytest


class TestModuleImports:
    def test_module_can_be_imported(self):
        import general_ludd.commands.make

        assert general_ludd.commands.make is not None

    def test_logger_exists(self):
        import logging

        from general_ludd.commands.make import logger

        assert isinstance(logger, logging.Logger)
        assert logger.name == "general_ludd.commands.make"


class TestConstants:
    def test_tail_chars_default(self):
        from general_ludd.commands.make import _TAIL_CHARS

        assert _TAIL_CHARS == 16000

    def test_default_timeout_s(self):
        from general_ludd.commands.make import _DEFAULT_TIMEOUT_S

        assert _DEFAULT_TIMEOUT_S == 300

    def test_read_chunk(self):
        from general_ludd.commands.make import _READ_CHUNK

        assert _READ_CHUNK == 8192

    def test_forbidden_metachars_is_frozenset(self):
        from general_ludd.commands.make import _FORBIDDEN_METACHARS

        assert isinstance(_FORBIDDEN_METACHARS, frozenset)
        assert ";" in _FORBIDDEN_METACHARS
        assert "|" in _FORBIDDEN_METACHARS
        assert "&" in _FORBIDDEN_METACHARS
        assert "$" in _FORBIDDEN_METACHARS

    def test_base_env_keys(self):
        from general_ludd.commands.make import _BASE_ENV_KEYS

        assert isinstance(_BASE_ENV_KEYS, tuple)
        assert "PATH" in _BASE_ENV_KEYS
        assert "HOME" in _BASE_ENV_KEYS

    def test_fallback_path(self):
        from general_ludd.commands.make import _FALLBACK_PATH

        assert isinstance(_FALLBACK_PATH, str)
        assert "/bin" in _FALLBACK_PATH

    def test_phase_re_compiled(self):
        import re

        from general_ludd.commands.make import _PHASE_RE

        assert isinstance(_PHASE_RE, re.Pattern)


class TestBoundedReader:
    def test_is_thread_subclass(self):
        from general_ludd.commands.make import _BoundedReader

        assert issubclass(_BoundedReader, threading.Thread)

    def test_constructor_accepts_stream_and_cap(self):
        from general_ludd.commands.make import _BoundedReader

        reader = _BoundedReader(None, 100)
        assert reader._cap == 100
        assert reader._stream is None

    def test_text_property_returns_str_for_empty(self):
        from general_ludd.commands.make import _BoundedReader

        reader = _BoundedReader(None, 100)
        result = reader.text
        assert isinstance(result, str)
        assert result == ""

    def test_is_daemon_thread(self):
        from general_ludd.commands.make import _BoundedReader

        reader = _BoundedReader(None, 100)
        assert reader.daemon is True

    def test_run_reads_chunks_and_discards_oldest_chunk_over_cap(self):
        from general_ludd.commands.make import _BoundedReader

        stream = MagicMock()
        stream.read.side_effect = ["abcdef", "ghijkl", ""]
        reader = _BoundedReader(stream, 8)

        reader.run()

        assert reader.text == "ghijkl"
        assert stream.read.call_count == 3

    def test_run_tolerates_stream_error_after_partial_output(self):
        from general_ludd.commands.make import _BoundedReader

        stream = MagicMock()
        stream.read.side_effect = ["captured", OSError("closed")]
        reader = _BoundedReader(stream, 100)

        reader.run()

        assert reader.text == "captured"


class TestMakeResult:
    def test_is_dataclass(self):
        import dataclasses

        from general_ludd.commands.make import MakeResult

        assert dataclasses.is_dataclass(MakeResult)

    def test_default_fields(self):
        from general_ludd.commands.make import MakeResult

        result = MakeResult(target="test", exit_code=0, success=True, duration_s=1.0)
        assert result.target == "test"
        assert result.exit_code == 0
        assert result.success is True
        assert result.duration_s == 1.0
        assert result.stdout_tail == ""
        assert result.stderr_tail == ""
        assert result.timed_out is False
        assert result.oom_killed is False
        assert result.error is None
        assert isinstance(result.phases, list)

    def test_phases_are_extractable(self):
        from general_ludd.commands.make import _PHASE_RE, MakeResult

        result = MakeResult(
            target="gate",
            exit_code=0,
            success=True,
            duration_s=1.0,
            stdout_tail="=== LINT ===\nok\n=== TYPECHECK ===\nok\n",
        )
        phases = _PHASE_RE.findall(result.stdout_tail)
        assert "LINT" in phases
        assert "TYPECHECK" in phases


class TestMakeRunner:
    def test_constructor_default_cwd(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        assert runner._default_timeout_s == 300

    def test_constructor_explicit_cwd(self):
        import tempfile
        from pathlib import Path

        from general_ludd.commands.make import MakeRunner

        tmp = Path(tempfile.gettempdir())
        runner = MakeRunner(cwd=tmp)
        assert runner._cwd == tmp.resolve()

    def test_constructor_custom_timeout(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner(default_timeout_s=600)
        assert runner._default_timeout_s == 600

    def test_build_env_returns_dict_with_path(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        env = runner._build_env()
        assert isinstance(env, dict)
        assert "PATH" in env

    def test_build_env_uses_fallback_path_for_empty_parent_environment(self):
        from general_ludd.commands.make import _FALLBACK_PATH, MakeRunner

        with patch.dict("general_ludd.commands.make.os.environ", {}, clear=True):
            env = MakeRunner()._build_env()

        assert env == {"PATH": _FALLBACK_PATH}

    def test_sanitize_args_allows_clean_args(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        result = runner._sanitize_args(["test", "TESTFILE=foo.py"])
        assert result == ["test", "TESTFILE=foo.py"]

    def test_sanitize_args_rejects_semicolon(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        with pytest.raises(ValueError, match="shell metacharacters"):
            runner._sanitize_args(["test; rm -rf"])

    def test_sanitize_args_rejects_pipe(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        with pytest.raises(ValueError, match="shell metacharacters"):
            runner._sanitize_args(["test|xargs"])

    def test_extract_phases_static(self):
        from general_ludd.commands.make import MakeRunner

        text = "=== START ===\nwork\n=== PHASE mid ===\n=== END ==="
        phases = MakeRunner._extract_phases(text)
        assert "START" in phases
        assert "PHASE mid" in phases
        assert "END" in phases

    def test_run_test_method_exists(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        assert callable(runner.run_test)

    def test_run_gate_method_exists(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        assert callable(runner.run_gate)

    def test_run_lint_method_exists(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        assert callable(runner.run_lint)

    def test_run_typecheck_method_exists(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        assert callable(runner.run_typecheck)

    def test_run_specific_method_exists(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        assert callable(runner.run_specific)

    def test_run_method_exists(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        assert callable(runner.run)

    def test_spawn_method_exists(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        assert callable(runner.spawn)

    def test_kill_group_is_static(self):
        from general_ludd.commands.make import MakeRunner

        assert callable(MakeRunner._kill_group)


class TestMakeRunnerSpawn:
    def test_spawn_without_log_returns_pipe_backed_process(self, tmp_path):
        from general_ludd.commands.make import MakeRunner

        process = MagicMock()
        with patch(
            "general_ludd.commands.make.subprocess.Popen", return_value=process
        ) as popen:
            returned_process, log_path = MakeRunner(cwd=tmp_path).spawn(
                "test",
                extra_args=["TESTFILE=tests/unit/test_example.py"],
                env_extra={"GLUDD_RUN_ID": "coverage"},
            )

        assert returned_process is process
        assert log_path is None
        args, kwargs = popen.call_args
        assert args[0] == [
            "make",
            "test",
            "TESTFILE=tests/unit/test_example.py",
        ]
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["env"]["GLUDD_RUN_ID"] == "coverage"
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["start_new_session"] is True

    def test_spawn_with_log_combines_output_in_requested_file(self, tmp_path):
        from general_ludd.commands.make import MakeRunner

        process = MagicMock()
        log_file = tmp_path / "background.log"
        with patch(
            "general_ludd.commands.make.subprocess.Popen", return_value=process
        ) as popen:
            returned_process, log_path = MakeRunner(cwd=tmp_path).spawn(
                "gate",
                log_file=log_file,
            )

        assert returned_process is process
        assert log_path == log_file
        args, kwargs = popen.call_args
        assert args[0] == ["make", "gate"]
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["stderr"] is subprocess.STDOUT
        assert kwargs["text"] is True
        assert kwargs["close_fds"] is True
        assert kwargs["stdout"].name == str(log_file)
        assert kwargs["stdout"].closed is True

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (FileNotFoundError(), "make executable not found"),
            (OSError("resource unavailable"), "spawn failed: resource unavailable"),
        ],
    )
    def test_spawn_translates_process_creation_errors(self, error, message):
        from general_ludd.commands.make import MakeRunner

        with (
            patch("general_ludd.commands.make.subprocess.Popen", side_effect=error),
            pytest.raises(RuntimeError, match=message),
        ):
            MakeRunner().spawn("test")


class TestMakeRunnerRun:
    def test_run_returns_captured_output_phases_and_success(self, tmp_path):
        from general_ludd.commands.make import MakeRunner

        process = MagicMock(
            stdout=io.StringIO("=== COLLECT ===\nready\n"),
            stderr=io.StringIO("diagnostic\n"),
            returncode=0,
        )
        with patch(
            "general_ludd.commands.make.subprocess.Popen", return_value=process
        ) as popen:
            result = MakeRunner(cwd=tmp_path).run(
                "test",
                extra_args=["TESTFILE=tests/unit/test_example.py"],
                env_extra={"GLUDD_RUN_ID": "coverage"},
            )

        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout_tail == "=== COLLECT ===\nready\n"
        assert result.stderr_tail == "diagnostic\n"
        assert result.phases == ["COLLECT"]
        args, kwargs = popen.call_args
        assert args[0] == [
            "make",
            "test",
            "TESTFILE=tests/unit/test_example.py",
        ]
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["env"]["GLUDD_RUN_ID"] == "coverage"
        process.wait.assert_called_once_with(timeout=300)

    @pytest.mark.parametrize(("returncode", "oom_killed"), [(-9, True), (137, True), (2, False)])
    def test_run_reports_oom_and_regular_failures(self, returncode, oom_killed):
        from general_ludd.commands.make import MakeRunner

        process = MagicMock(
            stdout=io.StringIO(""),
            stderr=io.StringIO("failed"),
            returncode=returncode,
        )
        with patch(
            "general_ludd.commands.make.subprocess.Popen", return_value=process
        ):
            result = MakeRunner().run("test", timeout_s=7)

        assert result.success is False
        assert result.exit_code == returncode
        assert result.oom_killed is oom_killed
        assert result.timed_out is False
        process.wait.assert_called_once_with(timeout=7)

    def test_run_terminates_process_group_after_timeout(self):
        from general_ludd.commands.make import MakeRunner

        process = MagicMock(
            stdout=io.StringIO("partial"),
            stderr=io.StringIO(""),
            returncode=-15,
        )
        process.wait.side_effect = [subprocess.TimeoutExpired(["make", "test"], 1), None]
        with (
            patch("general_ludd.commands.make.subprocess.Popen", return_value=process),
            patch.object(MakeRunner, "_kill_group") as kill_group,
        ):
            result = MakeRunner().run("test", timeout_s=1)

        assert result.success is False
        assert result.timed_out is True
        assert result.oom_killed is False
        assert result.stdout_tail == "partial"
        kill_group.assert_called_once_with(process)
        assert process.wait.call_args_list == [call(timeout=1), call(timeout=10)]

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (FileNotFoundError(), "make executable not found"),
            (OSError("quota"), "spawn failed: quota"),
        ],
    )
    def test_run_returns_structured_process_creation_error(self, error, message):
        from general_ludd.commands.make import MakeRunner

        with patch("general_ludd.commands.make.subprocess.Popen", side_effect=error):
            result = MakeRunner().run("test")

        assert result.success is False
        assert result.exit_code is None
        assert result.error == message
        assert result.duration_s >= 0

    def test_run_delegates_to_streaming_mode_when_callback_present(self):
        from general_ludd.commands.make import MakeResult, MakeRunner

        process = MagicMock()
        callback = MagicMock()
        streamed = MakeResult("gate", 0, True, 0.1)
        runner = MakeRunner()
        with (
            patch("general_ludd.commands.make.subprocess.Popen", return_value=process),
            patch.object(runner, "_run_stream", return_value=streamed) as run_stream,
        ):
            result = runner.run(
                "gate",
                timeout_s=9,
                stream=True,
                stream_callback=callback,
            )

        assert result is streamed
        run_stream.assert_called_once()
        assert run_stream.call_args.args[0] is process
        assert run_stream.call_args.args[1] == ["make", "gate"]
        assert run_stream.call_args.args[2] == "gate"
        assert run_stream.call_args.args[4:] == (9, callback)


class TestMakeRunnerStreaming:
    def test_run_stream_reports_each_phase_once_and_completion(self):
        from general_ludd.commands.make import MakeRunner

        process = MagicMock(returncode=0)
        process.poll.side_effect = [None, 0]
        reader = MagicMock(text="=== LINT ===\n=== LINT ===\n=== TEST ===\n")
        callback = MagicMock()
        runner = MakeRunner()

        with (
            patch("general_ludd.commands.make._BoundedReader", return_value=reader),
            patch("general_ludd.commands.make.time.monotonic", return_value=10.5),
            patch("general_ludd.commands.make.time.sleep"),
        ):
            result = runner._run_stream(
                process,
                ["make", "gate"],
                "gate",
                10.0,
                30,
                callback,
            )

        assert result.success is True
        assert result.stdout_tail == "=== LINT ===\n=== LINT ===\n=== TEST ===\n"
        assert result.phases == ["LINT", "LINT", "TEST"]
        assert callback.call_args_list == [
            call("LINT"),
            call("TEST"),
            call("=== COMPLETE ==="),
        ]
        reader.start.assert_called_once_with()
        reader.join.assert_called_once_with(timeout=10)

    def test_run_stream_times_out_and_terminates_process_group(self):
        from general_ludd.commands.make import MakeRunner

        process = MagicMock(returncode=-15)
        process.poll.return_value = None
        reader = MagicMock(text="=== COLLECT ===\npartial")
        callback = MagicMock()
        runner = MakeRunner()

        with (
            patch("general_ludd.commands.make._BoundedReader", return_value=reader),
            patch("general_ludd.commands.make.time.monotonic", return_value=12.0),
            patch.object(runner, "_kill_group") as kill_group,
        ):
            result = runner._run_stream(
                process,
                ["make", "test"],
                "test",
                10.0,
                1,
                callback,
            )

        assert result.success is False
        assert result.timed_out is True
        assert result.duration_s == 2.0
        assert result.stdout_tail == "=== COLLECT ===\npartial"
        kill_group.assert_called_once_with(process)
        reader.join.assert_called_once_with(timeout=10)
        callback.assert_not_called()


class TestMakeRunnerConvenienceMethods:
    def test_methods_route_to_expected_make_targets_and_timeouts(self):
        from general_ludd.commands.make import MakeResult, MakeRunner

        result = MakeResult("test", 0, True, 0.0)
        runner = MakeRunner()
        with patch.object(runner, "run", return_value=result) as run:
            assert runner.run_test() is result
            assert runner.run_test("tests/unit/test_example.py", timeout_s=11) is result
            assert runner.run_gate() is result
            assert runner.run_gate(timeout_s=12) is result
            assert runner.run_lint() is result
            assert runner.run_lint(timeout_s=13) is result
            assert runner.run_typecheck() is result
            assert runner.run_typecheck(timeout_s=14) is result
            assert runner.run_specific("tests/unit/test_example.py", timeout_s=15) is result

        assert run.call_args_list == [
            call("test", timeout_s=None),
            call(
                "test",
                extra_args=["TESTFILE=tests/unit/test_example.py"],
                timeout_s=11,
            ),
            call("gate", timeout_s=3600),
            call("gate", timeout_s=12),
            call("lint", timeout_s=180),
            call("lint", timeout_s=13),
            call("typecheck", timeout_s=300),
            call("typecheck", timeout_s=14),
            call(
                "test-specific",
                extra_args=["TESTFILE=tests/unit/test_example.py"],
                timeout_s=15,
            ),
        ]


class TestMakeRunnerProcessTermination:
    def test_kill_group_stops_after_successful_sigterm(self):
        from general_ludd.commands.make import MakeRunner

        process = MagicMock(pid=41)
        with (
            patch("general_ludd.commands.make.os.getpgid", return_value=99),
            patch("general_ludd.commands.make.os.killpg") as killpg,
        ):
            MakeRunner._kill_group(process)

        killpg.assert_called_once_with(99, signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=5)

    def test_kill_group_escalates_to_sigkill_after_grace_period(self):
        from general_ludd.commands.make import MakeRunner

        process = MagicMock(pid=41)
        process.wait.side_effect = subprocess.TimeoutExpired(["make", "test"], 5)
        with (
            patch("general_ludd.commands.make.os.getpgid", return_value=99),
            patch("general_ludd.commands.make.os.killpg") as killpg,
        ):
            MakeRunner._kill_group(process)

        assert killpg.call_args_list == [
            call(99, signal.SIGTERM),
            call(99, signal.SIGKILL),
        ]
        process.wait.assert_called_once_with(timeout=5)

    def test_kill_group_tolerates_already_exited_process(self):
        from general_ludd.commands.make import MakeRunner

        process = MagicMock(pid=41)
        with (
            patch("general_ludd.commands.make.os.getpgid", return_value=99),
            patch(
                "general_ludd.commands.make.os.killpg",
                side_effect=ProcessLookupError,
            ) as killpg,
        ):
            MakeRunner._kill_group(process)

        killpg.assert_called_once_with(99, signal.SIGTERM)
        process.wait.assert_not_called()
