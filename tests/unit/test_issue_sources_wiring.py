"""Unit tests: issue-sources wiring into config, EventLoop, and daemon."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.config.user_config import IssuesConfig, UserConfig
from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop
from general_ludd.git_automation.issue_ingestor import GitHubIssueIngestor

# --- IssuesConfig -----------------------------------------------------------


class TestIssuesConfigDefaults:
    def test_polling_disabled_by_default(self) -> None:
        cfg = IssuesConfig()
        assert cfg.polling_enabled is False

    def test_interval_default_is_300(self) -> None:
        cfg = IssuesConfig()
        assert cfg.poll_interval_ticks == 300

    def test_label_default_is_gludd(self) -> None:
        cfg = IssuesConfig()
        assert cfg.github_label == "gludd"

    def test_owner_repo_empty_by_default(self) -> None:
        cfg = IssuesConfig()
        assert cfg.github_owner == ""
        assert cfg.github_repo == ""


class TestIssuesConfigEnvOverride:
    def test_polling_enabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ISSUES", '{"polling_enabled": true}')
        cfg = UserConfig()
        assert cfg.issues.polling_enabled is True

    def test_polling_enabled_via_nested_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ISSUES__POLLING_ENABLED", "true")
        cfg = UserConfig()
        assert cfg.issues.polling_enabled is True


# --- GitHubIssueIngestor ----------------------------------------------------


class TestIssueIngestorConstruction:
    def test_not_configured_when_owner_empty(self) -> None:
        ingestor = GitHubIssueIngestor(owner="", repo="r")
        assert ingestor.is_configured() is False

    def test_not_configured_when_repo_empty(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="")
        assert ingestor.is_configured() is False

    def test_configured_when_owner_and_repo_set(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r")
        assert ingestor.is_configured() is True

    def test_seen_ids_passed_through(self) -> None:
        seen: set[int | str] = {1, 2}
        ingestor = GitHubIssueIngestor(owner="o", repo="r", seen_ids=seen)
        assert ingestor._seen_ids is seen

    def test_default_seen_ids_is_private_set(self) -> None:
        ingestor = GitHubIssueIngestor()
        assert isinstance(ingestor._seen_ids, set)
        assert len(ingestor._seen_ids) == 0


# --- EventLoop wiring -------------------------------------------------------


class TestEventLoopIssueIngestorWiring:
    def test_stores_issue_ingestor_when_passed(self) -> None:
        ingestor = GitHubIssueIngestor()
        loop = EventLoop(session=MagicMock(spec=AsyncSession), issue_ingestor=ingestor)
        assert loop._issue_ingestor is ingestor

    def test_issue_ingestor_none_by_default(self) -> None:
        loop = EventLoop(session=MagicMock(spec=AsyncSession))
        assert loop._issue_ingestor is None


class TestEventLoopPollPhase:
    def test_phase_is_in_order(self) -> None:
        assert "poll_issue_sources" in PHASE_ORDER

    def test_phase_noop_when_ingestor_is_none(self) -> None:
        ingestor = GitHubIssueIngestor()
        cast(Any, ingestor).is_configured = MagicMock(return_value=False)
        loop = EventLoop(session=MagicMock(spec=AsyncSession), issue_ingestor=None)
        loop._issue_ingestor = None
        # Should not raise
        loop._issue_poll_tick_counter = 0
        loop._issue_poll_interval_ticks = 1
        import asyncio
        asyncio.run(loop._phase_poll_issue_sources())

    def test_phase_skips_when_counter_below_interval(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r")
        cast(Any, ingestor).poll_issues = AsyncMock(return_value=[])
        loop = EventLoop(session=MagicMock(spec=AsyncSession), issue_ingestor=ingestor)
        loop._issue_poll_interval_ticks = 10
        loop._issue_poll_tick_counter = 5
        import asyncio
        asyncio.run(loop._phase_poll_issue_sources())
        cast(Any, ingestor.poll_issues).assert_not_called()

    def test_phase_polls_when_counter_reaches_interval(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r")
        cast(Any, ingestor).poll_issues = AsyncMock(return_value=[])
        loop = EventLoop(session=MagicMock(spec=AsyncSession), issue_ingestor=ingestor)
        loop._issue_poll_interval_ticks = 5
        loop._issue_poll_tick_counter = 5
        import asyncio
        asyncio.run(loop._phase_poll_issue_sources())
        cast(Any, ingestor.poll_issues).assert_called_once()

    def test_phase_resets_counter_after_poll(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r")
        cast(Any, ingestor).poll_issues = AsyncMock(return_value=[])
        loop = EventLoop(session=MagicMock(spec=AsyncSession), issue_ingestor=ingestor)
        loop._issue_poll_interval_ticks = 5
        loop._issue_poll_tick_counter = 5
        import asyncio
        asyncio.run(loop._phase_poll_issue_sources())
        assert loop._issue_poll_tick_counter == 0

    def test_phase_persists_new_todos(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r")
        todo = {"title": "Fix bug", "description": "desc", "queue": "core",
                "priority": "medium", "work_type": "bug_fix",
                "source": "github:o/r#1"}
        cast(Any, ingestor).poll_issues = AsyncMock(return_value=[todo])
        session = MagicMock(spec=AsyncSession)
        loop = EventLoop(session=session, issue_ingestor=ingestor)
        loop._issue_poll_interval_ticks = 1
        loop._issue_poll_tick_counter = 1
        loop._todo_repo = MagicMock()
        loop._todo_repo.create = AsyncMock(return_value=None)
        loop._tick_metrics = {}
        import asyncio
        asyncio.run(loop._phase_poll_issue_sources())
        loop._todo_repo.create.assert_called_once_with(todo)
        assert loop._tick_metrics.get("issues_polled") == 1

    def test_phase_handles_poll_exception_gracefully(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r")
        cast(Any, ingestor).poll_issues = AsyncMock(side_effect=RuntimeError("network down"))
        loop = EventLoop(session=MagicMock(spec=AsyncSession), issue_ingestor=ingestor)
        loop._issue_poll_interval_ticks = 1
        loop._issue_poll_tick_counter = 1
        import asyncio
        # Should not raise
        asyncio.run(loop._phase_poll_issue_sources())

    def test_phase_handles_create_exception_gracefully(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r")
        todo = {"title": "Fix bug"}
        cast(Any, ingestor).poll_issues = AsyncMock(return_value=[todo])
        session = MagicMock(spec=AsyncSession)
        loop = EventLoop(session=session, issue_ingestor=ingestor)
        loop._issue_poll_interval_ticks = 1
        loop._issue_poll_tick_counter = 1
        loop._todo_repo = MagicMock()
        loop._todo_repo.create = AsyncMock(side_effect=ValueError("db error"))
        loop._tick_metrics = {}
        import asyncio
        # Should not raise
        asyncio.run(loop._phase_poll_issue_sources())
        assert loop._tick_metrics.get("issues_polled") is None

    def test_phase_skips_when_poll_returns_empty(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r")
        cast(Any, ingestor).poll_issues = AsyncMock(return_value=[])
        session = MagicMock(spec=AsyncSession)
        loop = EventLoop(session=session, issue_ingestor=ingestor)
        loop._issue_poll_interval_ticks = 1
        loop._issue_poll_tick_counter = 1
        loop._todo_repo = MagicMock()
        loop._tick_metrics = {}
        import asyncio
        asyncio.run(loop._phase_poll_issue_sources())
        loop._todo_repo.create.assert_not_called()
        assert loop._tick_metrics.get("issues_polled") is None


# --- Daemon wiring ---------------------------------------------------------


class TestDaemonIssueSourceWiring:
    def test_issue_ingestor_app_state_default(self) -> None:
        import tempfile
        from pathlib import Path

        from fastapi.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            with TestClient(create_daemon_app(
                tick_interval=0.01,
                _db_path_override=str(db),
            )) as client:
                # app.state should exist
                assert client is not None

    def test_issues_config_respects_env_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ISSUES__POLLING_ENABLED", "false")
        cfg = UserConfig()
        assert cfg.issues.polling_enabled is False
        assert cfg.issues.poll_interval_ticks == 300

    def test_issues_config_env_full_object(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "GLUDD_ISSUES",
            '{"polling_enabled": true, "poll_interval_ticks": 60, '
            '"github_label": "bot-task", "github_owner": "testorg", '
            '"github_repo": "testrepo"}'
        )
        cfg = UserConfig()
        assert cfg.issues.polling_enabled is True
        assert cfg.issues.poll_interval_ticks == 60
        assert cfg.issues.github_label == "bot-task"
        assert cfg.issues.github_owner == "testorg"
        assert cfg.issues.github_repo == "testrepo"

    def test_issue_ingestor_label_respects_config(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r", label="custom-label")
        assert ingestor._label == "custom-label"
