"""Unit tests for outcome_observer Ornith training pair resolver."""

from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.ornith.outcome_observer import OutcomeObserver


class FakeSessionFactory:
    def __init__(self, repo):
        self.repo = repo

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def commit(self):
        pass


@pytest.fixture
def observer():
    repo = AsyncMock()
    repo.set_outcome = AsyncMock()
    repo.get_pending_outcomes = AsyncMock(return_value=[])
    with patch("general_ludd.ornith.outcome_observer.OrnithTrainingRepo", return_value=repo):
        observer = OutcomeObserver(session_factory=FakeSessionFactory(repo), poll_interval_seconds=1)
        observer.repo = repo
        yield observer


class TestOutcomeObserver:
    @pytest.mark.asyncio
    async def test_on_gate_complete(self, observer):
        listener = AsyncMock()
        observer.subscribe_gate(listener)
        await observer.on_gate_complete("p1", True)
        observer.repo.set_outcome.assert_awaited_once()
        assert observer.repo.set_outcome.call_args[0][1] == "succeeded"
        listener.assert_awaited_with("p1", True)

    @pytest.mark.asyncio
    async def test_on_review_decision_approval_no_op(self, observer):
        await observer.on_review_decision("p1", True)
        observer.repo.set_outcome.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_review_decision_rejection(self, observer):
        await observer.on_review_decision("p1", False, "bad code")
        observer.repo.set_outcome.assert_awaited_once()
        assert observer.repo.set_outcome.call_args[0][1] == "rejected_by_review"

    @pytest.mark.asyncio
    async def test_on_commit_revert(self, observer):
        await observer.on_commit_revert("p1", "rollback")
        observer.repo.set_outcome.assert_awaited_once()
        assert observer.repo.set_outcome.call_args[0][1] == "reverted"

    @pytest.mark.asyncio
    async def test_mark_applied(self, observer):
        await observer.mark_applied("p1")
        observer.repo.set_outcome.assert_awaited_once()
        assert observer.repo.set_outcome.call_args[0][1] == "applied"

    @pytest.mark.asyncio
    async def test_apply_outcome_unknown_pair(self, observer):
        observer.repo.set_outcome.side_effect = KeyError("missing")
        await observer._apply_outcome("p1", "succeeded", {})

    @pytest.mark.asyncio
    async def test_poll_once_pending(self, observer):
        observer.repo.get_pending_outcomes.return_value = [{"pair_id": "p1"}]
        await observer._poll_once()
        observer.repo.get_pending_outcomes.assert_awaited_once()


class TestOutcomeObserverLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        repo = AsyncMock()
        repo.set_outcome = AsyncMock()
        repo.get_pending_outcomes = AsyncMock(return_value=[])
        observer = OutcomeObserver(session_factory=FakeSessionFactory(repo), poll_interval_seconds=1)
        observer.start()
        assert observer._task is not None
        await observer.stop()
        assert observer._task is None
