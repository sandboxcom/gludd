"""Tests for ship_commit and test_and_commit in git_automation.

Enforces the contracts codified in the Makefile's ship-commit, test-and-commit,
git-commit, commit-no-verify, repo-commit, and git-commit-file targets.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.git_automation.ship_commit import (
    ShipCommitError,
    collect_check,
    gate_is_green,
    ship_commit,
)
from general_ludd.git_automation.ship_commit import (
    test_and_commit as _test_and_commit,
)


def _touch(path: str, content: str = "") -> None:
    with open(path, "w") as f:
        f.write(content)


class TestCollectCheck:
    def test_passes_when_collection_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            tests_dir = os.path.join(d, "tests")
            os.mkdir(tests_dir)
            _touch(os.path.join(tests_dir, "__init__.py"))
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                collect_check(d)
            mock_run.assert_called_once()

    def test_raises_when_collection_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tests_dir = os.path.join(d, "tests")
            os.mkdir(tests_dir)
            _touch(os.path.join(tests_dir, "__init__.py"))
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 1
                mock_run.return_value.stderr = "ImportError"
                with pytest.raises(ShipCommitError):
                    collect_check(d)


class TestGateIsGreen:
    def test_missing_gate_status_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            assert gate_is_green(os.path.join(d, ".gate-status")) is False

    def test_gate_passed_returns_true(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".gate-status")
            _touch(p, "=== GATE: PASSED ===\nlint PASS\ntypecheck PASS\n")
            assert gate_is_green(p) is True

    def test_gate_failed_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".gate-status")
            _touch(p, "=== GATE: FAILED ===\nlint PASS\ntypecheck FAIL\n")
            assert gate_is_green(p) is False

    def test_incomplete_gate_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".gate-status")
            _touch(p, "lint PASS\n")
            assert gate_is_green(p) is False

    def test_passed_then_failed_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".gate-status")
            _touch(p, "=== GATE: PASSED ===\nsome output\n=== GATE: FAILED ===\n")
            assert gate_is_green(p) is False


class TestShipCommit:
    def test_requires_gate_green(self):
        ga = MagicMock()
        ga.commit.return_value = "abc1234"

        with tempfile.TemporaryDirectory() as d:
            gate_path = os.path.join(d, ".gate-status")
            _touch(gate_path, "=== GATE: PASSED ===\nlint PASS\n")
            sha = ship_commit("test message", git=ga, repo_root=d, gate_path_override=gate_path)
            assert sha == "abc1234"
            ga.commit.assert_called_once_with("test message")

    def test_default_no_push(self):
        ga = MagicMock()
        ga.commit.return_value = "abc1234"

        with tempfile.TemporaryDirectory() as d:
            gate_path = os.path.join(d, ".gate-status")
            _touch(gate_path, "=== GATE: PASSED ===\n")
            sha = ship_commit("msg", git=ga, repo_root=d, gate_path_override=gate_path)
            ga.push.assert_not_called()
            assert sha == "abc1234"

    def test_push_when_flag_set(self):
        ga = MagicMock()
        ga.commit.return_value = "abc1234"

        with tempfile.TemporaryDirectory() as d:
            gate_path = os.path.join(d, ".gate-status")
            _touch(gate_path, "=== GATE: PASSED ===\n")
            sha = ship_commit("msg", git=ga, push=True, repo_root=d, gate_path_override=gate_path)
            ga.push.assert_called_once()
            assert sha == "abc1234"

    def test_blocks_on_red_gate(self):
        ga = MagicMock()

        with tempfile.TemporaryDirectory() as d:
            gate_path = os.path.join(d, ".gate-status")
            _touch(gate_path, "=== GATE: FAILED ===\nlint FAIL\n")
            with pytest.raises(ShipCommitError, match="red"):
                ship_commit("msg", git=ga, repo_root=d, gate_path_override=gate_path)
            ga.commit.assert_not_called()

    def test_allows_meta_commits_via_repo_commit(self):
        ga = MagicMock()
        ga.commit.return_value = "xyz5678"

        sha = ship_commit("meta: bump version", git=ga, skip_gate=True)
        ga.commit.assert_called_once()
        assert sha == "xyz5678"

    def test_commit_specific_files(self):
        ga = MagicMock()
        ga.commit.return_value = "abc1234"

        with tempfile.TemporaryDirectory() as d:
            gate_path = os.path.join(d, ".gate-status")
            _touch(gate_path, "=== GATE: PASSED ===\n")
            sha = ship_commit("msg", files=["a.py", "b.py"], git=ga, repo_root=d, gate_path_override=gate_path)
            assert sha == "abc1234"

    def test_defaults_to_cwd_repo_root(self):
        ga = MagicMock()
        ga.commit.return_value = "abc1234"

        with tempfile.TemporaryDirectory() as d:
            gate_path = os.path.join(d, ".gate-status")
            _touch(gate_path, "=== GATE: PASSED ===\n")
            with patch("os.getcwd", return_value=d):
                sha = ship_commit("msg", git=ga)
            assert sha == "abc1234"

    def test_missing_gate_raises_when_not_skipped(self):
        ga = MagicMock()
        with tempfile.TemporaryDirectory() as d:
            with pytest.raises(ShipCommitError, match="Gate is red or missing"):
                ship_commit("msg", git=ga, repo_root=d)
            ga.commit.assert_not_called()


class TestTestAndCommit:
    def test_runs_tests_before_commit(self):
        ga = MagicMock()
        ga.commit.return_value = "abc1234"

        with tempfile.TemporaryDirectory() as d:
            gate_path = os.path.join(d, ".gate-status")
            _touch(gate_path, "=== GATE: PASSED ===\n")

            with patch("general_ludd.git_automation.ship_commit.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "10 passed"
                sha = _test_and_commit(
                    "msg",
                    test_command=["python", "-m", "pytest", "tests/"],
                    git=ga,
                    repo_root=d,
                    gate_path_override=gate_path,
                )
            mock_run.assert_called()
            ga.commit.assert_called_once_with("msg")
            assert sha == "abc1234"

    def test_blocks_commit_if_tests_fail(self):
        ga = MagicMock()

        with tempfile.TemporaryDirectory() as d:
            gate_path = os.path.join(d, ".gate-status")
            _touch(gate_path, "=== GATE: PASSED ===\n")

            with patch("general_ludd.git_automation.ship_commit.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 1
                mock_run.return_value.stderr = "1 failed, 9 passed"
                mock_run.return_value.stdout = ""
                with pytest.raises(ShipCommitError, match="Tests failed"):
                    _test_and_commit(
                        "msg",
                        test_command=["pytest"],
                        git=ga,
                        repo_root=d,
                        gate_path_override=gate_path,
                    )
            ga.commit.assert_not_called()

    def test_commits_if_tests_pass(self):
        ga = MagicMock()
        ga.commit.return_value = "def6789"

        with tempfile.TemporaryDirectory() as d:
            gate_path = os.path.join(d, ".gate-status")
            _touch(gate_path, "=== GATE: PASSED ===\n")

            with patch("general_ludd.git_automation.ship_commit.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                sha = _test_and_commit(
                    "msg",
                    test_command=["pytest"],
                    git=ga,
                    repo_root=d,
                    gate_path_override=gate_path,
                    skip_gate=True,
                )
            assert sha == "def6789"

    def test_gate_is_checked_before_tests(self):
        ga = MagicMock()

        with tempfile.TemporaryDirectory() as d:
            gate_path = os.path.join(d, ".gate-status")
            _touch(gate_path, "=== GATE: FAILED ===\n")
            with pytest.raises(ShipCommitError, match="red"):
                _test_and_commit(
                    "msg",
                    test_command=["pytest"],
                    git=ga,
                    repo_root=d,
                    gate_path_override=gate_path,
                )
            ga.commit.assert_not_called()
