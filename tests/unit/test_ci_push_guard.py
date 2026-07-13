"""Unit tests for ci_push_guard.py — CI busy-check logic."""

from __future__ import annotations

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

    def test_returns_empty_on_gh_error(self, monkeypatch):
        def mock_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["gh", "run", "list"], timeout=15)

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert _gh_run_list("master") == []

    def test_returns_empty_on_nonzero_return(self, monkeypatch):
        def mock_run(*args, **kwargs):
            r = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="gh error")
            return r

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert _gh_run_list("master") == []

    def test_returns_empty_on_empty_json(self, monkeypatch):
        def mock_run(*args, **kwargs):
            r = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            return r

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert _gh_run_list("master") == []

    def test_returns_parsed_run_list(self, monkeypatch):
        output = '[{"databaseId": 12345, "status": "in_progress", "conclusion": null}]'

        def mock_run(*args, **kwargs):
            r = subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")
            return r

        monkeypatch.setattr(subprocess, "run", mock_run)
        runs = _gh_run_list("master")
        assert len(runs) == 1
        assert runs[0]["databaseId"] == 12345
        assert runs[0]["status"] == "in_progress"


class TestCiBusyCheck:
    def test_returns_idle_when_no_runs(self, monkeypatch):
        def mock_runs(branch):
            return []

        monkeypatch.setattr("ci_push_guard._gh_run_list", mock_runs)
        assert ci_busy_check("master") == 0

    def test_returns_busy_when_active_run_exists(self, monkeypatch):
        def mock_runs(branch):
            return [{"databaseId": 9999, "status": "in_progress", "conclusion": None}]

        monkeypatch.setattr("ci_push_guard._gh_run_list", mock_runs)
        assert ci_busy_check("master") == 1

    def test_returns_busy_when_queued_run_exists(self, monkeypatch):
        def mock_runs(branch):
            return [{"databaseId": 8888, "status": "queued", "conclusion": None}]

        monkeypatch.setattr("ci_push_guard._gh_run_list", mock_runs)
        assert ci_busy_check("master") == 1

    def test_returns_busy_when_waiting_run_exists(self, monkeypatch):
        def mock_runs(branch):
            return [{"databaseId": 7777, "status": "waiting", "conclusion": None}]

        monkeypatch.setattr("ci_push_guard._gh_run_list", mock_runs)
        assert ci_busy_check("master") == 1

    def test_force_bypasses_busy_check(self, monkeypatch):
        def mock_runs(branch):
            return [{"databaseId": 9999, "status": "in_progress", "conclusion": None}]

        monkeypatch.setattr("ci_push_guard._gh_run_list", mock_runs)
        assert ci_busy_check("master", force=True) == 0

    def test_passes_branch_parameter(self, monkeypatch):
        captured_branch = []

        def mock_runs(branch):
            captured_branch.append(branch)
            return []

        monkeypatch.setattr("ci_push_guard._gh_run_list", mock_runs)
        ci_busy_check("development")
        assert captured_branch == ["development"]
