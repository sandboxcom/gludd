"""Structural tests for commands/make.py — structured subprocess runner for make targets."""

from __future__ import annotations

import threading

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
