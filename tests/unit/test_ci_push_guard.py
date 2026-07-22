"""Unit tests for ci_push_guard.py — CI busy-check logic.

Verifies all 8 test scenarios outlined in the enhancement spec:
- CI busy (active) → exit 1
- CI idle (no active) → exit 0
- FORCE=1 bypasses busy check → exit 0
- gh CLI unavailable → fail-open → exit 0
- gh returns unexpected JSON → fail-open → exit 0
- Timeout on gh call → fail-open → exit 0
- Multiple active runs → exit 1
- Only completed runs → exit 0
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from ci_push_guard import _gh_run_list, ci_busy_check


class TestGhRunList:
    def test_returns_empty_on_gh_unavailable(self, monkeypatch):
        def mock_run(*args, **kwargs):
            raise FileNotFoundError("gh not found")
        monkeypatch.setattr(subprocess, "run", mock_run)
        assert _gh_run_list("master") == []

    def test_returns_empty_on_timeout(self, monkeypatch):
        def mock_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["gh", "run", "list"], timeout=15)
        monkeypatch.setattr(subprocess, "run", mock_run)
        assert _gh_run_list("master") == []

    def test_returns_empty_on_nonzero_return(self, monkeypatch):
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="gh error")
        monkeypatch.setattr(subprocess, "run", mock_run)
        assert _gh_run_list("master") == []

    def test_returns_empty_on_empty_json(self, monkeypatch):
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        monkeypatch.setattr(subprocess, "run", mock_run)
        assert _gh_run_list("master") == []

    def test_returns_parsed_run_list(self, monkeypatch):
        output = json.dumps([{"databaseId": 12345, "status": "in_progress", "conclusion": None}])
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")
        monkeypatch.setattr(subprocess, "run", mock_run)
        runs = _gh_run_list("master")
        assert len(runs) == 1
        assert runs[0]["databaseId"] == 12345
        assert runs[0]["status"] == "in_progress"

    def test_returns_empty_on_bad_json(self, monkeypatch):
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="not-json{", stderr="")
        monkeypatch.setattr(subprocess, "run", mock_run)
        assert _gh_run_list("master") == []

    def test_returns_multiple_active_runs(self, monkeypatch):
        output = json.dumps([
            {"databaseId": 1, "status": "in_progress", "conclusion": None},
            {"databaseId": 2, "status": "queued", "conclusion": None},
        ])
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")
        monkeypatch.setattr(subprocess, "run", mock_run)
        runs = _gh_run_list("master")
        assert len(runs) == 2

    def test_returns_empty_when_gh_returns_only_completed_runs(self, monkeypatch):
        """gh run list --status in_progress --status queued --status waiting
        returns [] when all runs are completed — this is the 'CI idle' path."""
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        monkeypatch.setattr(subprocess, "run", mock_run)
        assert _gh_run_list("master") == []

    def test_respects_branch_parameter(self, monkeypatch):
        captured = []
        def mock_run(*args, **kwargs):
            captured.extend(args[0])
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        monkeypatch.setattr(subprocess, "run", mock_run)
        _gh_run_list("development")
        assert "--branch" in captured
        assert "development" in captured


class TestCiBusyCheck:
    def test_returns_idle_when_no_runs(self, monkeypatch):
        monkeypatch.setattr("ci_push_guard._gh_run_list", lambda branch: [])
        assert ci_busy_check("master") == 0

    def test_returns_busy_when_active_run_exists(self, monkeypatch):
        monkeypatch.setattr(
            "ci_push_guard._gh_run_list",
            lambda branch: [{"databaseId": 9999, "status": "in_progress", "conclusion": None}],
        )
        assert ci_busy_check("master") == 1

    def test_returns_busy_when_queued_run_exists(self, monkeypatch):
        monkeypatch.setattr(
            "ci_push_guard._gh_run_list",
            lambda branch: [{"databaseId": 8888, "status": "queued", "conclusion": None}],
        )
        assert ci_busy_check("master") == 1

    def test_returns_busy_when_waiting_run_exists(self, monkeypatch):
        monkeypatch.setattr(
            "ci_push_guard._gh_run_list",
            lambda branch: [{"databaseId": 7777, "status": "waiting", "conclusion": None}],
        )
        assert ci_busy_check("master") == 1

    def test_force_bypasses_busy_check(self, monkeypatch):
        monkeypatch.setattr(
            "ci_push_guard._gh_run_list",
            lambda branch: [{"databaseId": 9999, "status": "in_progress", "conclusion": None}],
        )
        assert ci_busy_check("master", force=True) == 0

    def test_passes_branch_parameter(self, monkeypatch):
        captured_branch = []
        def mock_runs(branch):
            captured_branch.append(branch)
            return []
        monkeypatch.setattr("ci_push_guard._gh_run_list", mock_runs)
        ci_busy_check("development")
        assert captured_branch == ["development"]

    def test_multiple_active_runs_returns_busy(self, monkeypatch):
        monkeypatch.setattr(
            "ci_push_guard._gh_run_list",
            lambda branch: [
                {"databaseId": 1, "status": "in_progress", "conclusion": None},
                {"databaseId": 2, "status": "queued", "conclusion": None},
            ],
        )
        assert ci_busy_check("master") == 1


def test_gh_query_fetches_recent_branch_runs_without_status_filters(monkeypatch):
    captured_cmd = []

    def mock_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)
    _gh_run_list("development")

    assert captured_cmd.count("--status") == 0
    assert "--branch" in captured_cmd
    assert "development" in captured_cmd
    limit_index = captured_cmd.index("--limit")
    assert int(captured_cmd[limit_index + 1]) >= 20


def test_gh_run_list_filters_mixed_statuses_in_python(monkeypatch):
    output = json.dumps([
        {"databaseId": 1, "status": "completed", "conclusion": "failure"},
        {"databaseId": 2, "status": "in_progress", "conclusion": None},
        {"databaseId": 3, "status": "queued", "conclusion": None},
        {"databaseId": 4, "status": "waiting", "conclusion": None},
    ])

    def mock_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)
    runs = _gh_run_list("master")

    assert [run["databaseId"] for run in runs] == [2, 3, 4]
