"""Tests for git_automation verify_remote — ported from Makefile git ls-remote pattern."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from general_ludd.git_automation.verify_remote import (
    VerifyRemoteResult,
    verify_remote,
)


class TestVerifyRemoteResult:
    def test_verified_status_is_a_string(self) -> None:
        r = VerifyRemoteResult(
            status="VERIFIED",
            remote_sha="abc123def456",
            expected_sha="abc123def456",
            remote="sandboxcom",
            ref="refs/heads/master",
        )
        assert r.status == "VERIFIED"

    def test_mismatch_status(self) -> None:
        r = VerifyRemoteResult(
            status="MISMATCH",
            remote_sha="abc123",
            expected_sha="def456",
            remote="sandboxcom",
            ref="refs/heads/master",
        )
        assert r.status == "MISMATCH"

    def test_unreachable_status(self) -> None:
        r = VerifyRemoteResult(
            status="UNREACHABLE",
            remote_sha="",
            expected_sha="abc123",
            remote="sandboxcom",
            ref="refs/heads/master",
        )
        assert r.status == "UNREACHABLE"

    def test_default_message_is_empty(self) -> None:
        r = VerifyRemoteResult(
            status="VERIFIED",
            remote_sha="abc123",
            expected_sha="abc123",
            remote="origin",
            ref="refs/heads/main",
        )
        assert r.message == ""

    def test_message_field(self) -> None:
        r = VerifyRemoteResult(
            status="MISMATCH",
            remote_sha="abc",
            expected_sha="def",
            remote="origin",
            ref="refs/heads/main",
            message="remote tip differs from local HEAD",
        )
        assert "remote tip differs" in r.message


class TestVerifyRemoteModule:
    def test_accepts_remote_and_branch_and_expected_sha(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="abc123def456\trefs/heads/master\n", stderr=""
            )
            result = verify_remote(
                remote="sandboxcom",
                branch="master",
                expected_sha="abc123def456",
            )
        assert result.status == "VERIFIED"

    def test_returns_verified_when_sha_matches(self) -> None:
        sha = "abc123def456abc123def456abc123def456abc123de"
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=f"{sha}\trefs/heads/master\n", stderr=""
            )
            result = verify_remote(
                remote="sandboxcom",
                branch="master",
                expected_sha=sha,
            )
        assert result.status == "VERIFIED"
        assert result.remote_sha == sha

    def test_returns_mismatch_when_sha_differs(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="def456\trefs/heads/master\n", stderr=""
            )
            result = verify_remote(
                remote="sandboxcom",
                branch="master",
                expected_sha="abc123",
            )
        assert result.status == "MISMATCH"
        assert result.remote_sha == "def456"

    def test_returns_unreachable_when_no_matching_ref(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            result = verify_remote(
                remote="nonexistent",
                branch="no-branch",
                expected_sha="abc123",
            )
        assert result.status == "UNREACHABLE"

    def test_fails_closed_on_network_error(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git", "ls-remote"], timeout=60)
            result = verify_remote(
                remote="sandboxcom",
                branch="master",
                expected_sha="abc123",
            )
        assert result.status == "UNREACHABLE"
        assert "timed out" in result.message.lower()

    def test_fails_closed_on_called_process_error(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=128, cmd=["git", "ls-remote"], stderr="fatal: unable to connect"
            )
            result = verify_remote(
                remote="sandboxcom",
                branch="master",
                expected_sha="abc123",
            )
        assert result.status == "UNREACHABLE"
        assert "unable to connect" in result.message.lower()

    def test_respects_ssh_key_path_parameter(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="abc123\trefs/heads/master\n", stderr=""
            )
            verify_remote(
                remote="sandboxcom",
                branch="master",
                expected_sha="abc123",
                ssh_key_path="/some/key",
            )
        env = mock_run.call_args[1]["env"]
        assert "GIT_SSH_COMMAND" in env
        assert "/some/key" in env["GIT_SSH_COMMAND"]

    def test_handles_tag_refs_as_well_as_branches(self) -> None:
        sha = "abc123def456"
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=f"{sha}\trefs/tags/v1.0.0\n", stderr=""
            )
            result = verify_remote(
                remote="sandboxcom",
                branch="v1.0.0",
                expected_sha=sha,
                ref_type="tags",
            )
        assert result.status == "VERIFIED"
        args_list = mock_run.call_args[0][0]
        assert "refs/tags/v1.0.0" in args_list

    def test_partial_sha_match_is_not_verified(self) -> None:
        """A short-sha prefix match is NOT verification — full sha must match."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="abc123def456789\trefs/heads/master\n",
                stderr="",
            )
            result = verify_remote(
                remote="sandboxcom",
                branch="master",
                expected_sha="abc123d",
            )
        assert result.status == "MISMATCH"

    def test_no_ssh_key_gives_no_git_ssh_command_env(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="abc123\trefs/heads/master\n", stderr=""
            )
            verify_remote(
                remote="origin",
                branch="master",
                expected_sha="abc123",
            )
        env = mock_run.call_args[1]["env"]
        assert "GIT_SSH_COMMAND" not in env

    def test_reject_leading_dash_remote(self) -> None:
        with pytest.raises(ValueError, match="begins with '-'"):
            verify_remote(
                remote="--upload-pack=x",
                branch="master",
                expected_sha="abc123",
            )

    def test_reject_leading_dash_branch(self) -> None:
        with pytest.raises(ValueError, match="begins with '-'"):
            verify_remote(
                remote="sandboxcom",
                branch="--exec=whoami",
                expected_sha="abc123",
            )

    def test_return_type_is_dataclass(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="abc123\trefs/heads/main\n", stderr=""
            )
            result = verify_remote(
                remote="origin",
                branch="main",
                expected_sha="abc123",
            )
        assert isinstance(result, VerifyRemoteResult)
        assert result.remote == "origin"
        assert result.ref == "refs/heads/main"


class TestVerifyRemoteIntegration:
    """Integration-level checks against a real (or minimal) git repo."""

    def test_verify_against_local_head(self, tmp_path: Path) -> None:
        """Verify that ls-remote against origin in a test repo works.

        We push to a bare repo to create a remote, then verify the pushed sha.
        """
        repo_dir = tmp_path / "repo"
        bare_dir = tmp_path / "bare.git"
        repo_dir.mkdir()
        bare_dir.mkdir()

        subprocess.run(["git", "-C", str(repo_dir), "init"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo_dir), "config", "user.email", "test@test"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "config", "user.name", "Test"],
            check=True, capture_output=True,
        )
        (repo_dir / "file.txt").write_text("hello")
        subprocess.run(
            ["git", "-C", str(repo_dir), "add", "file.txt"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "commit", "-m", "init"],
            check=True, capture_output=True,
        )
        sha = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "add", "origin", str(bare_dir)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "init", "--bare", str(bare_dir)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "push", "origin", "master"],
            check=True, capture_output=True,
        )

        result = verify_remote(
            remote=str(bare_dir),
            branch="master",
            expected_sha=sha,
        )
        assert result.status == "VERIFIED"
        assert result.remote_sha == sha

    def test_mismatch_after_new_commit(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo"
        bare_dir = tmp_path / "bare.git"
        repo_dir.mkdir()
        bare_dir.mkdir()

        subprocess.run(["git", "-C", str(repo_dir), "init"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo_dir), "config", "user.email", "test@test"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "config", "user.name", "Test"],
            check=True, capture_output=True,
        )
        (repo_dir / "f.txt").write_text("a")
        subprocess.run(
            ["git", "-C", str(repo_dir), "add", "f.txt"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "commit", "-m", "first"],
            check=True, capture_output=True,
        )
        sha1 = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        subprocess.run(
            ["git", "init", "--bare", str(bare_dir)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "add", "origin", str(bare_dir)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "push", "origin", "master"],
            check=True, capture_output=True,
        )

        (repo_dir / "f.txt").write_text("b")
        subprocess.run(
            ["git", "-C", str(repo_dir), "commit", "-am", "second"],
            check=True, capture_output=True,
        )
        sha2 = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(repo_dir), "push", "origin", "master"],
            check=True, capture_output=True,
        )

        result = verify_remote(
            remote=str(bare_dir),
            branch="master",
            expected_sha=sha1,
        )
        assert result.status == "MISMATCH"
        assert result.remote_sha == sha2

    def test_unreachable_non_existent_remote(self) -> None:
        result = verify_remote(
            remote="/nonexistent/path/gludd-unittest-remote",
            branch="master",
            expected_sha="abc123",
        )
        assert result.status == "UNREACHABLE"
