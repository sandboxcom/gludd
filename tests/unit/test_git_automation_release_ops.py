"""Tests for git_automation release operations — release-cut, release-delete, release-recut."""

from __future__ import annotations

from unittest.mock import patch

from general_ludd.git_automation.release_ops import (
    ReleaseCutResult,
    ReleaseDeleteResult,
    ReleaseRecutResult,
    release_cut,
    release_delete,
    release_recut,
    verify_readme_status,
)


class TestReleaseCutResult:
    def test_success(self):
        r = ReleaseCutResult(success=True, tag="v0.1.0", branch="master", commit_sha="abc123")
        assert r.success is True
        assert r.tag == "v0.1.0"
        assert r.branch == "master"
        assert r.commit_sha == "abc123"

    def test_failure_with_message(self):
        r = ReleaseCutResult(success=False, tag="v0.1.0", branch="master", message="CI not green")
        assert r.success is False
        assert r.commit_sha == ""
        assert r.message == "CI not green"

    def test_steps_completed_defaults(self):
        r = ReleaseCutResult(success=True, tag="v0.1.0", branch="master")
        assert r.steps_completed == []

    def test_steps_completed_tracks_progress(self):
        r = ReleaseCutResult(success=False, tag="v0.1.0", branch="master",
                             steps_completed=["ci-green", "readme-check"])
        assert r.steps_completed == ["ci-green", "readme-check"]


class TestReleaseDeleteResult:
    def test_success_all_deleted(self):
        r = ReleaseDeleteResult(success=True, tag="v0.1.0",
                                local_deleted=True, remote_deleted=True,
                                gh_release_deleted=True)
        assert r.local_deleted is True
        assert r.remote_deleted is True
        assert r.gh_release_deleted is True

    def test_partial_deletion(self):
        r = ReleaseDeleteResult(success=True, tag="v0.1.0",
                                local_deleted=True, remote_deleted=False,
                                gh_release_deleted=False,
                                message="remote tag not found")
        assert r.remote_deleted is False
        assert "remote tag not found" in r.message

    def test_defaults(self):
        r = ReleaseDeleteResult(success=False, tag="v0.1.0")
        assert r.local_deleted is False
        assert r.remote_deleted is False
        assert r.gh_release_deleted is False
        assert r.message == ""


class TestReleaseRecutResult:
    def test_success(self):
        r = ReleaseRecutResult(success=True, tag="v0.1.0",
                               message="Re-cut tag v0.1.0 pushed")
        assert r.success is True
        assert r.tag == "v0.1.0"

    def test_failure_on_ci_red(self):
        r = ReleaseRecutResult(success=False, tag="v0.1.0",
                               message="CI not green for tag commit")
        assert r.success is False

    def test_steps_completed_defaults(self):
        r = ReleaseRecutResult(success=True, tag="v0.1.0")
        assert r.steps_completed == []


class TestReleaseCut:
    @patch("general_ludd.git_automation.release_ops._run_require_ci_green")
    @patch("general_ludd.git_automation.release_ops._run_check_readme_status")
    @patch("general_ludd.git_automation.release_ops._git_push_branch")
    @patch("general_ludd.git_automation.release_ops._git_tag_push")
    @patch("general_ludd.git_automation.release_ops._git_rev_parse")
    @patch("general_ludd.git_automation.release_ops._git_tag_exists")
    def test_happy_path_all_steps(
        self, mock_tag_exists, mock_rev_parse, mock_tag_push,
        mock_push_branch, mock_readme, mock_ci_green,
    ):
        mock_tag_exists.return_value = False
        mock_ci_green.return_value = (0, "CI GREEN")
        mock_readme.return_value = (0, "OK")
        mock_push_branch.return_value = (0, "Everything up-to-date")
        mock_tag_push.return_value = (0, "Pushed tag")
        mock_rev_parse.return_value = "abc123def456"

        result = release_cut(tag="v0.1.0", message="release", branch="master",
                             repo_path="/tmp/repo", remote="sandboxcom")

        assert result.success is True
        assert result.tag == "v0.1.0"
        assert result.commit_sha == "abc123def456"
        assert "ci-green" in result.steps_completed
        assert "readme-check" in result.steps_completed
        assert "branch-push" in result.steps_completed
        assert "tag-push" in result.steps_completed

    @patch("general_ludd.git_automation.release_ops._run_require_ci_green")
    @patch("general_ludd.git_automation.release_ops._git_rev_parse")
    @patch("general_ludd.git_automation.release_ops._git_tag_exists")
    def test_requires_ci_green_before_tag(
        self, mock_tag_exists, mock_rev_parse, mock_ci_green,
    ):
        mock_tag_exists.return_value = False
        mock_ci_green.return_value = (1, "CI RED")
        mock_rev_parse.return_value = "abc123"

        result = release_cut(tag="v0.1.0", message="release", branch="master",
                             repo_path="/tmp/repo", remote="sandboxcom")

        assert result.success is False
        assert "CI" in result.message

    @patch("general_ludd.git_automation.release_ops._run_require_ci_green")
    @patch("general_ludd.git_automation.release_ops._run_check_readme_status")
    @patch("general_ludd.git_automation.release_ops._git_push_branch")
    @patch("general_ludd.git_automation.release_ops._git_rev_parse")
    @patch("general_ludd.git_automation.release_ops._git_tag_exists")
    def test_checks_readme_status_matches_tag(
        self, mock_tag_exists, mock_rev_parse, mock_push, mock_readme, mock_ci_green,
    ):
        mock_tag_exists.return_value = False
        mock_ci_green.return_value = (0, "CI GREEN")
        mock_readme.return_value = (1, "README stale")
        mock_push.return_value = (0, "ok")
        mock_rev_parse.return_value = "abc123"

        result = release_cut(tag="v0.1.0", message="release", branch="master",
                             repo_path="/tmp/repo", remote="sandboxcom")

        assert result.success is False
        assert "README" in result.message

    @patch("general_ludd.git_automation.release_ops._run_require_ci_green")
    @patch("general_ludd.git_automation.release_ops._run_check_readme_status")
    @patch("general_ludd.git_automation.release_ops._git_push_branch")
    @patch("general_ludd.git_automation.release_ops._git_tag_push")
    @patch("general_ludd.git_automation.release_ops._git_rev_parse")
    @patch("general_ludd.git_automation.release_ops._git_tag_exists")
    def test_rejects_when_tag_exists(
        self, mock_tag_exists, mock_rev_parse, mock_tag_push,
        mock_push_branch, mock_readme, mock_ci_green,
    ):
        mock_tag_exists.return_value = True

        result = release_cut(tag="v0.1.0", message="release", branch="master",
                             repo_path="/tmp/repo", remote="sandboxcom")

        assert result.success is False
        assert "already exists" in result.message.lower()

    @patch("general_ludd.git_automation.release_ops._run_require_ci_green")
    @patch("general_ludd.git_automation.release_ops._git_push_branch")
    @patch("general_ludd.git_automation.release_ops._git_tag_push")
    @patch("general_ludd.git_automation.release_ops._git_rev_parse")
    @patch("general_ludd.git_automation.release_ops._git_tag_exists")
    def test_skips_readme_check_when_flag_disabled(
        self, mock_tag_exists, mock_rev_parse, mock_tag_push,
        mock_push_branch, mock_ci_green,
    ):
        mock_tag_exists.return_value = False
        mock_ci_green.return_value = (0, "CI GREEN")
        mock_push_branch.return_value = (0, "ok")
        mock_tag_push.return_value = (0, "Pushed tag")
        mock_rev_parse.return_value = "abc123"

        result = release_cut(tag="v0.1.0", message="release", branch="master",
                             repo_path="/tmp/repo", remote="sandboxcom",
                             skip_readme_check=True)

        assert result.success is True
        assert "readme-check" not in result.steps_completed


class TestReleaseDelete:
    @patch("general_ludd.git_automation.release_ops._run_git_tag_exists")
    @patch("general_ludd.git_automation.release_ops._git_tag_delete_local")
    @patch("general_ludd.git_automation.release_ops._git_tag_delete_remote")
    @patch("general_ludd.git_automation.release_ops._gh_release_delete")
    def test_deletes_local_tag(
        self, mock_gh, mock_remote_del, mock_local_del, mock_tag_exists,
    ):
        mock_tag_exists.return_value = True
        mock_local_del.return_value = (0, "Deleted tag 'v0.1.0'")
        mock_remote_del.return_value = (0, "")
        mock_gh.return_value = (0, "")

        result = release_delete(tag="v0.1.0", repo_path="/tmp/repo",
                                remote="sandboxcom")

        assert result.local_deleted is True
        mock_local_del.assert_called_once_with("v0.1.0", "/tmp/repo")

    @patch("general_ludd.git_automation.release_ops._run_git_tag_exists")
    @patch("general_ludd.git_automation.release_ops._git_tag_delete_local")
    @patch("general_ludd.git_automation.release_ops._git_tag_delete_remote")
    @patch("general_ludd.git_automation.release_ops._gh_release_delete")
    def test_deletes_remote_tag(
        self, mock_gh, mock_remote_del, mock_local_del, mock_tag_exists,
    ):
        mock_tag_exists.return_value = True
        mock_local_del.return_value = (0, "")
        mock_remote_del.return_value = (0, "")
        mock_gh.return_value = (0, "")

        result = release_delete(tag="v0.1.0", repo_path="/tmp/repo", remote="sandboxcom")

        assert result.remote_deleted is True

    @patch("general_ludd.git_automation.release_ops._run_git_tag_exists")
    @patch("general_ludd.git_automation.release_ops._git_tag_delete_local")
    @patch("general_ludd.git_automation.release_ops._git_tag_delete_remote")
    @patch("general_ludd.git_automation.release_ops._gh_release_delete")
    def test_calls_gh_release_delete(
        self, mock_gh, mock_remote_del, mock_local_del, mock_tag_exists,
    ):
        mock_tag_exists.return_value = True
        mock_local_del.return_value = (0, "")
        mock_remote_del.return_value = (0, "")
        mock_gh.return_value = (0, "release deleted")

        result = release_delete(tag="v0.1.0", repo_path="/tmp/repo", remote="sandboxcom")

        mock_gh.assert_called_once_with("v0.1.0", "sandboxcom/gludd")
        assert result.gh_release_deleted is True

    @patch("general_ludd.git_automation.release_ops._run_git_tag_exists")
    @patch("general_ludd.git_automation.release_ops._git_tag_delete_local")
    @patch("general_ludd.git_automation.release_ops._git_tag_delete_remote")
    @patch("general_ludd.git_automation.release_ops._gh_release_delete")
    def test_handles_missing_tag_gracefully(
        self, mock_gh, mock_remote_del, mock_local_del, mock_tag_exists,
    ):
        mock_tag_exists.return_value = False
        mock_local_del.return_value = (1, "error: tag 'v0.1.0' not found.")
        mock_remote_del.return_value = (0, "")
        mock_gh.return_value = (0, "")

        result = release_delete(tag="v0.1.0", repo_path="/tmp/repo",
                                remote="sandboxcom")

        assert result.success is True


class TestReleaseRecut:
    @patch("general_ludd.git_automation.release_ops._run_require_ci_green")
    @patch("general_ludd.git_automation.release_ops._git_rev_parse")
    @patch("general_ludd.git_automation.release_ops._run_git_tag_exists")
    @patch("general_ludd.git_automation.release_ops._git_tag_delete_remote")
    @patch("general_ludd.git_automation.release_ops._git_tag_push")
    def test_deletes_then_recreates_tag(
        self, mock_tag_push, mock_remote_del, mock_tag_exists,
        mock_rev_parse, mock_ci_green,
    ):
        mock_tag_exists.return_value = True
        mock_ci_green.return_value = (0, "CI GREEN")
        mock_rev_parse.return_value = "abc123"
        mock_remote_del.return_value = (0, "")
        mock_tag_push.return_value = (0, "Pushed tag")

        result = release_recut(tag="v0.1.0", message="re-cut", branch="master",
                               repo_path="/tmp/repo", remote="sandboxcom")

        assert result.success is True
        mock_remote_del.assert_called_once()
        mock_tag_push.assert_called_once()

    @patch("general_ludd.git_automation.release_ops._run_require_ci_green")
    @patch("general_ludd.git_automation.release_ops._git_rev_parse")
    @patch("general_ludd.git_automation.release_ops._run_git_tag_exists")
    @patch("general_ludd.git_automation.release_ops._git_tag_push")
    def test_pushes_new_tag_after_delete(
        self, mock_tag_push, mock_tag_exists, mock_rev_parse, mock_ci_green,
    ):
        mock_tag_exists.return_value = True
        mock_ci_green.return_value = (0, "CI GREEN")
        mock_rev_parse.return_value = "abc123"
        mock_tag_push.return_value = (0, "Pushed tag v0.1.0")

        release_recut(tag="v0.1.0", message="re-cut", branch="master",
                      repo_path="/tmp/repo", remote="sandboxcom")

        mock_tag_push.assert_called_once_with(
            "v0.1.0", "re-cut", "/tmp/repo", commit=None, remote="sandboxcom"
        )

    @patch("general_ludd.git_automation.release_ops._run_require_ci_green")
    @patch("general_ludd.git_automation.release_ops._git_rev_parse")
    @patch("general_ludd.git_automation.release_ops._run_git_tag_exists")
    def test_fails_when_tag_not_found(
        self, mock_tag_exists, mock_rev_parse, mock_ci_green,
    ):
        mock_tag_exists.return_value = False
        mock_ci_green.return_value = (0, "CI GREEN")
        mock_rev_parse.return_value = "abc123"

        result = release_recut(tag="v0.1.0", message="re-cut", branch="master",
                               repo_path="/tmp/repo", remote="sandboxcom")

        assert result.success is False
        assert "not found" in result.message.lower()

    @patch("general_ludd.git_automation.release_ops._run_require_ci_green")
    def test_rejects_when_ci_red(self, mock_ci_green):
        mock_ci_green.return_value = (1, "CI RED")

        result = release_recut(tag="v0.1.0", message="re-cut", branch="master",
                               repo_path="/tmp/repo", remote="sandboxcom")

        assert result.success is False


class TestVerifyReadmeStatus:
    @patch("general_ludd.git_automation.release_ops._check_readme_status_inner")
    def test_matches_tag(self, mock_inner):
        mock_inner.return_value = (0, "OK — README status table is current")
        rc, msg = verify_readme_status("v0.1.0")
        assert rc == 0
        assert "OK" in msg

    @patch("general_ludd.git_automation.release_ops._check_readme_status_inner")
    def test_stale_readme(self, mock_inner):
        mock_inner.return_value = (1, "ERROR: README status table is stale")
        rc, msg = verify_readme_status("v0.2.0")
        assert rc == 1
        assert "stale" in msg

    @patch("general_ludd.git_automation.release_ops._check_readme_status_inner")
    def test_readme_missing_status_line(self, mock_inner):
        mock_inner.return_value = (1, "ERROR: no 'Status as of' line found")
        rc, msg = verify_readme_status("v0.1.0")
        assert rc == 1
        assert "no 'Status as of'" in msg.lower() or "status" in msg.lower()
