"""Tests for git_automation/ci_ops.py — CI verdict, cooldown, cancel operations."""

from __future__ import annotations

import json
import subprocess
import time
from unittest import mock

from general_ludd.git_automation.ci_ops import (
    _parse_gh_run_list,
    ci_active,
    ci_cancel,
    ci_cooldown_status,
    ci_verdict,
    ci_verdict_safe,
)


# ── helpers ────────────────────────────────────────────────────────────────────
def _make_run(**overrides):
    run = {
        "databaseId": 1234567890,
        "conclusion": "success",
        "status": "completed",
        "headSha": "abc123def4567890123456789012345678901234",
    }
    run.update(overrides)
    return run


def _mock_run_output(runs):
    return json.dumps(runs)


# ── CiVerdict ───────────────────────────────────────────────────────────────────


class TestParseGhRunList:
    def test_parse_empty_list(self):
        result = _parse_gh_run_list([])
        assert result["verdict"] == "UNKNOWN"

    def test_parse_success_conclusion(self):
        result = _parse_gh_run_list([_make_run(conclusion="success")])
        assert result["verdict"] == "GREEN"
        assert result["headSha"] == "abc123def4567890123456789012345678901234"

    def test_parse_failure_conclusion(self):
        result = _parse_gh_run_list([_make_run(conclusion="failure")])
        assert result["verdict"] == "RED"
        assert result["run_id"] == "1234567890"

    def test_parse_cancelled_is_red(self):
        result = _parse_gh_run_list([_make_run(conclusion="cancelled")])
        assert result["verdict"] == "RED"

    def test_parse_timed_out_is_red(self):
        result = _parse_gh_run_list([_make_run(conclusion="timed_out")])
        assert result["verdict"] == "RED"

    def test_parse_in_progress_is_pending(self):
        result = _parse_gh_run_list(
            [_make_run(status="in_progress", conclusion=None)]
        )
        assert result["verdict"] == "PENDING"

    def test_parse_queued_is_pending(self):
        result = _parse_gh_run_list(
            [_make_run(status="queued", conclusion=None)]
        )
        assert result["verdict"] == "PENDING"

    def test_parse_null_conclusion_in_progress_is_pending(self):
        result = _parse_gh_run_list(
            [_make_run(status="in_progress", conclusion=None)]
        )
        assert result["verdict"] == "PENDING"

    def test_parse_skipped_is_bypass(self):
        result = _parse_gh_run_list([_make_run(conclusion="skipped")])
        assert result["verdict"] == "GREEN"

    def test_parse_takes_first_run(self):
        result = _parse_gh_run_list(
            [
                _make_run(conclusion="success", databaseId=1),
                _make_run(conclusion="failure", databaseId=2),
            ]
        )
        assert result["verdict"] == "GREEN"
        assert result["run_id"] == "1"


class TestCiVerdict:
    def test_verdict_returns_green_for_success(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout=json.dumps([_make_run(conclusion="success")]),
                returncode=0,
            )
            result = ci_verdict(branch="development", sha="abc123d")
            assert result["verdict"] == "GREEN"
            assert result["run_id"] == "1234567890"

    def test_verdict_returns_red_for_failure(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout=json.dumps([_make_run(conclusion="failure")]),
                returncode=0,
            )
            result = ci_verdict(branch="development", sha="deadbee")
            assert result["verdict"] == "RED"

    def test_verdict_returns_pending_for_in_progress(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout=json.dumps(
                    [_make_run(status="in_progress", conclusion=None)]
                ),
                returncode=0,
            )
            result = ci_verdict(branch="development", sha="abc123d")
            assert result["verdict"] == "PENDING"

    def test_verdict_unknown_for_no_run(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout=json.dumps([]),
                returncode=0,
            )
            result = ci_verdict(branch="development", sha="abc123d")
            assert result["verdict"] == "UNKNOWN"

    def test_verdict_matches_sha(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout=json.dumps(
                    [_make_run(headSha="0000111122223333444455556666777788889999")]
                ),
                returncode=0,
            )
            result = ci_verdict(branch="development", sha="0000111122223333444455556666777788889999")
            assert result["headSha"] == "0000111122223333444455556666777788889999"

    def test_verdict_detects_stale_run_headsha_mismatch(self):
        result = _parse_gh_run_list([
            _make_run(headSha="aaa11122233344455566677788899900001112222"),
        ])
        assert result["headSha"] == "aaa11122233344455566677788899900001112222"

    def test_verdict_stale_warning_when_headsha_differs_from_requested_sha(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout=json.dumps(
                    [_make_run(
                        conclusion="success",
                        headSha="different_sha_that_doesnt_match_0",
                    )]
                ),
                returncode=0,
            )
            result = ci_verdict(branch="development", sha="requested_sha_000")
            assert result["verdict"] == "GREEN"
            assert result["headSha"] == "different_sha_that_doesnt_match_0"
            assert result.get("stale") is True

    def test_verdict_handles_gh_error(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.side_effect = subprocess.CalledProcessError(1, "gh", "error")
            result = ci_verdict(branch="development")
            assert result["verdict"] == "UNKNOWN"

    def test_verdict_handles_json_parse_error(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout="not valid json",
                returncode=0,
            )
            result = ci_verdict(branch="development")
            assert result["verdict"] == "UNKNOWN"


# ── CiCooldown ──────────────────────────────────────────────────────────────────


class TestCiCooldown:
    def test_cooldown_blocks_recheck_within_window(self):
        now = time.time()
        state = {
            "last_check_epoch": now,
            "last_head_sha": "abc",
            "check_count": 3,
            "last_verdict": "success",
            "last_verdict_epoch": now,
        }
        with mock.patch(
            "general_ludd.git_automation.ci_ops._load_cooldown_state",
            return_value=state,
        ), mock.patch(
            "general_ludd.git_automation.ci_ops._get_current_time",
            return_value=now + 1,
        ):
            status = ci_cooldown_status()
            assert status["cooldown_active"] is True
            assert status["remaining_sec"] > 0

    def test_cooldown_allows_recheck_after_expiry(self):
        now = time.time()
        cooldown_sec = 600
        state = {
            "last_check_epoch": now - cooldown_sec - 1,
            "last_head_sha": "abc",
            "check_count": 3,
            "last_verdict": "success",
            "last_verdict_epoch": now - cooldown_sec - 1,
        }
        with mock.patch(
            "general_ludd.git_automation.ci_ops._load_cooldown_state",
            return_value=state,
        ), mock.patch(
            "general_ludd.git_automation.ci_ops._get_current_time",
            return_value=now,
        ):
            status = ci_cooldown_status()
            assert status["cooldown_active"] is False
            assert status["remaining_sec"] == 0

    def test_cooldown_records_last_known_verdict(self):
        now = time.time()
        state = {
            "last_check_epoch": now - 700,
            "last_head_sha": "abc",
            "check_count": 5,
            "last_verdict": "failure",
            "last_verdict_epoch": now - 700,
        }
        with mock.patch(
            "general_ludd.git_automation.ci_ops._load_cooldown_state",
            return_value=state,
        ), mock.patch(
            "general_ludd.git_automation.ci_ops._get_current_time",
            return_value=now,
        ):
            status = ci_cooldown_status()
            assert status["last_verdict"] == "failure"
            assert status["check_count"] == 5

    def test_cooldown_default_state_when_no_file(self):
        with mock.patch(
            "general_ludd.git_automation.ci_ops._load_cooldown_state",
            return_value={},
        ):
            status = ci_cooldown_status()
            assert status["cooldown_active"] is False
            assert status["check_count"] == 0
            assert status["last_verdict"] == ""

    def test_verdict_safe_blocks_within_cooldown(self):
        now = time.time()
        state = {
            "last_check_epoch": now,
            "last_head_sha": "abc",
            "check_count": 3,
            "last_verdict": "success",
            "last_verdict_epoch": now,
        }
        with mock.patch(
            "general_ludd.git_automation.ci_ops._load_cooldown_state",
            return_value=state,
        ), mock.patch(
            "general_ludd.git_automation.ci_ops._get_current_time",
            return_value=now + 1,
        ):
            result = ci_verdict_safe(branch="development")
            assert result["verdict"] == "COOLDOWN"

    def test_verdict_safe_allows_when_force(self):
        now = time.time()
        state = {
            "last_check_epoch": now,
            "last_head_sha": "abc",
            "check_count": 3,
            "last_verdict": "success",
            "last_verdict_epoch": now,
        }
        with mock.patch(
            "general_ludd.git_automation.ci_ops._load_cooldown_state",
            return_value=state,
        ), mock.patch(
            "general_ludd.git_automation.ci_ops._get_current_time",
            return_value=now + 1,
        ), mock.patch(
            "general_ludd.git_automation.ci_ops.ci_verdict",
            return_value={"verdict": "GREEN", "run_id": "1", "headSha": "abc"},
        ), mock.patch(
            "general_ludd.git_automation.ci_ops._save_cooldown_state"
        ) as save_state:
            result = ci_verdict_safe(branch="development", force=True)
            assert result["verdict"] == "GREEN"
            save_state.assert_called_once()

    def test_verdict_safe_allows_after_expiry(self):
        now = time.time()
        cooldown_sec = 600
        state = {
            "last_check_epoch": now - cooldown_sec - 10,
            "last_head_sha": "abc",
            "check_count": 3,
            "last_verdict": "success",
            "last_verdict_epoch": now - cooldown_sec - 10,
        }
        with mock.patch(
            "general_ludd.git_automation.ci_ops._load_cooldown_state",
            return_value=state,
        ), mock.patch(
            "general_ludd.git_automation.ci_ops._get_current_time",
            return_value=now,
        ), mock.patch(
            "general_ludd.git_automation.ci_ops.ci_verdict",
            return_value={"verdict": "GREEN", "run_id": "2", "headSha": "abc"},
        ), mock.patch(
            "general_ludd.git_automation.ci_ops._save_cooldown_state",
        ) as m_save:
            result = ci_verdict_safe(branch="development")
            assert result["verdict"] == "GREEN"
            m_save.assert_called()


# ── CiCancel ────────────────────────────────────────────────────────────────────


class TestCiCancel:
    def test_cancel_returns_run_id_on_success(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout="✓ Cancelled run 1234567890",
                stderr="",
                returncode=0,
            )
            result = ci_cancel("1234567890")
            assert result.get("success") is True
            assert result.get("run_id") == "1234567890"

    def test_cancel_handles_no_running_jobs(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.side_effect = subprocess.CalledProcessError(
                1, "gh", "Cannot cancel a workflow run that is completed.\n"
            )
            result = ci_cancel("9999999999")
            assert result.get("success") is False


# ── CiActive ────────────────────────────────────────────────────────────────────


class TestCiActive:
    def test_active_returns_true_when_in_progress(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout=json.dumps(
                    [
                        {"databaseId": 1, "status": "in_progress"},
                        {"databaseId": 2, "status": "completed"},
                    ]
                ),
                returncode=0,
            )
            assert ci_active(branch="development") is True

    def test_active_returns_false_when_none_in_progress(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout=json.dumps(
                    [
                        {"databaseId": 1, "status": "completed"},
                        {"databaseId": 2, "status": "completed"},
                    ]
                ),
                returncode=0,
            )
            assert ci_active(branch="development") is False

    def test_active_returns_false_when_no_runs(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.return_value = mock.MagicMock(
                stdout=json.dumps([]),
                returncode=0,
            )
            assert ci_active(branch="development") is False

    def test_active_handles_gh_error(self):
        with mock.patch("subprocess.run") as m_run:
            m_run.side_effect = subprocess.CalledProcessError(1, "gh", "error")
            assert ci_active(branch="development") is False
