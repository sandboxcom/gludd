"""Tests for outcome-driven self-improvement (G5)."""

from __future__ import annotations

import pytest

from general_ludd.self_improve.outcomes import OutcomeAnalyzer


class TestOutcomeAnalyzer:
    def test_analyze_no_data_returns_empty_suggestions(self):
        analyzer = OutcomeAnalyzer()
        result = analyzer.analyze()

        assert result == {"status": "no_data", "suggestions": []}

    def test_analyze_accepts_outcomes_and_stores_them(self):
        analyzer = OutcomeAnalyzer()
        outcomes = [
            {"task_id": "a", "status": "completed", "duration_s": 12.5},
            {"task_id": "b", "status": "failed", "duration_s": 3.0},
        ]

        result = analyzer.analyze(outcomes)

        assert result["status"] == "analyzed"
        assert result["suggestions"] == []
        assert len(analyzer._outcomes) == 2

    def test_init_respects_min_samples(self):
        analyzer = OutcomeAnalyzer(min_samples=5)
        assert analyzer.min_samples == 5

    def test_init_defaults_min_samples(self):
        analyzer = OutcomeAnalyzer()
        assert analyzer.min_samples == OutcomeAnalyzer.DEFAULT_MIN_SAMPLES

    def test_analyze_accumulates_across_calls(self):
        analyzer = OutcomeAnalyzer()
        analyzer.analyze([{"task_id": "a"}])
        result = analyzer.analyze([{"task_id": "b"}])

        assert len(analyzer._outcomes) == 2
        assert result["status"] == "analyzed"
