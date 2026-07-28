"""G11: Prove ConsensusEngine is invoked in the event loop review phase.

Tests the config-gated wiring: when ``consensus_review.enabled`` is true and a
ConsensusReviewer is wired, the event loop's ``_review_in_process`` calls the
consensus reviewer's ``review_return`` instead of the standard reviewer.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.task_decision import TaskDecision


def _make_tr(**overrides):
    tr = MagicMock()
    tr.return_id = "RET-001"
    tr.todo_id = "TODO-001"
    tr.job_id = "JOB-001"
    tr.playbook = "noop.yml"
    tr.queue = "model"
    tr.work_type = "review"
    tr.exit_code = 0
    tr.result_summary = "all good"
    for k, v in overrides.items():
        setattr(tr, k, v)
    return tr


def _make_decision(decision: str = "complete") -> TaskDecision:
    return TaskDecision(
        return_id="RET-001",
        matched_todo_id="TODO-001",
        decision=decision,
        confidence=0.99,
    )


def _fake_to_thread():
    """asyncio.to_thread shim that runs the synchronous fn inline."""

    async def _runner(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    return _runner


class TestConsensusReviewWiring:
    @pytest.mark.asyncio
    async def test_consensus_enabled_uses_consensus_reviewer(self):
        """When consensus_review.enabled=True and consensus_reviewer is wired,
        _review_in_process calls consensus_reviewer.review_return."""
        consensus_reviewer = MagicMock()
        consensus_reviewer.review_return.return_value = _make_decision("complete")

        standard_reviewer = MagicMock()
        standard_reviewer.review_return.return_value = _make_decision("needs_more_work")

        todo_repo = AsyncMock()
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result

        loop = EventLoop(
            reviewer=standard_reviewer,
            consensus_reviewer=consensus_reviewer,
            todo_repo=todo_repo,
            session=session,
            config={"consensus_review": {"enabled": True}},
        )

        tr = _make_tr()

        with patch(
            "general_ludd.event_loop.loop.asyncio.to_thread", _fake_to_thread()
        ), patch(
            "general_ludd.review.decision_applier.apply_decision",
            new_callable=AsyncMock,
        ):
            await loop._review_in_process(tr)

        # Consensus reviewer was called — NOT the standard reviewer.
        consensus_reviewer.review_return.assert_called_once()
        standard_reviewer.review_return.assert_not_called()

    @pytest.mark.asyncio
    async def test_consensus_disabled_uses_standard_reviewer(self):
        """When consensus_review.enabled=False, _review_in_process calls
        the standard reviewer even if a consensus_reviewer is wired."""
        consensus_reviewer = MagicMock()
        consensus_reviewer.review_return.return_value = _make_decision("complete")

        standard_reviewer = MagicMock()
        standard_reviewer.review_return.return_value = _make_decision("complete")

        todo_repo = AsyncMock()
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result

        loop = EventLoop(
            reviewer=standard_reviewer,
            consensus_reviewer=consensus_reviewer,
            todo_repo=todo_repo,
            session=session,
            config={"consensus_review": {"enabled": False}},
        )

        tr = _make_tr()

        with patch(
            "general_ludd.event_loop.loop.asyncio.to_thread", _fake_to_thread()
        ), patch(
            "general_ludd.review.decision_applier.apply_decision",
            new_callable=AsyncMock,
        ):
            await loop._review_in_process(tr)

        standard_reviewer.review_return.assert_called_once()
        consensus_reviewer.review_return.assert_not_called()

    @pytest.mark.asyncio
    async def test_consensus_enabled_no_consensus_wired_uses_standard(self):
        """When consensus_review.enabled=True but no consensus_reviewer is
        wired, falls back to standard reviewer (must be present)."""
        standard_reviewer = MagicMock()
        standard_reviewer.review_return.return_value = _make_decision("complete")

        todo_repo = AsyncMock()
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result

        loop = EventLoop(
            reviewer=standard_reviewer,
            consensus_reviewer=None,
            todo_repo=todo_repo,
            session=session,
            config={"consensus_review": {"enabled": True}},
        )

        tr = _make_tr()

        with patch(
            "general_ludd.event_loop.loop.asyncio.to_thread", _fake_to_thread()
        ), patch(
            "general_ludd.review.decision_applier.apply_decision",
            new_callable=AsyncMock,
        ):
            await loop._review_in_process(tr)

        standard_reviewer.review_return.assert_called_once()

    @pytest.mark.asyncio
    async def test_consensus_enabled_asserts_if_no_reviewer_at_all(self):
        """When consensus_review.enabled=True but neither reviewer is wired,
        _review_in_process asserts (must raise AssertionError)."""
        todo_repo = AsyncMock()
        session = AsyncMock()

        loop = EventLoop(
            reviewer=None,
            consensus_reviewer=None,
            todo_repo=todo_repo,
            session=session,
            config={"consensus_review": {"enabled": True}},
        )

        tr = _make_tr()

        with pytest.raises(AssertionError):
            await loop._review_in_process(tr)

    @pytest.mark.asyncio
    async def test_dispatch_review_job_enters_with_consensus_only(self):
        """_dispatch_review_job allows entry when consensus_reviewer is wired
        and enabled, even if standard reviewer is None."""
        consensus_reviewer = MagicMock()
        consensus_reviewer.review_return.return_value = _make_decision("complete")

        todo_repo = AsyncMock()
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result

        loop = EventLoop(
            reviewer=None,
            consensus_reviewer=consensus_reviewer,
            todo_repo=todo_repo,
            session=session,
            config={"consensus_review": {"enabled": True}},
        )
        loop._active_session = session
        loop._todo_repo = todo_repo
        loop._task_return_repo = AsyncMock()

        tr = _make_tr()
        call_order: list[str] = []

        original_review = loop._review_in_process

        async def _tracking_review(tr_arg):
            call_order.append("review_in_process")
            await original_review(tr_arg)

        cast(Any, loop)._review_in_process = _tracking_review

        with patch(
            "general_ludd.event_loop.loop.asyncio.to_thread", _fake_to_thread()
        ), patch(
            "general_ludd.review.decision_applier.apply_decision",
            new_callable=AsyncMock,
        ):
            await loop._dispatch_review_job(tr)

        assert "review_in_process" in call_order
        consensus_reviewer.review_return.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_review_job_skips_when_no_reviewer(self):
        """_dispatch_review_job falls through to playbook/HTTP dispatch when
        neither reviewer nor consensus_reviewer is available."""
        session = AsyncMock()
        session.flush = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result

        loop = EventLoop(
            reviewer=None,
            consensus_reviewer=None,
            session=session,
            config={"consensus_review": {"enabled": True}},
        )
        loop._active_session = session

        tr = _make_tr()

        call_order: list[str] = []
        original_review = loop._review_in_process

        async def _tracking_review(tr_arg):
            call_order.append("review_in_process")
            await original_review(tr_arg)

        cast(Any, loop)._review_in_process = _tracking_review

        await loop._dispatch_review_job(tr)

        assert "review_in_process" not in call_order

    @pytest.mark.asyncio
    async def test_consensus_disabled_skips_when_no_standard_reviewer(self):
        """_dispatch_review_job does NOT enter in-process review when
        consensus is disabled and no standard reviewer is wired."""
        session = AsyncMock()

        consensus_reviewer = MagicMock()

        loop = EventLoop(
            reviewer=None,
            consensus_reviewer=consensus_reviewer,
            session=session,
            config={"consensus_review": {"enabled": False}},
        )
        loop._active_session = session

        tr = _make_tr()

        call_order: list[str] = []
        original_review = loop._review_in_process

        async def _tracking_review(tr_arg):
            call_order.append("review_in_process")
            await original_review(tr_arg)

        cast(Any, loop)._review_in_process = _tracking_review

        await loop._dispatch_review_job(tr)

        assert "review_in_process" not in call_order

    @pytest.mark.asyncio
    async def test_consensus_reviewer_receives_task_return(self):
        """The consensus reviewer receives the correct TaskReturn fields."""
        consensus_reviewer = MagicMock()
        consensus_reviewer.review_return.return_value = _make_decision("complete")

        todo_repo = AsyncMock()
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result

        loop = EventLoop(
            reviewer=MagicMock(),
            consensus_reviewer=consensus_reviewer,
            todo_repo=todo_repo,
            session=session,
            config={"consensus_review": {"enabled": True}},
        )

        tr = _make_tr(
            return_id="RET-Z1",
            todo_id="TODO-Z1",
            result_summary="Fixed the bug.",
            exit_code=0,
            playbook="bug_fix.yml",
        )

        with patch(
            "general_ludd.event_loop.loop.asyncio.to_thread", _fake_to_thread()
        ), patch(
            "general_ludd.review.decision_applier.apply_decision",
            new_callable=AsyncMock,
        ):
            await loop._review_in_process(tr)

        call_args = consensus_reviewer.review_return.call_args
        task_return = call_args[0][0]
        assert task_return.return_id == "RET-Z1"
        assert task_return.todo_id == "TODO-Z1"
        assert task_return.result_summary == "Fixed the bug."
        assert task_return.exit_code == 0
        assert task_return.playbook == "bug_fix.yml"

    @pytest.mark.asyncio
    async def test_consensus_config_absent_defaults_to_standard(self):
        """When consensus_review key is absent from config, standard reviewer is used."""
        standard_reviewer = MagicMock()
        standard_reviewer.review_return.return_value = _make_decision("complete")

        consensus_reviewer = MagicMock()

        todo_repo = AsyncMock()
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result

        loop = EventLoop(
            reviewer=standard_reviewer,
            consensus_reviewer=consensus_reviewer,
            todo_repo=todo_repo,
            session=session,
            config={},
        )

        tr = _make_tr()

        with patch(
            "general_ludd.event_loop.loop.asyncio.to_thread", _fake_to_thread()
        ), patch(
            "general_ludd.review.decision_applier.apply_decision",
            new_callable=AsyncMock,
        ):
            await loop._review_in_process(tr)

        standard_reviewer.review_return.assert_called_once()
        consensus_reviewer.review_return.assert_not_called()
