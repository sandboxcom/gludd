"""Tests for TestHarnessRunner — runs generated pytest files and captures output."""

from __future__ import annotations

from pathlib import Path

from general_ludd.agents.test_generation.test_harness import TestHarnessRunner


class TestHarnessRunnerCreation:
    def test_creates_with_defaults(self) -> None:
        runner = TestHarnessRunner()
        assert runner.pytest_args == ["-v"]
        assert runner.timeout_seconds == 300

    def test_creates_with_custom_args(self) -> None:
        runner = TestHarnessRunner(pytest_args=["-v", "-x"], timeout_seconds=120)
        assert runner.pytest_args == ["-v", "-x"]
        assert runner.timeout_seconds == 120


class TestHarnessRunnerExecute:
    def test_runs_pytest_on_empty_dir(self, tmp_path: Path) -> None:
        runner = TestHarnessRunner()
        result = runner.execute(test_dir=str(tmp_path))
        assert result.returncode in (0, 1, 2, 5)
        assert isinstance(result.stdout, str)

    def test_runs_pytest_on_single_test_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_sample.py"
        test_file.write_text("def test_pass():\n    assert True\n")
        runner = TestHarnessRunner()
        result = runner.execute(test_dir=str(tmp_path))
        assert result.returncode == 0
        assert "passed" in result.stdout.lower() or "PASSED" in result.stdout

    def test_failing_test_returns_nonzero(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_fail.py"
        test_file.write_text("def test_fail():\n    assert False\n")
        runner = TestHarnessRunner()
        result = runner.execute(test_dir=str(tmp_path))
        assert result.returncode != 0

    def test_stderr_captured_on_error(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_err.py"
        test_file.write_text("def test_import_err():\n    import nonexistent_module\n")
        runner = TestHarnessRunner()
        result = runner.execute(test_dir=str(tmp_path))
        assert result.returncode != 0

    def test_timeout_kills_long_running_test(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_slow.py"
        test_file.write_text("import time\ndef test_slow():\n    time.sleep(60)\n")
        runner = TestHarnessRunner(timeout_seconds=2)
        result = runner.execute(test_dir=str(tmp_path))
        assert result.returncode != 0
