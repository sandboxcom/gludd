"""Tests for the W$ cost-adjusted scoring metric."""

from __future__ import annotations

import math

import pytest

from general_ludd.scoring.metric import MetricConfig, compute_w_dollar


class TestComputeWDollar:
    def test_zero_cost_yields_raw_score(self):
        """When cost is 0, log10(1+0)=0, denominator=0 → should handle gracefully."""
        assert compute_w_dollar(composite_score=0.85, median_dollars_per_mtok=0.0) == 0.85

    def test_high_cost_deflates_score(self):
        """A very expensive provider ($100/Mtok) should deflate the composite score."""
        w_dollar = compute_w_dollar(composite_score=0.90, median_dollars_per_mtok=100.0)
        assert 0.0 < w_dollar < 0.90

    def test_low_cost_boosts_score(self):
        """A cheap provider ($0.001/Mtok) gets a boost: W$ > W."""
        w_dollar = compute_w_dollar(composite_score=0.90, median_dollars_per_mtok=0.001)
        assert w_dollar > 0.90

    def test_cost_of_one_mtok(self):
        """At cost=1.0, log10(1+1)≈0.301, so W$ = W / 0.301."""
        w_dollar = compute_w_dollar(composite_score=1.0, median_dollars_per_mtok=1.0)
        expected = 1.0 / math.log10(2.0)
        assert abs(w_dollar - expected) < 0.0001

    def test_typical_provider_cost(self):
        """Claude Opus at ~$15/Mtok: log10(1+15)≈1.204 → W$ = W / 1.204."""
        w_dollar = compute_w_dollar(composite_score=0.92, median_dollars_per_mtok=15.0)
        expected = 0.92 / math.log10(16.0)
        assert abs(w_dollar - expected) < 0.0001

    def test_negative_cost_is_rejected(self):
        """Negative cost is nonsensical and should raise ValueError."""
        with pytest.raises(ValueError, match="median_dollars_per_mtok"):
            compute_w_dollar(composite_score=0.5, median_dollars_per_mtok=-1.0)

    def test_negative_composite_score_is_rejected(self):
        """Negative composite score should raise ValueError."""
        with pytest.raises(ValueError, match="composite_score"):
            compute_w_dollar(composite_score=-0.1, median_dollars_per_mtok=1.0)

    def test_score_above_one_is_rejected(self):
        """Composite score above 1.0 should raise ValueError."""
        with pytest.raises(ValueError, match="composite_score"):
            compute_w_dollar(composite_score=1.5, median_dollars_per_mtok=1.0)

    def test_metric_config_defaults(self):
        """MetricConfig has sensible defaults."""
        config = MetricConfig()
        assert config.log_base == 10
        assert config.offset == 1
        assert config.score_floor == 0.0
        assert config.score_ceiling == float("inf")


class TestMetricConfig:
    def test_config_override_values(self):
        config = MetricConfig(log_base=2, offset=5, score_floor=0.1, score_ceiling=0.9)
        assert config.log_base == 2
        assert config.offset == 5
        assert config.score_floor == 0.1
        assert config.score_ceiling == 0.9

    def test_config_is_frozen(self):
        """Frozen dataclass is registered as frozen."""
        config = MetricConfig()
        assert config.__dataclass_params__.frozen
