"""OutcomeAnalyzer wiring proofs.

Proves that OutcomeAnalyzer from self_improve/outcomes.py is wired into the
self-improve apply pipeline (EventLoop._apply_self_improvements).
"""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock

import pytest


class TestOutcomeAnalyzerConstructability:
    """OutcomeAnalyzer can be imported and constructed."""

    def test_constructs_with_defaults(self):
        from general_ludd.self_improve.outcomes import OutcomeAnalyzer

        analyzer = OutcomeAnalyzer()
        assert analyzer.min_samples == 10
        assert analyzer._outcomes == []

    def test_constructs_with_custom_min_samples(self):
        from general_ludd.self_improve.outcomes import OutcomeAnalyzer

        analyzer = OutcomeAnalyzer(min_samples=5)
        assert analyzer.min_samples == 5

    def test_analyze_returns_no_data_when_empty(self):
        from general_ludd.self_improve.outcomes import OutcomeAnalyzer

        analyzer = OutcomeAnalyzer()
        result = analyzer.analyze()
        assert result["status"] == "no_data"
        assert result["suggestions"] == []

    def test_analyze_aggregates_outcomes(self):
        from general_ludd.self_improve.outcomes import OutcomeAnalyzer

        analyzer = OutcomeAnalyzer()
        outcomes = [
            {"case_id": "c1", "task_type": "code", "model": "sonnet",
             "passed": False, "tokens_used": 100, "duration_ms": 500},
            {"case_id": "c2", "task_type": "code", "model": "sonnet",
             "passed": False, "tokens_used": 200, "duration_ms": 600},
        ]
        result = analyzer.analyze(outcomes=outcomes, threshold=0.5)
        assert result["status"] == "analyzed"
        assert len(result["suggestions"]) >= 1


class TestOutcomeAnalyzerWiredInApplyPath:
    """OutcomeAnalyzer is imported and constructable from EventLoop._apply_self_improvements."""

    def test_event_loop_imports_outcome_analyzer(self):
        """_apply_self_improvements imports OutcomeAnalyzer."""
        import inspect

        from general_ludd.event_loop.loop import EventLoop

        source = textwrap.dedent(
            inspect.getsource(EventLoop._apply_self_improvements)
        )

        assert (
            "OutcomeAnalyzer" in source
        ), (
            "EventLoop._apply_self_improvements must reference OutcomeAnalyzer"
        )

        assert (
            "from general_ludd.self_improve.outcomes import" in source
            or "from general_ludd.self_improve.outcomes import OutcomeAnalyzer" in source
        ), (
            "EventLoop._apply_self_improvements must import from "
            "general_ludd.self_improve.outcomes"
        )

    def test_apply_self_improvements_constructs_analyzer(self):
        """_apply_self_improvements constructs an OutcomeAnalyzer instance."""
        import inspect

        from general_ludd.event_loop.loop import EventLoop

        source = textwrap.dedent(
            inspect.getsource(EventLoop._apply_self_improvements)
        )

        assert "OutcomeAnalyzer(" in source, (
            "EventLoop._apply_self_improvements must construct an OutcomeAnalyzer instance"
        )

    @pytest.mark.asyncio
    async def test_apply_self_improvements_runs_with_wired_analyzer(self, monkeypatch):
        """_apply_self_improvements runs successfully with the wired analyzer."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop()

        dummy_session = MagicMock()
        dummy_session.commit = MagicMock(return_value=None)
        dummy_session.close = MagicMock(return_value=None)
        dummy_factory = MagicMock()

        async def _fake_aenter(self):
            return dummy_session

        async def _fake_aexit(self, *args):
            return None

        dummy_factory.__aenter__ = _fake_aenter
        dummy_factory.__aexit__ = _fake_aexit
        loop._session_factory = lambda: dummy_factory

        class FakeCollector:
            def __init__(self, session):
                pass

            async def quality_report(self):
                return {
                    "total_pairs": 5, "resolved": 5,
                    "positive_examples": 3, "negative_examples": 2,
                }

            async def list_by_statuses(self, statuses, limit, lookback_days):
                return [
                    MagicMock(
                        instruction="premature stop detected",
                    ),
                    MagicMock(
                        instruction="grind failure on main thread",
                    ),
                ]

        monkeypatch.setattr(
            "general_ludd.ornith.training_data.TrainingDataCollector",
            FakeCollector,
        )

        # Should not raise — the wired OutcomeAnalyzer is constructed but
        # currently its result is only logged; no assertion needed beyond
        # not raising.
        await loop._apply_self_improvements()

    @pytest.mark.asyncio
    async def test_apply_self_improvements_skips_when_no_factory(self):
        """_apply_self_improvements returns early when no session factory exists."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop()
        loop._session_factory = None
        await loop._apply_self_improvements()
