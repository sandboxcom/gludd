"""Tests for BackgroundTestRunner - background test execution and polling."""

from __future__ import annotations

import json
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.runner.background_test_runner import BackgroundTestRunner


class TestBackgroundTestRunnerLaunch:
    """Tests for launching background tests."""

    def test_launch_creates_pid_file(self, tmp_path: Path) -> None:
        """Launch creates a PID file in status directory."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            runner.launch(testfile, timeout_min=1)

        pid_files = list(tmp_path.glob(".test-*.pid"))
        assert len(pid_files) == 1
        assert pid_files[0].read_text().strip() == "12345"

    def test_launch_creates_status_json_with_required_fields(self, tmp_path: Path) -> None:
        """Launch creates status JSON with pid, testfile, start_time, phase, exit_code, log_file."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            runner.launch(testfile, timeout_min=1)

        status_files = list(tmp_path.glob(".test-*.status.json"))
        assert len(status_files) == 1

        status = json.loads(status_files[0].read_text())
        assert status["pid"] == 12345
        assert status["testfile"] == testfile
        assert "start_time" in status
        assert status["phase"] == "running"
        assert status["exit_code"] is None
        assert "log_file" in status
        assert "terminal_marker" in status

    def test_launch_returns_already_running_if_pid_alive(self, tmp_path: Path) -> None:
        """Launch returns already_running if test is already running."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("99999")

        with patch.object(runner, "_pid_alive", return_value=True):
            result = runner.launch(testfile)

        assert result["status"] == "already_running"
        assert result["pid"] == 99999

    def test_launch_runs_make_test_specific_command(self, tmp_path: Path) -> None:
        """Launch runs 'make test-specific TESTFILE=<file>' command."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            runner.launch(testfile)

        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        assert call_args == ["make", "test-specific", f"TESTFILE={testfile}"]


class TestBackgroundTestRunnerStatus:
    """Tests for checking background test status."""

    def test_status_returns_running_when_pid_alive(self, tmp_path: Path) -> None:
        """Status returns RUNNING phase when PID is alive."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        status_path = tmp_path / ".test-tests_unit_test_example_py.status.json"
        status_path.write_text(json.dumps({"pid": 12345, "testfile": testfile, "phase": "running"}))

        with patch.object(runner, "_pid_alive", return_value=True):
            result = runner.status(testfile)

        assert result["phase"] == "running"
        assert result["alive"] is True
        assert result["pid"] == 12345

    def test_status_returns_completed_when_pid_dead(self, tmp_path: Path) -> None:
        """Status returns COMPLETED phase when PID is dead."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        status_path = tmp_path / ".test-tests_unit_test_example_py.status.json"
        status_path.write_text(json.dumps({"pid": 12345, "testfile": testfile, "phase": "running"}))

        log_file = tmp_path / "test-tests_unit_test_example_py-20240101-120000.log"
        log_file.write_text("=== PASSED ===\n5 passed in 1.23s")

        with patch.object(runner, "_pid_alive", return_value=False):
            result = runner.status(testfile)

        assert result["phase"] == "completed"
        assert result["alive"] is False
        assert result["terminal_marker"] == "PASS"

    def test_status_detects_fail_terminal_marker(self, tmp_path: Path) -> None:
        """Status detects FAIL terminal marker in log."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        log_file = tmp_path / "test-tests_unit_test_example_py-20240101-120000.log"
        log_file.write_text("=== FAILED ===\n2 failed, 3 passed in 1.23s")

        with patch.object(runner, "_pid_alive", return_value=False):
            result = runner.status(testfile)

        assert result["terminal_marker"] == "FAIL"

    def test_status_includes_last_lines_from_log(self, tmp_path: Path) -> None:
        """Status includes last N lines from log file."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        log_file = tmp_path / "test-tests_unit_test_example_py-20240101-120000.log"
        log_content = "\n".join([f"line {i}" for i in range(20)])
        log_file.write_text(log_content)

        with patch.object(runner, "_pid_alive", return_value=False):
            result = runner.status(testfile)

        assert "last_lines" in result
        # Should include last few lines


class TestBackgroundTestRunnerPollAll:
    """Tests for polling all background tests."""

    def test_poll_all_returns_list_of_all_tracked_tests(self, tmp_path: Path) -> None:
        """poll_all returns status for every tracked test."""
        runner = BackgroundTestRunner(status_dir=tmp_path)

        # Create two test PID files
        for i, testfile in enumerate(["tests/unit/test_a.py", "tests/unit/test_b.py"]):
            pid_path = tmp_path / f".test-tests_unit_test_{testfile[-5]}.py.pid"
            pid_path.write_text(f"1234{i}")
            status_path = tmp_path / f".test-tests_unit_test_{testfile[-5]}.py.status.json"
            status_path.write_text(json.dumps({"pid": 12340 + i, "testfile": testfile, "phase": "running"}))

        with patch.object(runner, "_pid_alive", return_value=True):
            results = runner.poll_all()

        assert len(results) == 2
        testfiles = {r["testfile"] for r in results}
        assert testfiles == {"tests/unit/test_a.py", "tests/unit/test_b.py"}

    def test_poll_all_returns_empty_list_when_no_tests(self, tmp_path: Path) -> None:
        """poll_all returns empty list when no PID files exist."""
        runner = BackgroundTestRunner(status_dir=tmp_path)

        results = runner.poll_all()

        assert results == []


class TestBackgroundTestRunnerKill:
    """Tests for killing background tests."""

    def test_kill_sends_sigterm_then_sigkill(self, tmp_path: Path) -> None:
        """Kill sends SIGTERM, waits 5s, then sends SIGKILL."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        with patch.object(
            runner, "_pid_alive",
            side_effect=[True, True, True, True, True, True, False],
        ), patch("os.kill") as mock_kill:
            result = runner.kill(testfile, force=True)

        assert mock_kill.call_count == 2
        mock_kill.assert_any_call(12345, signal.SIGTERM)
        mock_kill.assert_any_call(12345, signal.SIGKILL)
        assert result["status"] == "killed"
        assert not pid_path.exists()

    def test_kill_returns_already_dead_if_pid_not_alive(self, tmp_path: Path) -> None:
        """Kill returns already_dead if process is already gone."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        with patch.object(runner, "_pid_alive", return_value=False):
            result = runner.kill(testfile)

        assert result["status"] == "already_dead"
        assert not pid_path.exists()

    def test_kill_returns_no_pid_file_if_missing(self, tmp_path: Path) -> None:
        """Kill returns no_pid_file if PID file doesn't exist."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        result = runner.kill(testfile)

        assert result["status"] == "no_pid_file"


class TestBackgroundTestRunnerTailLog:
    """Tests for tailing log files."""

    def test_tail_log_returns_last_n_lines(self, tmp_path: Path) -> None:
        """Tail returns last N lines from log file."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        log_file = tmp_path / "test-tests_unit_test_example_py-20240101-120000.log"
        log_content = "\n".join([f"line {i}" for i in range(100)])
        log_file.write_text(log_content)

        result = runner.tail_log(testfile, n=10)

        lines = result.strip().split("\n")
        assert len(lines) == 10
        assert lines[0] == "line 90"
        assert lines[-1] == "line 99"

    def test_tail_log_returns_empty_if_no_log(self, tmp_path: Path) -> None:
        """Tail returns empty string if no log file exists."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_nonexistent.py"

        result = runner.tail_log(testfile, n=10)

        assert result == ""


class TestBackgroundTestRunnerResults:
    """Tests for getting final results."""

    def test_results_returns_complete_when_test_finished(self, tmp_path: Path) -> None:
        """Results returns complete=True with pass/fail when test finished."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        status_path = tmp_path / ".test-tests_unit_test_example_py.status.json"
        status_path.write_text(json.dumps({"pid": 12345, "testfile": testfile, "phase": "running"}))

        log_file = tmp_path / "test-tests_unit_test_example_py-20240101-120000.log"
        log_file.write_text("=== PASSED ===\n5 passed in 1.23s")

        with patch.object(runner, "_pid_alive", return_value=False):
            result = runner.results(testfile)

        assert result["complete"] is True
        assert result["passed"] is True
        assert result["phase"] == "completed"

    def test_results_returns_running_when_test_still_alive(self, tmp_path: Path) -> None:
        """Results returns complete=False when test still running."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        with patch.object(runner, "_pid_alive", return_value=True):
            result = runner.results(testfile)

        assert result["complete"] is False
        assert result["phase"] == "running"


class TestBackgroundTestRunnerWait:
    """Tests for blocking wait."""

    def test_wait_blocks_until_test_completes(self, tmp_path: Path) -> None:
        """Wait blocks and returns results when test completes."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        log_file = tmp_path / "test-tests_unit_test_example_py-20240101-120000.log"
        log_file.write_text("=== PASSED ===\n5 passed in 1.23s")

        call_count = [0]

        def pid_alive_side_effect(pid):
            call_count[0] += 1
            return call_count[0] <= 2

        with patch.object(runner, "_pid_alive", side_effect=pid_alive_side_effect):
            result = runner._wait(testfile, timeout_min=1, poll_interval=0)

        assert result["complete"] is True
        assert result["passed"] is True

    def test_wait_returns_timeout_if_deadline_exceeded(self, tmp_path: Path) -> None:
        """Wait returns timeout if deadline exceeded."""
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        pid_path = tmp_path / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        with patch.object(runner, "_pid_alive", return_value=True):
            result = runner._wait(testfile, timeout_min=0, poll_interval=0)

        assert result["phase"] == "timeout"
        assert result["complete"] is False


class TestBackgroundTestRunnerIntegration:
    """Integration tests with gate-background system."""

    def test_uses_same_gate_logs_directory(self, tmp_path: Path) -> None:
        """BackgroundTestRunner uses .gate-logs directory by default."""
        runner = BackgroundTestRunner()

        assert runner.status_dir == Path(".gate-logs")

    def test_status_files_shared_with_gate_background(self, tmp_path: Path) -> None:
        """Status files coexist with gate-background logs in same directory."""
        gate_log_dir = tmp_path / ".gate-logs"
        gate_log_dir.mkdir()

        # Create gate log
        gate_log = gate_log_dir / "gate-20240101-120000.log"
        gate_log.write_text("=== GATE: PASSED ===")

        # Create test status files
        runner = BackgroundTestRunner(status_dir=gate_log_dir)
        testfile = "tests/unit/test_example.py"

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            runner.launch(testfile)

        # Both gate log and test status files exist
        gate_logs = list(gate_log_dir.glob("gate-*.log"))
        test_statuses = list(gate_log_dir.glob(".test-*.status.json"))
        test_pids = list(gate_log_dir.glob(".test-*.pid"))

        assert len(gate_logs) == 1
        assert len(test_statuses) == 1
        assert len(test_pids) == 1

    def test_poll_all_includes_gate_background_if_running(self, tmp_path: Path) -> None:
        """poll_all only returns test entries, not gate entries."""
        gate_log_dir = tmp_path / ".gate-logs"
        gate_log_dir.mkdir()

        runner = BackgroundTestRunner(status_dir=gate_log_dir)
        testfile = "tests/unit/test_example.py"

        pid_path = gate_log_dir / ".test-tests_unit_test_example_py.pid"
        pid_path.write_text("12345")

        with patch.object(runner, "_pid_alive", return_value=True):
            results = runner.poll_all()

        assert len(results) == 1
        assert results[0]["testfile"] == testfile


class TestBackgroundTestRunnerSanitize:
    """Tests for filename sanitization."""

    def test_sanitize_replaces_special_chars(self) -> None:
        """Sanitize replaces special characters with underscore."""
        runner = BackgroundTestRunner()
        assert runner._sanitize("tests/unit/test_foo.py") == "tests_unit_test_foo_py"
        assert runner._sanitize("tests::unit::test.py") == "tests_unit_test_py"
        assert runner._sanitize("test@file#name.py") == "test_file_name_py"


class TestBackgroundTestRunnerDetectTerminalMarker:
    """Tests for terminal marker detection."""

    def test_detect_pass_from_pytest_summary(self) -> None:
        """Detects PASS from pytest passed summary."""
        runner = BackgroundTestRunner()
        assert runner._detect_terminal_marker("5 passed in 1.23s") == "PASS"
        assert runner._detect_terminal_marker("=== PASSED ===") == "PASS"

    def test_detect_fail_from_pytest_summary(self) -> None:
        """Detects FAIL from pytest failed/error summary."""
        runner = BackgroundTestRunner()
        assert runner._detect_terminal_marker("2 failed, 3 passed in 1.23s") == "FAIL"
        assert runner._detect_terminal_marker("=== FAILED ===") == "FAIL"
        assert runner._detect_terminal_marker("1 error in 0.5s") == "FAIL"

    def test_detect_none_when_inconclusive(self) -> None:
        """Returns None when no terminal marker found."""
        runner = BackgroundTestRunner()
        assert runner._detect_terminal_marker("test running...") is None
        assert runner._detect_terminal_marker("") is None


class TestBackgroundTestRunnerParseExitCode:
    """Tests for exit code parsing."""

    def test_parse_exit_code_from_log(self) -> None:
        """Parses exit code from log text."""
        runner = BackgroundTestRunner()
        assert runner._parse_exit_code("exit code 1") == 1
        assert runner._parse_exit_code("PYTEST_EXIT=2") == 2

    def test_parse_exit_code_returns_none_when_missing(self) -> None:
        """Returns None when no exit code found."""
        runner = BackgroundTestRunner()
        assert runner._parse_exit_code("test running...") is None
        assert runner._parse_exit_code("") is None


# Pytest fixtures
@pytest.fixture
def tmp_path(tmp_path: Path) -> Path:
    """Provide a temporary directory for tests."""
    return tmp_path
