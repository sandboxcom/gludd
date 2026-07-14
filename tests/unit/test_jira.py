"""Structural tests for issue_sources/jira.py — JiraIssueSource."""

from __future__ import annotations

from general_ludd.issue_sources.jira import _DEFAULT_TIMEOUT, JiraIssueSource


class TestJiraModule:
    def test_source_importable(self) -> None:
        assert JiraIssueSource is not None

    def test_default_timeout_positive(self) -> None:
        assert _DEFAULT_TIMEOUT > 0
        assert isinstance(_DEFAULT_TIMEOUT, float)
