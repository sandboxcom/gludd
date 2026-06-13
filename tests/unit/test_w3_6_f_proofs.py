"""W3.6 proof tests for F1 (PR delivery) and F3 (GitHub issue ingestion).

These two feature modules existed in src/ but had no dedicated proof test,
leaving their rows in the W3.6 per-item proof table unbacked. This file
supplies the named proofs so every F1-F7 item has a green test reference.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.git_automation.issue_ingestor import GitHubIssueIngestor
from general_ludd.git_automation.pr_delivery import PRDelivery


class TestF1PRDelivery:
    """F1 — PR delivery via gh pr create (mocked subprocess)."""

    def test_push_and_create_pr_invokes_gh(self) -> None:
        delivery = PRDelivery(base_branch="main", draft=False, labels=["gludd"])

        gh_version = MagicMock(returncode=0)
        push = MagicMock(returncode=0, stderr="")
        pr_create = MagicMock(returncode=0, stdout="https://github.com/o/r/pull/7\n", stderr="")

        with patch(
            "general_ludd.git_automation.pr_delivery.subprocess.run",
            side_effect=[gh_version, push, pr_create],
        ) as run:
            result = delivery.push_and_create_pr(
                repo_path="/tmp/repo",
                branch_name="gludd/T1-fix",
                todo_id="T1",
                title="Fix bug",
                description="body",
            )

        assert result["pr_url"] == "https://github.com/o/r/pull/7"
        assert result["error"] is None
        # gh pr create was called with the branch + base + labelled title.
        pr_args = run.call_args_list[2].args[0]
        assert pr_args[:3] == ["gh", "pr", "create"]
        assert "--head" in pr_args and "gludd/T1-fix" in pr_args
        assert "--label" in pr_args and "gludd" in pr_args

    def test_no_pr_when_gh_missing(self) -> None:
        delivery = PRDelivery()
        with patch(
            "general_ludd.git_automation.pr_delivery.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            result = delivery.push_and_create_pr(
                repo_path="/tmp/repo",
                branch_name="b",
                todo_id="T1",
                title="t",
            )
        assert result["pr_url"] is None
        assert "gh CLI not found" in result["error"]

    def test_push_failure_does_not_create_pr(self) -> None:
        delivery = PRDelivery()
        gh_version = MagicMock(returncode=0)
        push = MagicMock(returncode=1, stderr="rejected")
        with patch(
            "general_ludd.git_automation.pr_delivery.subprocess.run",
            side_effect=[gh_version, push],
        ):
            result = delivery.push_and_create_pr(
                repo_path="/tmp/repo",
                branch_name="b",
                todo_id="T1",
                title="t",
            )
        assert result["pr_url"] is None
        assert "Push failed" in result["error"]


class TestF3IssueIngestion:
    """F3 — GitHub issues become todos, idempotently."""

    @pytest.mark.asyncio
    async def test_labeled_issue_becomes_todo_once(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r", label="gludd")
        fake_issues = [
            {"id": 1, "number": 12, "title": "Crash on boot",
             "body": "stacktrace", "labels": [{"name": "bug"}]},
        ]

        async def _fake_fetch() -> list[dict[str, object]]:
            return fake_issues

        with patch.object(ingestor, "_fetch_labeled_issues", _fake_fetch):
            todos = await ingestor.poll_issues()
            assert len(todos) == 1
            assert todos[0]["title"] == "Crash on boot"
            assert todos[0]["work_type"] == "bug_fix"
            assert todos[0]["source"] == "github:o/r#12"
            # Second poll over the same issue is idempotent (dedup by id).
            again = await ingestor.poll_issues()
            assert again == []

    @pytest.mark.asyncio
    async def test_unconfigured_ingestor_returns_no_todos(self) -> None:
        ingestor = GitHubIssueIngestor()
        assert ingestor.is_configured() is False
        assert await ingestor.poll_issues() == []
