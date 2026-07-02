"""Tests for the per-task token-cost tracker (which tasks are token-heavy)."""

from __future__ import annotations

import pytest

from general_ludd.observability.token_cost import (
    TokenCostTracker,
    default_token_tracker,
)


class TestRecordAndBaseline:
    def test_baseline_is_median_total(self):
        t = TokenCostTracker(min_samples=3)
        t.record("feature", 100, 50)   # total 150
        t.record("feature", 200, 100)  # total 300
        t.record("feature", 300, 150)  # total 450
        assert t.baseline_total("feature") == 300  # median of 150/300/450

    def test_none_until_min_samples(self):
        t = TokenCostTracker(min_samples=3)
        t.record("feature", 100, 50)
        t.record("feature", 200, 100)
        assert t.baseline_total("feature") is None
        assert t.weight("feature") is None

    def test_weight_exposes_input_output_medians(self):
        t = TokenCostTracker(min_samples=3)
        for _ in range(3):
            t.record("docs", 40, 10)
        w = t.weight("docs")
        assert w is not None
        assert w.median_input == 40
        assert w.median_output == 10
        assert w.median_total == 50
        assert w.samples == 3

    def test_negative_tokens_ignored(self):
        t = TokenCostTracker(min_samples=1)
        t.record("x", -5, 10)
        t.record("x", 10, -5)
        assert t.baseline_total("x") is None  # both rejected

    def test_window_keeps_only_recent(self):
        t = TokenCostTracker(window=3, min_samples=1)
        for total in (10, 10, 10, 1000, 1000, 1000):
            t.record("k", total, 0)
        # Only the last 3 (all 1000) survive the window.
        assert t.baseline_total("k") == 1000


class TestHeaviest:
    def _seed(self) -> TokenCostTracker:
        t = TokenCostTracker(min_samples=3)
        for _ in range(3):
            t.record("light_task", 60, 40)      # total 100
            t.record("mid_task", 600, 400)       # total 1000
            t.record("heavy_task", 6000, 4000)   # total 10000
        return t

    def test_ranks_by_total_descending(self):
        t = self._seed()
        ranked = t.heaviest()
        assert [w.key for w in ranked] == ["heavy_task", "mid_task", "light_task"]

    def test_top_n_caps_result(self):
        t = self._seed()
        top1 = t.heaviest(n=1)
        assert len(top1) == 1
        assert top1[0].key == "heavy_task"

    def test_classify_relative_to_cross_key_median(self):
        t = self._seed()
        assert t.classify("heavy_task") == "heavy"
        assert t.classify("light_task") == "light"
        assert t.classify("mid_task") == "moderate"

    def test_classify_unknown_without_baseline(self):
        t = TokenCostTracker(min_samples=3)
        t.record("new", 10, 10)
        assert t.classify("new") == "unknown"


class TestConstructionAndSingleton:
    def test_invalid_params_raise(self):
        with pytest.raises(ValueError):
            TokenCostTracker(window=0)
        with pytest.raises(ValueError):
            TokenCostTracker(min_samples=0)
        with pytest.raises(ValueError):
            TokenCostTracker(heavy_factor=1.0)

    def test_default_token_tracker_is_shared(self):
        a = default_token_tracker()
        b = default_token_tracker()
        assert a is b
