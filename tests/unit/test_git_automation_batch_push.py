"""Unit tests for git_automation/batch_push.py — Makefile batch-push port."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import Mock, patch

from general_ludd.git_automation.batch_push import BatchPushResult, batch_push


def _mock_run(
    stdout_map: dict[str, str],
    returncode_map: dict[str, int] | None = None,
) -> Any:
    returncode_map = returncode_map or {}

    def _runner(cmd: list[str], **kwargs: Any) -> Mock:
        key = " ".join(str(c) for c in cmd)
        stdout = ""
        returncode = 0
        for pattern, output in stdout_map.items():
            if pattern in key:
                stdout = output
                break
        for pattern, rc in returncode_map.items():
            if pattern in key:
                returncode = rc
                break
        proc = Mock()
        proc.stdout = stdout
        proc.returncode = returncode
        return proc

    return _runner


class TestBatchPushLogic:

    def test_blocks_when_below_threshold(self) -> None:
        with patch(
            "general_ludd.git_automation.batch_push._count_unpushed",
            return_value=3,
        ):
            result = batch_push(
                repo_path="/tmp/test-repo",
                remote="origin",
                branch="master",
                threshold=5,
                force=False,
            )
        assert result.pushed is False
        assert result.reason == "below_threshold"
        assert result.unpushed_count == 3
        assert result.threshold == 5

    def test_pushes_when_at_threshold(self) -> None:
        with (
            patch(
                "general_ludd.git_automation.batch_push._count_unpushed",
                return_value=5,
            ),
            patch(
                "general_ludd.git_automation.batch_push._ci_in_flight",
                return_value=False,
            ),
            patch(
                "general_ludd.git_automation.batch_push._do_push",
                return_value=True,
            ),
            patch(
                "general_ludd.git_automation.batch_push._verify_remote",
                return_value="abc123def",
            ),
        ):
            result = batch_push(
                repo_path="/tmp/test-repo",
                remote="sandboxcom",
                branch="master",
                threshold=5,
                force=False,
            )
        assert result.pushed is True
        assert result.reason == "threshold_met"
        assert result.remote_sha == "abc123def"

    def test_pushes_when_above_threshold(self) -> None:
        with (
            patch(
                "general_ludd.git_automation.batch_push._count_unpushed",
                return_value=7,
            ),
            patch(
                "general_ludd.git_automation.batch_push._ci_in_flight",
                return_value=False,
            ),
            patch(
                "general_ludd.git_automation.batch_push._do_push",
                return_value=True,
            ),
            patch(
                "general_ludd.git_automation.batch_push._verify_remote",
                return_value="def456abc",
            ),
        ):
            result = batch_push(
                repo_path="/tmp/test-repo",
                remote="sandboxcom",
                branch="master",
                threshold=5,
            )
        assert result.pushed is True
        assert result.unpushed_count == 7

    def test_blocks_when_ci_in_flight(self) -> None:
        with (
            patch(
                "general_ludd.git_automation.batch_push._count_unpushed",
                return_value=5,
            ),
            patch(
                "general_ludd.git_automation.batch_push._ci_in_flight",
                return_value=True,
            ),
        ):
            result = batch_push(
                repo_path="/tmp/test-repo",
                remote="sandboxcom",
                branch="master",
                threshold=5,
            )
        assert result.pushed is False
        assert result.reason == "ci_in_flight"

    def test_allows_force_override(self) -> None:
        with (
            patch(
                "general_ludd.git_automation.batch_push._count_unpushed",
                return_value=2,
            ),
            patch(
                "general_ludd.git_automation.batch_push._do_push",
                return_value=True,
            ),
            patch(
                "general_ludd.git_automation.batch_push._verify_remote",
                return_value="abc123",
            ),
        ):
            result = batch_push(
                repo_path="/tmp/test-repo",
                remote="sandboxcom",
                branch="master",
                threshold=5,
                force=True,
            )
        assert result.pushed is True
        assert result.reason == "force_override"
        assert result.unpushed_count == 2

    def test_verify_remote_after_push(self) -> None:
        with (
            patch(
                "general_ludd.git_automation.batch_push._count_unpushed",
                return_value=5,
            ),
            patch(
                "general_ludd.git_automation.batch_push._ci_in_flight",
                return_value=False,
            ),
            patch(
                "general_ludd.git_automation.batch_push._do_push",
                return_value=True,
            ),
            patch(
                "general_ludd.git_automation.batch_push._verify_remote",
                return_value="sha_verified",
            ),
        ):
            result = batch_push(
                repo_path="/tmp/test-repo",
                remote="sandboxcom",
                branch="master",
                threshold=5,
            )
        assert result.pushed is True
        assert result.remote_sha == "sha_verified"
        assert result.verified is True

    def test_push_failure_sets_verified_false(self) -> None:
        with (
            patch(
                "general_ludd.git_automation.batch_push._count_unpushed",
                return_value=5,
            ),
            patch(
                "general_ludd.git_automation.batch_push._ci_in_flight",
                return_value=False,
            ),
            patch(
                "general_ludd.git_automation.batch_push._do_push",
                return_value=False,
            ),
            patch(
                "general_ludd.git_automation.batch_push._verify_remote",
                return_value="",
            ),
        ):
            result = batch_push(
                repo_path="/tmp/test-repo",
                remote="sandboxcom",
                branch="master",
                threshold=5,
            )
        assert result.pushed is False
        assert result.verified is False
        assert result.reason == "push_failed"

    def test_returns_push_count_and_shas(self) -> None:
        with (
            patch(
                "general_ludd.git_automation.batch_push._count_unpushed",
                return_value=6,
            ),
            patch(
                "general_ludd.git_automation.batch_push._ci_in_flight",
                return_value=False,
            ),
            patch(
                "general_ludd.git_automation.batch_push._do_push",
                return_value=True,
            ),
            patch(
                "general_ludd.git_automation.batch_push._verify_remote",
                return_value="fedcba987",
            ),
        ):
            result = batch_push(
                repo_path="/tmp/test-repo",
                remote="sandboxcom",
                branch="master",
                threshold=5,
                force=False,
            )
        assert result.pushed is True
        assert result.unpushed_count == 6
        assert result.threshold == 5
        assert result.remote_sha == "fedcba987"
        assert result.verified is True

    def test_force_bypasses_ci_check(self) -> None:
        with (
            patch(
                "general_ludd.git_automation.batch_push._count_unpushed",
                return_value=1,
            ),
            patch(
                "general_ludd.git_automation.batch_push._do_push",
                return_value=True,
            ),
            patch(
                "general_ludd.git_automation.batch_push._verify_remote",
                return_value="111aaaa",
            ),
        ):
            result = batch_push(
                repo_path="/tmp/test-repo",
                remote="sandboxcom",
                branch="master",
                threshold=5,
                force=True,
                check_ci=True,
            )
        assert result.pushed is True

    def test_reason_for_ci_in_flight(self) -> None:
        with (
            patch(
                "general_ludd.git_automation.batch_push._count_unpushed",
                return_value=10,
            ),
            patch(
                "general_ludd.git_automation.batch_push._ci_in_flight",
                return_value=True,
            ),
        ):
            result = batch_push(
                repo_path="/tmp/test-repo",
                remote="sandboxcom",
                branch="master",
                threshold=5,
                force=False,
                check_ci=True,
            )
        assert result.pushed is False
        assert result.reason == "ci_in_flight"
        assert result.unpushed_count == 10


class TestBatchPushResult:
    def test_defaults(self) -> None:
        r = BatchPushResult(pushed=False, unpushed_count=0, threshold=5)
        assert r.pushed is False
        assert r.unpushed_count == 0
        assert r.threshold == 5
        assert r.reason == ""
        assert r.remote_sha == ""
        assert r.verified is False

    def test_successful_push(self) -> None:
        r = BatchPushResult(
            pushed=True,
            unpushed_count=7,
            threshold=5,
            reason="threshold_met",
            remote_sha="abc123",
            verified=True,
        )
        assert r.pushed is True
        assert r.reason == "threshold_met"
        assert r.verified is True

    def test_force_push(self) -> None:
        r = BatchPushResult(
            pushed=True,
            unpushed_count=2,
            threshold=5,
            reason="force_override",
            remote_sha="def456",
            verified=True,
        )
        assert r.pushed is True
        assert r.reason == "force_override"
        assert r.unpushed_count == 2
        assert r.threshold == 5


class TestBatchPushHelpers:
    def test_count_unpushed_zero(self) -> None:
        mock_run = _mock_run({"rev-list": ""})
        with patch("subprocess.run", side_effect=mock_run):
            from general_ludd.git_automation.batch_push import _count_unpushed

            count = _count_unpushed("/tmp/test-repo", "sandboxcom", "master")
        assert count == 0

    def test_count_unpushed_several(self) -> None:
        mock_run = _mock_run({"rev-list": "7\n"})
        with patch("subprocess.run", side_effect=mock_run):
            from general_ludd.git_automation.batch_push import _count_unpushed

            count = _count_unpushed("/tmp/test-repo", "sandboxcom", "master")
        assert count == 7

    def test_count_unpushed_handles_missing_upstream(self) -> None:
        def _runner(cmd: list[str], **kwargs: Any) -> Mock:
            if "rev-list" in " ".join(str(c) for c in cmd):
                raise subprocess.CalledProcessError(
                    128, cmd, output="", stderr="fatal: no upstream"
                )
            return Mock(stdout="", returncode=0)

        with patch("subprocess.run", side_effect=_runner):
            from general_ludd.git_automation.batch_push import _count_unpushed

            count = _count_unpushed("/tmp/test-repo", "sandboxcom", "master")
        assert count == 0

    def test_ci_in_flight_detection(self) -> None:
        def _runner(cmd: list[str], **kwargs: Any) -> Mock:
            key = " ".join(str(c) for c in cmd)
            if "run list" in key:
                proc = Mock()
                proc.stdout = (
                    '[{"status":"in_progress","conclusion":null,"headSha":"abc"}]'
                )
                proc.returncode = 0
                return proc
            return Mock(stdout="", returncode=0)

        with patch("subprocess.run", side_effect=_runner):
            from general_ludd.git_automation.batch_push import _ci_in_flight

            inflight = _ci_in_flight("master")
        assert inflight is True

    def test_ci_not_in_flight(self) -> None:
        def _runner(cmd: list[str], **kwargs: Any) -> Mock:
            key = " ".join(str(c) for c in cmd)
            if "run list" in key:
                proc = Mock()
                proc.stdout = (
                    '[{"status":"completed","conclusion":"success","headSha":"abc"}]'
                )
                proc.returncode = 0
                return proc
            return Mock(stdout="", returncode=0)

        with patch("subprocess.run", side_effect=_runner):
            from general_ludd.git_automation.batch_push import _ci_in_flight

            inflight = _ci_in_flight("master")
        assert inflight is False

    def test_ci_check_fails_gracefully(self) -> None:
        def _runner(cmd: list[str], **kwargs: Any) -> Mock:
            key = " ".join(str(c) for c in cmd)
            if "run list" in key:
                raise subprocess.CalledProcessError(
                    1, cmd, output="", stderr="gh not found"
                )
            return Mock(stdout="", returncode=0)

        with patch("subprocess.run", side_effect=_runner):
            from general_ludd.git_automation.batch_push import _ci_in_flight

            inflight = _ci_in_flight("master")
        assert inflight is False

    def test_verify_remote_match(self) -> None:
        def _runner(cmd: list[str], **kwargs: Any) -> Mock:
            key = " ".join(str(c) for c in cmd)
            if "rev-parse HEAD" in key:
                proc = Mock()
                proc.stdout = "abc123\n"
                proc.returncode = 0
                return proc
            if "ls-remote" in key:
                proc = Mock()
                proc.stdout = "abc123\trefs/heads/master\n"
                proc.returncode = 0
                return proc
            return Mock(stdout="", returncode=0)

        with patch("subprocess.run", side_effect=_runner):
            from general_ludd.git_automation.batch_push import _verify_remote

            sha = _verify_remote("/tmp/test-repo", "sandboxcom", "master")
        assert sha == "abc123"

    def test_verify_remote_mismatch(self) -> None:
        def _runner(cmd: list[str], **kwargs: Any) -> Mock:
            key = " ".join(str(c) for c in cmd)
            if "rev-parse HEAD" in key:
                proc = Mock()
                proc.stdout = "abc123\n"
                proc.returncode = 0
                return proc
            if "ls-remote" in key:
                proc = Mock()
                proc.stdout = "def456\trefs/heads/master\n"
                proc.returncode = 0
                return proc
            return Mock(stdout="", returncode=0)

        with patch("subprocess.run", side_effect=_runner):
            from general_ludd.git_automation.batch_push import _verify_remote

            sha = _verify_remote("/tmp/test-repo", "sandboxcom", "master")
        assert sha == ""

    def test_verify_remote_failure(self) -> None:
        def _runner(cmd: list[str], **kwargs: Any) -> Mock:
            raise subprocess.CalledProcessError(
                1, ["git"], output="", stderr="failed"
            )

        with patch("subprocess.run", side_effect=_runner):
            from general_ludd.git_automation.batch_push import _verify_remote

            sha = _verify_remote("/tmp/test-repo", "sandboxcom", "master")
        assert sha == ""

    def test_do_push_success(self) -> None:
        mock_run = Mock(returncode=0, stdout="Everything up-to-date\n", stderr="")
        with patch("subprocess.run", return_value=mock_run):
            from general_ludd.git_automation.batch_push import _do_push

            ok = _do_push("/tmp/test-repo", "sandboxcom", "master")
        assert ok is True

    def test_do_push_failure(self) -> None:
        def _runner(*args: Any, **kwargs: Any) -> Mock:
            raise subprocess.CalledProcessError(
                1, ["git", "push"], output="", stderr="rejected"
            )

        with patch("subprocess.run", side_effect=_runner):
            from general_ludd.git_automation.batch_push import _do_push

            ok = _do_push("/tmp/test-repo", "sandboxcom", "master")
        assert ok is False
