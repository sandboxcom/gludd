"""TDD tests for H.13 — H-ORNITH-SANDBOX-GAPS.

Covers: arbitrary file-write sandbox for coding-agent subprocess,
filesystem confinement for export out_path (extending existing coverage).
"""

import os
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.ornith.sandbox import (
    _sandbox_preexec_fn,
    confine_export_path,
    create_ornith_sandbox,
    ornith_sandbox_preexec,
    ornith_sandboxed_run,
)


class TestOrnithSandboxFilesystem:
    def test_sandbox_temp_dir_exists_and_is_writable(self):
        sb = create_ornith_sandbox()
        assert sb.temp_dir.exists()
        assert sb.temp_dir.is_dir()
        test_file = sb.temp_dir / "test.txt"
        test_file.write_text("hello")
        assert test_file.read_text() == "hello"

    def test_sandbox_cleanup_removes_temp_dir(self):
        sb = create_ornith_sandbox()
        tmp = sb.temp_dir
        assert tmp.exists()
        sb.cleanup()
        assert not tmp.exists()

    def test_sandbox_temp_dir_is_within_allowed_root(self):
        sb = create_ornith_sandbox()
        resolved = os.path.realpath(str(sb.temp_dir))
        assert resolved.startswith(
            os.path.realpath(tempfile.gettempdir())
        ) or resolved.startswith(os.path.realpath(os.getcwd()))

    def test_sandbox_context_manager_cleans_up(self):
        with create_ornith_sandbox() as sb:
            tmp = sb.temp_dir
            assert tmp.exists()
        assert not tmp.exists()

    def test_sandbox_context_manager_on_exception(self):
        try:
            with create_ornith_sandbox() as sb:
                tmp = sb.temp_dir
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert not tmp.exists()


class TestOrnithSandboxedRun:
    def test_sandboxed_run_captures_output(self):
        result = ornith_sandboxed_run(
            ["python3", "-c", "print('hello from sandbox')"],
            timeout=10,
        )
        assert "hello from sandbox" in result["stdout"]
        assert result["returncode"] == 0

    def test_sandboxed_run_rlimits_are_applied(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "general_ludd.system.rlimit.apply_limits"
        ) as mock_apply:
                _sandbox_preexec_fn(512, 30, td)
                mock_apply.assert_called_once_with(512, 30)

    def test_sandboxed_run_passes_extra_env(self):
        result = ornith_sandboxed_run(
            ["python3", "-c", "import os; print(os.environ.get('H13_TEST', 'missing'))"],
            timeout=10,
            env={"H13_TEST": "h13-value"},
        )
        assert "h13-value" in result["stdout"]

    def test_sandboxed_run_writes_are_confined_to_temp_dir(self):
        script = textwrap.dedent("""\
        import os
        p = os.path.join(os.getcwd(), "h13_created.txt")
        with open(p, "w") as f:
            f.write("sandbox-file")
        print("OK", os.path.realpath(p))
        """)
        result = ornith_sandboxed_run(
            ["python3", "-c", script],
            timeout=10,
        )
        assert "OK" in result["stdout"]
        assert result["returncode"] == 0
        real_path = result["stdout"].split("OK ", 1)[1].strip()
        assert tempfile.gettempdir() in real_path

    def test_sandboxed_run_cwd_is_temp_dir(self):
        result = ornith_sandboxed_run(
            ["python3", "-c", "import os; print(os.getcwd())"],
            timeout=10,
        )
        cwd = result["stdout"].strip()
        assert "ornith-sandbox-" in cwd, f"expected sandbox prefix in CWD, got: {cwd!r}"

    def test_sandboxed_run_failed_process_returns_errors(self):
        result = ornith_sandboxed_run(
            ["python3", "-c", "import sys; sys.exit(42)"],
            timeout=10,
        )
        assert result["returncode"] == 42

    def test_sandboxed_run_timeout_is_honored(self):
        result = ornith_sandboxed_run(
            ["python3", "-c", "import time; time.sleep(999)"],
            timeout=2,
        )
        assert result["returncode"] != 0

    def test_sandboxed_run_stderr_is_captured(self):
        result = ornith_sandboxed_run(
            ["python3", "-c", "import sys; sys.stderr.write('h13-stderr')"],
            timeout=10,
        )
        assert "h13-stderr" in result["stderr"]

    def test_sandboxed_run_binary_not_found(self):
        result = ornith_sandboxed_run(
            ["nonexistent_binary_h13_xyz"],
            timeout=10,
        )
        assert result["returncode"] != 0


class TestOutPathConfinementExtended:
    def test_out_path_with_null_byte_is_blocked(self):
        with patch(
            "general_ludd.ornith.sandbox._ALLOWED_EXPORT_ROOTS", ["/tmp"]
        ), pytest.raises(ValueError):
            confine_export_path("/tmp/ok\x00hidden.jsonl", "fallback.jsonl")

    def test_out_path_relative_dot_slash_resolves(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "general_ludd.ornith.sandbox._ORNITH_EXPORT_ROOT", td
        ), patch(
            "general_ludd.ornith.sandbox._ALLOWED_EXPORT_ROOTS", [td]
        ):
            result = confine_export_path("./output.jsonl", "fallback.jsonl")
            assert result == Path(os.path.realpath(td)) / "output.jsonl"


class TestOrnithSandboxSubprocessPreexec:
    def test_ornith_sandbox_preexec_applies_both_limits(self):
        with patch("general_ludd.system.rlimit.apply_limits") as mock_apply:
            ornith_sandbox_preexec()
            mock_apply.assert_called_once()
            args = mock_apply.call_args[0]
            assert len(args) == 2
            assert isinstance(args[0], int)
            assert isinstance(args[1], int)

    def test_ornith_sandbox_preexec_is_callable_in_subprocess(self):
        import subprocess
        import sys

        code = textwrap.dedent("""\
        from general_ludd.ornith.sandbox import ornith_sandbox_preexec
        ornith_sandbox_preexec()
        print("OK")
        """)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "OK" in proc.stdout
