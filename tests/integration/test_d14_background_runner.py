"""Integration test for D.14: background test runner full flow.

Exercises the full BackgroundTestRunner lifecycle:
launch → status → kill → status (dead), with only subprocess/os mocked.
"""

from __future__ import annotations

import json
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.runner.background_test_runner import BackgroundTestRunner


class TestBackgroundRunnerFullFlow:
    """Integration: launch → status (running) → kill → status (dead)."""

    def test_full_lifecycle_launch_status_kill_status(
        self, tmp_path: Path
    ) -> None:
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        # --- 1. Launch ---
        mock_proc = MagicMock()
        mock_proc.pid = 99999

        with patch("subprocess.Popen", return_value=mock_proc):
            result = runner.launch(testfile, timeout_min=30)

        assert result["pid"] == 99999
        assert result["testfile"] == testfile
        assert result["phase"] == "running"

        pid_file = tmp_path / ".test-tests_unit_test_example_py.pid"
        assert pid_file.exists()
        assert pid_file.read_text().strip() == "99999"

        status_file = tmp_path / ".test-tests_unit_test_example_py.status.json"
        assert status_file.exists()
        saved = json.loads(status_file.read_text())
        assert saved["pid"] == 99999
        assert saved["testfile"] == testfile

        # --- 2. Status — still running ---
        with patch.object(runner, "_pid_alive", return_value=True):
            status = runner.status(testfile)

        assert status["phase"] == "running"
        assert status["alive"] is True
        assert status["pid"] == 99999

        # --- 3. Kill ---
        with patch.object(
            runner, "_pid_alive",
            side_effect=[True, False],
        ), patch("os.kill") as mock_kill:
            kill_result = runner.kill(testfile)

        assert kill_result["status"] == "terminated"
        assert kill_result["pid"] == 99999
        mock_kill.assert_called_once_with(99999, signal.SIGTERM)
        assert not pid_file.exists()

        # --- 4. Status — dead / completed ---
        # Write PASS marker to the log file that launch created (it exists
        # because MakeRunner.spawn opens it for writing even with mocked Popen).
        logs = runner._log_paths(testfile)
        assert len(logs) >= 1, "launch should have created a log file"
        logs[0].write_text("=== PASSED ===\n5 passed in 1.23s")

        with patch.object(runner, "_pid_alive", return_value=False):
            status2 = runner.status(testfile)

        assert status2["phase"] == "completed"
        assert status2["alive"] is False
        assert status2["terminal_marker"] == "PASS"


class TestResultsAfterCompletion:
    """Integration: launch → simulate completion → results."""

    def test_results_returns_complete_after_test_finishes(
        self, tmp_path: Path
    ) -> None:
        runner = BackgroundTestRunner(status_dir=tmp_path)
        testfile = "tests/unit/test_example.py"

        # Launch
        mock_proc = MagicMock()
        mock_proc.pid = 88888

        with patch("subprocess.Popen", return_value=mock_proc):
            runner.launch(testfile, timeout_min=30)

        # Simulate completion: write PASS marker to the log launch created
        logs = runner._log_paths(testfile)
        assert len(logs) >= 1, "launch should have created a log file"
        logs[0].write_text("=== PASSED ===\n10 passed in 2.34s")

        with patch.object(runner, "_pid_alive", return_value=False):
            results = runner.results(testfile)

        assert results["complete"] is True
        assert results["passed"] is True
        assert results["phase"] == "completed"
        assert results["terminal_marker"] == "PASS"


class TestPollAllIntegration:
    """Integration: launch two tests → poll_all sees both."""

    def test_poll_all_sees_multiple_running_tests(
        self, tmp_path: Path
    ) -> None:
        runner = BackgroundTestRunner(status_dir=tmp_path)

        mock_proc_a = MagicMock()
        mock_proc_a.pid = 11111
        mock_proc_b = MagicMock()
        mock_proc_b.pid = 22222

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = [mock_proc_a, mock_proc_b]
            runner.launch("tests/unit/test_a.py", timeout_min=30)
            runner.launch("tests/unit/test_b.py", timeout_min=30)

        with patch.object(runner, "_pid_alive", return_value=True):
            all_results = runner.poll_all()

        assert len(all_results) == 2
        testfiles = {r["testfile"] for r in all_results}
        assert testfiles == {"tests/unit/test_a.py", "tests/unit/test_b.py"}
        for r in all_results:
            assert r["phase"] == "running"
            assert r["alive"] is True


class TestRejectBadPathsIntegration:
    """Integration: launch rejects unsafe paths."""

    @pytest.mark.parametrize("bad_path", [
        "../etc/passwd",
        "/etc/passwd",
        "test;echo hacked",
    ])
    def test_launch_rejects_unsafe_path(self, tmp_path: Path, bad_path: str) -> None:
        runner = BackgroundTestRunner(status_dir=tmp_path)
        with pytest.raises(ValueError):
            runner.launch(bad_path, timeout_min=30)
