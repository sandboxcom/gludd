from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from general_ludd.ornith.outcome_observer import (
    OutcomeObserver,
)


@pytest.fixture
def mock_session_factory():
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock()
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    factory = AsyncMock()
    factory.return_value = session

    training_repo = MagicMock()
    training_repo.set_outcome = AsyncMock()
    training_repo.get_pending_outcomes = AsyncMock(return_value=[])

    def mock_factory():
        return factory

    with patch(
        "general_ludd.ornith.outcome_observer.OrnithTrainingRepo",
        return_value=training_repo,
    ):
        yield mock_factory, training_repo


class TestOutcomeObserverInit:
    def test_default_init(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        assert obs._poll_interval == 300
        assert obs._pending_older_than_minutes == 0
        assert obs._task is None

    def test_custom_poll_interval(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory, poll_interval_seconds=60)
        assert obs._poll_interval == 60

    def test_minimum_poll_interval(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory, poll_interval_seconds=5)
        assert obs._poll_interval == 10

    def test_custom_pending_older_than(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory, pending_older_than_minutes=15)
        assert obs._pending_older_than_minutes == 15


class TestSubscriptions:
    async def test_subscribe_gate(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        called: list[tuple[str, bool]] = []

        async def listener(pair_id: str, gate_passed: bool) -> None:
            called.append((pair_id, gate_passed))

        obs.subscribe_gate(listener)
        await obs.on_gate_complete("pair-1", True)
        assert called == [("pair-1", True)]

    async def test_subscribe_gate_failure(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory)

        async def listener(pair_id: str, gate_passed: bool) -> None:
            _ = pair_id
            _ = gate_passed

        obs.subscribe_gate(listener)
        await obs.on_gate_complete("pair-2", False)

    async def test_subscribe_review(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        called: list[tuple[str, bool, str]] = []

        async def listener(pair_id: str, approved: bool, reason: str) -> None:
            called.append((pair_id, approved, reason))

        obs.subscribe_review(listener)
        await obs.on_review_decision("pair-3", False, "bad code")
        assert called == [("pair-3", False, "bad code")]

    async def test_subscribe_revert(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        called: list[tuple[str, str]] = []

        async def listener(pair_id: str, reason: str) -> None:
            called.append((pair_id, reason))

        obs.subscribe_revert(listener)
        await obs.on_commit_revert("pair-4", "security issue")
        assert called == [("pair-4", "security issue")]


class TestOnGateComplete:
    async def test_gate_passed(self, mock_session_factory):
        mock_factory, repo = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        await obs.on_gate_complete("pair-1", True)
        repo.set_outcome.assert_called()

    async def test_gate_failed(self, mock_session_factory):
        mock_factory, repo = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        await obs.on_gate_complete("pair-2", False)
        repo.set_outcome.assert_called()


class TestOnReviewDecision:
    async def test_review_rejected(self, mock_session_factory):
        mock_factory, repo = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        await obs.on_review_decision("pair-3", False, "not good")
        repo.set_outcome.assert_called()

    async def test_review_approved_does_not_set_outcome(self, mock_session_factory):
        mock_factory, repo = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        await obs.on_review_decision("pair-4", True, "looks fine")
        repo.set_outcome.assert_not_called()


class TestOnCommitRevert:
    async def test_revert_sets_outcome(self, mock_session_factory):
        mock_factory, repo = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        await obs.on_commit_revert("pair-5", "broken")
        repo.set_outcome.assert_called()


class TestMarkApplied:
    async def test_mark_applied(self, mock_session_factory):
        mock_factory, repo = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        await obs.mark_applied("pair-6")
        repo.set_outcome.assert_called()


class TestStartStop:
    async def test_start_creates_task(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        task = obs.start()
        assert task is not None
        obs._stop.set()
        task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await task

    async def test_stop_clears_task(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        obs.start()
        await obs.stop()
        assert obs._task is None

    async def test_stop_without_start(self, mock_session_factory):
        mock_factory, _ = mock_session_factory
        obs = OutcomeObserver(mock_factory)
        await obs.stop()
