"""Tests for ornith/sandbox.py — path confinement, sandbox lifecycle, subprocess run."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.ornith.sandbox import (
    OrnithSandbox,
    confine_export_path,
    create_ornith_sandbox,
    ornith_sandbox_preexec,
    ornith_sandboxed_run,
)


class TestConfineExportPath:
    def test_with_explicit_valid_path_in_tempdir(self):
        allowed = tempfile.gettempdir()
        with patch(
            "general_ludd.ornith.sandbox._ALLOWED_EXPORT_ROOTS", [allowed]
        ):
            result = confine_export_path(
                Path(allowed) / "exports" / "out.json",
                "default.json",
            )
        assert result.name == "out.json"
        assert result.is_relative_to(Path(allowed).resolve())

    def test_out_path_none_returns_default_in_root(self):
        root = tempfile.gettempdir()
        with patch("general_ludd.ornith.sandbox._ORNITH_EXPORT_ROOT", root):
            result = confine_export_path(None, "training.jsonl")
        assert result.name == "training.jsonl"
        assert str(result).startswith(root)

    def test_null_byte_in_path_raises(self):
        with pytest.raises(ValueError, match="null byte"):
            confine_export_path("valid\x00bad", "default.json")

    def test_escaping_path_raises(self):
        with pytest.raises(ValueError, match="not within an allowed export root"):
            confine_export_path("/etc/passwd", "default.json")


class TestOrnithSandbox:
    def test_context_manager_creates_and_cleans_up(self):
        with OrnithSandbox() as sandbox:
            assert sandbox.temp_dir.exists()
            temp_dir = sandbox.temp_dir
        assert not temp_dir.exists()

    def test_cleanup_is_idempotent(self):
        sandbox = OrnithSandbox()
        temp_dir = sandbox.temp_dir
        assert temp_dir.exists()
        sandbox.cleanup()
        assert not temp_dir.exists()
        sandbox.cleanup()

    def test_create_ornith_sandbox_factory(self):
        sandbox = create_ornith_sandbox()
        assert isinstance(sandbox, OrnithSandbox)
        assert sandbox.temp_dir.exists()
        sandbox.cleanup()

    def test_sandbox_dir_has_ornith_prefix(self):
        with OrnithSandbox() as sandbox:
            assert "ornith-sandbox-" in sandbox.temp_dir.name


class TestOrnithSandboxPreexec:
    def test_preexec_does_not_raise(self):
        with patch("general_ludd.system.rlimit.apply_limits") as mock_apply:
            ornith_sandbox_preexec()
            mock_apply.assert_called_once()


class TestOrnithSandboxedRun:
    def test_successful_run_returns_stdout(self):
        result = ornith_sandboxed_run(["echo", "hello world"], timeout=10)
        assert result["returncode"] == 0
        assert "hello world" in result["stdout"]

    def test_failing_command_returns_nonzero(self):
        result = ornith_sandboxed_run(
            ["sh", "-c", "exit 42"], timeout=10
        )
        assert result["returncode"] == 42

    def test_file_not_found_returns_stderr(self):
        result = ornith_sandboxed_run(
            ["nonexistent_binary_xyz"], timeout=5
        )
        assert result["returncode"] == -1
        assert "not found" in result["stderr"].lower()

    def test_env_is_passed_to_subprocess(self):
        result = ornith_sandboxed_run(
            ["sh", "-c", 'echo "$ORNITH_TEST_VAR"'],
            timeout=10,
            env={"ORNITH_TEST_VAR": "sandboxed"},
        )
        assert "sandboxed" in result["stdout"]

    def test_result_dict_keys(self):
        result = ornith_sandboxed_run(["echo", "test"], timeout=10)
        for key in ("stdout", "stderr", "returncode"):
            assert key in result
