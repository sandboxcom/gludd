"""Tests for GitAutomation repo module — importability, validation, and SSRF."""

from __future__ import annotations

import pytest


class TestGitAutomationImports:
    def test_module_importable(self) -> None:
        from general_ludd.git_automation import repo

        assert repo is not None

    def test_git_automation_class_exists(self) -> None:
        from general_ludd.git_automation.repo import GitAutomation

        assert GitAutomation is not None


class TestRejectLeadingDash:
    def test_normal_ref_accepted(self) -> None:
        from general_ludd.git_automation.repo import _reject_leading_dash

        result = _reject_leading_dash("main", kind="branch")
        assert result == "main"

    def test_leading_dash_rejected(self) -> None:
        from general_ludd.git_automation.repo import _reject_leading_dash

        with pytest.raises(ValueError, match="begins with '-'"):
            _reject_leading_dash("--force", kind="branch")
        assert True

    def test_empty_string_accepted_not_a_leading_dash(self) -> None:
        from general_ludd.git_automation.repo import _reject_leading_dash

        result = _reject_leading_dash("", kind="branch")
        assert result == ""

    def test_ref_with_dash_in_middle_accepted(self) -> None:
        from general_ludd.git_automation.repo import _reject_leading_dash

        result = _reject_leading_dash("feature/my-branch", kind="branch")
        assert result == "feature/my-branch"


class TestHostIsBlocked:
    def test_loopback_is_blocked(self) -> None:
        from general_ludd.git_automation.repo import _host_is_blocked

        assert _host_is_blocked("127.0.0.1") is True

    def test_localhost_is_blocked(self) -> None:
        from general_ludd.git_automation.repo import _host_is_blocked

        assert _host_is_blocked("localhost") is True

    def test_public_host_is_not_blocked(self) -> None:
        from general_ludd.git_automation.repo import _host_is_blocked

        assert _host_is_blocked("github.com") is False


class TestRejectUnsafeRepoUrl:
    def test_valid_https_url_accepted(self) -> None:
        from general_ludd.git_automation.repo import reject_unsafe_repo_url

        result = reject_unsafe_repo_url("https://github.com/user/repo.git")
        assert result == "https://github.com/user/repo.git"

    def test_file_url_rejected(self) -> None:
        from general_ludd.git_automation.repo import reject_unsafe_repo_url

        with pytest.raises(ValueError, match="refusing repo url scheme"):
            reject_unsafe_repo_url("file:///etc/passwd")
        assert True


class TestRejectCloneUrl:
    def test_https_url_accepted(self) -> None:
        from general_ludd.git_automation.repo import _reject_clone_url

        result = _reject_clone_url("https://github.com/user/repo.git")
        assert result == "https://github.com/user/repo.git"

    def test_empty_url_rejected(self) -> None:
        from general_ludd.git_automation.repo import _reject_clone_url

        with pytest.raises(ValueError, match="refusing empty"):
            _reject_clone_url("")
        assert True


class TestGitAutomationConstants:
    def test_git_timeout_positive(self) -> None:
        from general_ludd.git_automation.repo import _GIT_TIMEOUT_SECONDS

        assert _GIT_TIMEOUT_SECONDS > 0

    def test_non_interactive_env_present(self) -> None:
        from general_ludd.git_automation.repo import _NON_INTERACTIVE_GIT_ENV

        assert "GIT_TERMINAL_PROMPT" in _NON_INTERACTIVE_GIT_ENV
        assert _NON_INTERACTIVE_GIT_ENV["GIT_TERMINAL_PROMPT"] == "0"
