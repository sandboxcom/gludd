from __future__ import annotations

import subprocess
from unittest.mock import patch

from general_ludd.git_automation.repo import GitAutomation


def _ok(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        ["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestMergeBranchLock:
    """C.17: merge_branch holds the per-repo lock for the entire
    checkout+merge(+squash) sequence — not acquire/release per-invocation."""

    def test_merge_holds_lock_continuously(self):
        auto = GitAutomation(".")
        lock_held = False
        calls_in_lock = 0

        class FakeLock:
            def __enter__(self):
                nonlocal lock_held
                lock_held = True
                return None

            def __exit__(self, *args):
                nonlocal lock_held
                lock_held = False

        def tracking_run_git(*args, _cwd=None, check=True):
            nonlocal calls_in_lock
            if lock_held:
                calls_in_lock += 1
            return _ok(stdout="merged")

        with patch.object(auto, "_run_git", side_effect=tracking_run_git), patch(
            "general_ludd.git_automation.repo.git_repo_lock",
            return_value=FakeLock(),
        ):
            result = auto.merge_branch("/repo", "feat", "main", "ff")
            assert result.success is True
            assert calls_in_lock == 2

    def test_merge_lock_called_once(self):
        auto = GitAutomation(".")
        with patch.object(auto, "_run_git", return_value=_ok(stdout="merged")), patch(
            "general_ludd.git_automation.repo.git_repo_lock"
        ) as mock_lock:
            mock_lock.return_value.__enter__.return_value = None
            auto.merge_branch("/repo", "feat", "main", "ff")
            assert mock_lock.call_count == 1


class TestSquashPathNotFailOpen:
    """C.17: a squash-commit failure must produce success=False — never
    silently succeed (fail-open). Covered: CalledProcessError from check=True
    AND the redundant explicit returncode guard at line 646."""

    def test_squash_commit_called_process_error_returns_failure(self):
        auto = GitAutomation(".")
        with patch.object(auto, "_run_git") as mock_run:
            mock_run.side_effect = [
                _ok(),  # checkout target
                _ok(stdout="Squash commit -- not updating HEAD"),  # merge --squash
                subprocess.CalledProcessError(
                    1, "git commit", stderr="squash commit failed: index.lock"
                ),
            ]
            result = auto.merge_branch("/repo", "feat", "main", "squash")
            assert result.success is False
            assert result.strategy == "squash"
            assert "index.lock" in result.message

    def test_squash_returncode_nonzero_detected(self):
        """Redundant guard at line 646: a non-zero returncode that leaks past
        check=True must still fail closed."""
        auto = GitAutomation(".")
        with patch.object(auto, "_run_git") as mock_run:
            mock_run.side_effect = [
                _ok(),  # checkout
                _ok(stdout="Squash commit -- not updating HEAD"),  # merge --squash
                _ok(returncode=1, stderr="commit hook rejected"),  # non-zero, no raise
            ]
            result = auto.merge_branch("/repo", "feat", "main", "squash")
            assert result.success is False
            assert result.strategy == "squash"
            assert "commit hook rejected" in result.message


class TestBranchNameCollision:
    """C.17: create_branch must detect duplicate branch names and reject them
    BEFORE checkout -b runs."""

    def test_rejects_existing_branch_name(self):
        auto = GitAutomation(".")
        with patch.object(auto, "_run_git") as mock_run:
            mock_run.return_value = _ok(stdout="main\nfeature-x\nbugfix\n")
            try:
                auto.create_branch("feature-x")
                raise AssertionError("expected ValueError")
            except ValueError as exc:
                assert "'feature-x'" in str(exc)
                assert "already exists" in str(exc)

    def test_allows_unique_branch_name(self):
        auto = GitAutomation(".")
        with patch.object(auto, "_run_git") as mock_run:
            mock_run.side_effect = [
                _ok(stdout="main\n"),  # branch list
                _ok(),  # checkout -b
            ]
            result = auto.create_branch("feature-new")
            assert result == "feature-new"

    def test_rejects_leading_dash_branch(self):
        auto = GitAutomation(".")
        with patch.object(auto, "_run_git"):
            try:
                auto.create_branch("--option-injection")
                raise AssertionError("expected ValueError")
            except ValueError as exc:
                assert "begins with '-'" in str(exc)

    def test_collision_detection_runs_before_checkout(self):
        auto = GitAutomation(".")
        with patch.object(auto, "_run_git") as mock_run:
            mock_run.return_value = _ok(stdout="main\ndev\n")
            try:
                auto.create_branch("dev")
                raise AssertionError("expected ValueError")
            except ValueError:
                assert mock_run.call_count == 1  # only branch list, no checkout
