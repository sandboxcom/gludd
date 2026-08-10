"""Unit tests for general_ludd.ai_ml.evaluation (AIML-016).

Covers:
  - MetricKind enum values
  - MetricScore construction, validation, str->MetricKind coercion
  - BenchmarkResult construction, validation, overall_score, score_for
  - EvaluationHarness.one_verdict: regression detection
  - EvaluationHarness.compare: worst-regression selection
  - EvaluationHarness.promotion_gate: promote/block logic with safety hard-block
  - EvaluationHarness.run_benchmark
  - _HARD_BLOCK_METRICS frozen set
"""

from __future__ import annotations

from typing import cast

import pytest

from general_ludd.ai_ml.evaluation import (
    _HARD_BLOCK_METRICS,
    BenchmarkResult,
    EvaluationHarness,
    MetricKind,
    MetricScore,
    PromotionDecision,
    RegressionVerdict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _score(metric: MetricKind, value: float, higher_is_better: bool = True, unit: str = "") -> MetricScore:
    return MetricScore(metric=metric, value=value, higher_is_better=higher_is_better, unit=unit)


def _result(candidate_id: str, suite_id: str, run_id: str, *scores: MetricScore) -> BenchmarkResult:
    return BenchmarkResult(candidate_id=candidate_id, suite_id=suite_id, run_id=run_id, scores=scores)


# ---------------------------------------------------------------------------
# MetricKind
# ---------------------------------------------------------------------------


class TestMetricKind:
    def test_all_seven_families_exist(self) -> None:
        assert len(MetricKind) == 7
        for k in ("quality", "safety", "latency_ms", "cost_usd", "energy_kwh", "robustness", "calibration"):
            assert MetricKind(k)

    def test_str_identity(self) -> None:
        assert str(MetricKind.QUALITY) == "quality"
        assert str(MetricKind.SAFETY) == "safety"

    def test_hard_block_metrics_is_safety_only(self) -> None:
        assert frozenset({MetricKind.SAFETY}) == _HARD_BLOCK_METRICS


# ---------------------------------------------------------------------------
# MetricScore
# ---------------------------------------------------------------------------


class TestMetricScoreConstruction:
    def test_simple_construction(self) -> None:
        s = MetricScore(metric=MetricKind.QUALITY, value=0.95, higher_is_better=True, unit="f1")
        assert s.metric is MetricKind.QUALITY
        assert s.value == 0.95
        assert s.higher_is_better is True
        assert s.unit == "f1"

    def test_str_metric_coerced_to_enum(self) -> None:
        s = MetricScore(metric=cast(MetricKind, "safety"), value=0.88, higher_is_better=True)
        assert s.metric is MetricKind.SAFETY

    def test_int_value_allowed(self) -> None:
        s = MetricScore(metric=MetricKind.QUALITY, value=1, higher_is_better=True)
        assert s.value == 1

    def test_non_string_non_enum_falls_back_to_quality(self) -> None:
        s = MetricScore(metric=cast(MetricKind, 42), value=0.5, higher_is_better=True)
        assert s.metric is MetricKind.QUALITY

    def test_negative_value_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            MetricScore(metric=MetricKind.QUALITY, value=-0.1, higher_is_better=True)

    def test_non_numeric_value_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            MetricScore(metric=MetricKind.QUALITY, value="hi", higher_is_better=True)  # type: ignore[arg-type]

    def test_zero_value_allowed(self) -> None:
        s = MetricScore(metric=MetricKind.ENERGY_KWH, value=0.0, higher_is_better=False, unit="kWh")
        assert s.value == 0.0

    def test_default_unit_empty_string(self) -> None:
        s = MetricScore(metric=MetricKind.QUALITY, value=0.9, higher_is_better=True)
        assert s.unit == ""

    def test_frozen(self) -> None:
        s = MetricScore(metric=MetricKind.QUALITY, value=0.5, higher_is_better=True)
        with pytest.raises(AttributeError):
            s.value = 0.9  # type: ignore[misc]


class TestMetricScoreDirectionSemantics:
    """lower-is-better metrics: LATENCY_MS, COST_USD, ENERGY_KWH, CALIBRATION."""

    def test_latency_is_lower_is_better(self) -> None:
        s = MetricScore(metric=MetricKind.LATENCY_MS, value=120.0, higher_is_better=False)
        assert s.higher_is_better is False

    def test_cost_is_lower_is_better(self) -> None:
        s = MetricScore(metric=MetricKind.COST_USD, value=0.003, higher_is_better=False)
        assert s.higher_is_better is False


# ---------------------------------------------------------------------------
# BenchmarkResult
# ---------------------------------------------------------------------------


class TestBenchmarkResultConstruction:
    def test_valid_construction(self) -> None:
        r = _result("cand-1", "suite-a", "run-01", _score(MetricKind.QUALITY, 0.9))
        assert r.candidate_id == "cand-1"
        assert r.suite_id == "suite-a"
        assert r.run_id == "run-01"
        assert len(r.scores) == 1

    def test_multiple_scores(self) -> None:
        scores = (
            _score(MetricKind.QUALITY, 0.9),
            _score(MetricKind.SAFETY, 0.85),
            _score(MetricKind.LATENCY_MS, 100.0, higher_is_better=False),
        )
        r = _result("cand-1", "suite-a", "run-01", *scores)
        assert len(r.scores) == 3

    def test_empty_candidate_id_raises(self) -> None:
        with pytest.raises(ValueError, match="candidate_id"):
            _result("", "s", "r")

    def test_whitespace_candidate_id_raises(self) -> None:
        with pytest.raises(ValueError, match="candidate_id"):
            _result("   ", "s", "r")

    def test_empty_suite_id_raises(self) -> None:
        with pytest.raises(ValueError, match="suite_id"):
            _result("c", "", "r")

    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            _result("c", "s", "")

    def test_empty_scores_raises(self) -> None:
        with pytest.raises(ValueError, match="scores must contain"):
            BenchmarkResult(candidate_id="c", suite_id="s", run_id="r", scores=())

    def test_frozen(self) -> None:
        r = _result("c", "s", "r", _score(MetricKind.QUALITY, 0.5))
        with pytest.raises(AttributeError):
            r.candidate_id = "x"  # type: ignore[misc]


class TestBenchmarkResultOverallScore:
    def test_single_high_score_higher_is_better(self) -> None:
        r = _result("c", "s", "r", _score(MetricKind.QUALITY, 0.95))
        assert r.overall_score == pytest.approx(0.95)

    def test_single_low_score_higher_is_better(self) -> None:
        r = _result("c", "s", "r", _score(MetricKind.QUALITY, 0.10))
        assert r.overall_score == pytest.approx(0.10)

    def test_lower_is_better_inversion(self) -> None:
        r = _result("c", "s", "r", _score(MetricKind.LATENCY_MS, 0.2, higher_is_better=False))
        assert r.overall_score == pytest.approx(0.8)

    def test_lower_is_better_above_one_squashed(self) -> None:
        r = _result("c", "s", "r", _score(MetricKind.LATENCY_MS, 120.0, higher_is_better=False))
        assert r.overall_score == pytest.approx(1.0 / (1.0 + 120.0))

    def test_higher_is_better_above_one_squashed(self) -> None:
        r = _result("c", "s", "r", _score(MetricKind.QUALITY, 5.0))
        assert r.overall_score == pytest.approx(1.0 / 6.0)

    def test_composite_mean_multiple_metrics(self) -> None:
        scores = (
            _score(MetricKind.QUALITY, 0.8),  # 0.8
            _score(MetricKind.SAFETY, 0.9),  # 0.9
            _score(MetricKind.LATENCY_MS, 0.3, higher_is_better=False),  # 1 - 0.3 = 0.7
        )
        r = _result("c", "s", "r", *scores)
        expected = (0.8 + 0.9 + 0.7) / 3.0
        assert r.overall_score == pytest.approx(expected)


class TestBenchmarkResultScoreFor:
    def test_score_for_existing_metric(self) -> None:
        r = _result("c", "s", "r", _score(MetricKind.QUALITY, 0.9))
        found = r.score_for(MetricKind.QUALITY)
        assert found is not None
        assert found.value == 0.9

    def test_score_for_missing_metric(self) -> None:
        r = _result("c", "s", "r", _score(MetricKind.QUALITY, 0.9))
        assert r.score_for(MetricKind.SAFETY) is None

    def test_score_for_with_multiple_scores(self) -> None:
        scores = (
            _score(MetricKind.QUALITY, 0.9),
            _score(MetricKind.SAFETY, 0.85),
        )
        r = _result("c", "s", "r", *scores)
        assert r.score_for(MetricKind.SAFETY).value == 0.85  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# RegressionVerdict
# ---------------------------------------------------------------------------


class TestRegressionVerdict:
    def test_construction(self) -> None:
        v = RegressionVerdict(
            metric=MetricKind.QUALITY,
            baseline_value=0.9,
            candidate_value=0.8,
            delta=-0.1,
            is_regression=True,
            is_statistically_significant=True,
        )
        assert v.metric is MetricKind.QUALITY
        assert v.is_regression is True
        assert v.is_statistically_significant is True

    def test_frozen(self) -> None:
        v = RegressionVerdict(
            metric=MetricKind.QUALITY,
            baseline_value=0.9,
            candidate_value=0.9,
            delta=0.0,
            is_regression=False,
            is_statistically_significant=False,
        )
        with pytest.raises(AttributeError):
            v.delta = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PromotionDecision
# ---------------------------------------------------------------------------


class TestPromotionDecision:
    def test_promote_true(self) -> None:
        d = PromotionDecision(promote=True)
        assert d.promote is True
        assert d.blocked_metrics == ()
        assert d.regressions == ()
        assert d.reason == ""

    def test_promote_false_with_blocked_reason(self) -> None:
        d = PromotionDecision(
            promote=False,
            blocked_metrics=(MetricKind.LATENCY_MS,),
            reason="promotion blocked: 1 metric(s) regressed (latency_ms)",
        )
        assert d.promote is False
        assert MetricKind.LATENCY_MS in d.blocked_metrics

    def test_frozen(self) -> None:
        d = PromotionDecision(promote=True)
        with pytest.raises(AttributeError):
            d.promote = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EvaluationHarness — _one_verdict
# ---------------------------------------------------------------------------


class TestHarnessOneVerdict:
    def harness(self, threshold: float = 0.02) -> EvaluationHarness:
        return EvaluationHarness(significance_threshold=threshold)

    def test_quality_regression(self) -> None:
        base = _score(MetricKind.QUALITY, 0.95)
        cand = _score(MetricKind.QUALITY, 0.85)
        v = self.harness()._one_verdict(base, cand)
        assert v.is_regression is True
        assert v.delta == pytest.approx(-0.10)
        assert v.is_statistically_significant is True  # |delta| >= 0.02

    def test_quality_improvement(self) -> None:
        base = _score(MetricKind.QUALITY, 0.85)
        cand = _score(MetricKind.QUALITY, 0.95)
        v = self.harness()._one_verdict(base, cand)
        assert v.is_regression is False
        assert v.delta == pytest.approx(0.10)
        assert v.is_statistically_significant is False

    def test_latency_regression(self) -> None:
        base = _score(MetricKind.LATENCY_MS, 100.0, higher_is_better=False)
        cand = _score(MetricKind.LATENCY_MS, 150.0, higher_is_better=False)
        v = self.harness()._one_verdict(base, cand)
        assert v.is_regression is True
        assert v.delta == -50.0
        assert v.is_statistically_significant is True

    def test_latency_improvement(self) -> None:
        base = _score(MetricKind.LATENCY_MS, 150.0, higher_is_better=False)
        cand = _score(MetricKind.LATENCY_MS, 100.0, higher_is_better=False)
        v = self.harness()._one_verdict(base, cand)
        assert v.is_regression is False
        assert v.delta == 50.0  # baseline - candidate = positive (improvement)
        assert v.is_statistically_significant is False

    def test_small_regression_below_threshold(self) -> None:
        base = _score(MetricKind.QUALITY, 0.95)
        cand = _score(MetricKind.QUALITY, 0.949)
        v = self.harness(threshold=0.01)._one_verdict(base, cand)
        assert v.is_regression is True
        assert v.is_statistically_significant is False  # |delta| = 0.001 < 0.01

    def test_no_difference(self) -> None:
        base = _score(MetricKind.QUALITY, 0.9)
        cand = _score(MetricKind.QUALITY, 0.9)
        v = self.harness()._one_verdict(base, cand)
        assert v.is_regression is False
        assert v.delta == 0.0
        assert v.is_statistically_significant is False

    def test_metric_mismatch_raises(self) -> None:
        base = _score(MetricKind.QUALITY, 0.9)
        cand = _score(MetricKind.SAFETY, 0.9)
        with pytest.raises(ValueError, match="metric mismatch"):
            self.harness()._one_verdict(base, cand)

    def test_cost_regression(self) -> None:
        base = _score(MetricKind.COST_USD, 0.003, higher_is_better=False)
        cand = _score(MetricKind.COST_USD, 0.005, higher_is_better=False)
        v = self.harness()._one_verdict(base, cand)
        assert v.is_regression is True
        assert v.delta < 0


# ---------------------------------------------------------------------------
# EvaluationHarness — compare
# ---------------------------------------------------------------------------


class TestHarnessCompare:
    def harness(self, threshold: float = 0.02) -> EvaluationHarness:
        return EvaluationHarness(significance_threshold=threshold)

    def test_all_improved_returns_non_regression(self) -> None:
        base = _result(
            "base",
            "s",
            "r1",
            _score(MetricKind.QUALITY, 0.8),
            _score(MetricKind.SAFETY, 0.85),
            _score(MetricKind.LATENCY_MS, 150.0, higher_is_better=False),
        )
        cand = _result(
            "cand",
            "s",
            "r2",
            _score(MetricKind.QUALITY, 0.9),
            _score(MetricKind.SAFETY, 0.9),
            _score(MetricKind.LATENCY_MS, 100.0, higher_is_better=False),
        )
        v = self.harness().compare(base, cand)
        assert v.is_regression is False
        assert v.is_statistically_significant is False

    def test_worst_significant_regression_returned(self) -> None:
        base = _result(
            "base",
            "s",
            "r1",
            _score(MetricKind.QUALITY, 0.95),
            _score(MetricKind.LATENCY_MS, 100.0, higher_is_better=False),
        )
        cand = _result(
            "cand",
            "s",
            "r2",
            _score(MetricKind.QUALITY, 0.85),
            _score(MetricKind.LATENCY_MS, 200.0, higher_is_better=False),
        )
        v = self.harness().compare(base, cand)
        assert v.is_regression is True
        assert v.is_statistically_significant is True
        # latency regression: delta = 100 - 200 = -100 is larger than quality delta = -0.10
        assert v.metric is MetricKind.LATENCY_MS

    def test_no_overlapping_metrics_returns_neutral_verdict(self) -> None:
        base = _result("base", "s", "r1", _score(MetricKind.QUALITY, 0.9))
        cand = _result("cand", "s", "r2", _score(MetricKind.SAFETY, 0.85))
        v = self.harness().compare(base, cand)
        assert v.is_regression is False
        assert v.is_statistically_significant is False
        assert v.metric is MetricKind.QUALITY
        assert v.delta == 0.0

    def test_safety_regression_wins_as_worst(self) -> None:
        base = _result(
            "base",
            "s",
            "r1",
            _score(MetricKind.QUALITY, 0.9),
            _score(MetricKind.SAFETY, 0.95),
        )
        cand = _result(
            "cand",
            "s",
            "r2",
            _score(MetricKind.QUALITY, 0.8),
            _score(MetricKind.SAFETY, 0.80),
        )
        v = self.harness().compare(base, cand)
        assert v.is_regression is True
        assert v.metric is MetricKind.SAFETY  # safety dropped more


# ---------------------------------------------------------------------------
# EvaluationHarness — promotion_gate
# ---------------------------------------------------------------------------


class TestHarnessPromotionGate:
    def harness(self, threshold: float = 0.02) -> EvaluationHarness:
        return EvaluationHarness(significance_threshold=threshold)

    def test_all_improved_promotes(self) -> None:
        base = _result(
            "base",
            "s",
            "r1",
            _score(MetricKind.QUALITY, 0.8),
            _score(MetricKind.LATENCY_MS, 150.0, higher_is_better=False),
        )
        cand = _result(
            "cand",
            "s",
            "r2",
            _score(MetricKind.QUALITY, 0.9),
            _score(MetricKind.LATENCY_MS, 100.0, higher_is_better=False),
        )
        d = self.harness().promotion_gate(base, cand)
        assert d.promote is True
        assert d.blocked_metrics == ()

    def test_significant_regression_blocks(self) -> None:
        base = _result("base", "s", "r1", _score(MetricKind.QUALITY, 0.95))
        cand = _result("cand", "s", "r2", _score(MetricKind.QUALITY, 0.80))
        d = self.harness().promotion_gate(base, cand)
        assert d.promote is False
        assert MetricKind.QUALITY in d.blocked_metrics
        assert "promotion blocked" in d.reason

    def test_safety_any_regression_blocks_even_insignificant(self) -> None:
        base = _result("base", "s", "r1", _score(MetricKind.SAFETY, 0.95))
        # Tiny regression — delta = -0.005, below threshold of 0.02
        cand = _result("cand", "s", "r2", _score(MetricKind.SAFETY, 0.945))
        d = self.harness(threshold=0.02).promotion_gate(base, cand)
        assert d.promote is False
        assert MetricKind.SAFETY in d.blocked_metrics

    def test_custom_hard_block_metrics(self) -> None:
        h = EvaluationHarness(
            significance_threshold=0.02,
            hard_block_metrics=frozenset({MetricKind.QUALITY, MetricKind.SAFETY}),
        )
        base = _result("base", "s", "r1", _score(MetricKind.QUALITY, 0.95))
        cand = _result("cand", "s", "r2", _score(MetricKind.QUALITY, 0.949))
        d = h.promotion_gate(base, cand)
        assert d.promote is False

    def test_insignificant_non_hard_block_regression_allows_promotion(self) -> None:
        base = _result("base", "s", "r1", _score(MetricKind.LATENCY_MS, 100.0, higher_is_better=False))
        cand = _result("cand", "s", "r2", _score(MetricKind.LATENCY_MS, 100.5, higher_is_better=False))
        d = self.harness(threshold=2.0).promotion_gate(base, cand)
        assert d.promote is True

    def test_multiple_regressions_all_blocked(self) -> None:
        base = _result(
            "base",
            "s",
            "r1",
            _score(MetricKind.QUALITY, 0.95),
            _score(MetricKind.LATENCY_MS, 100.0, higher_is_better=False),
            _score(MetricKind.SAFETY, 0.95),
        )
        cand = _result(
            "cand",
            "s",
            "r2",
            _score(MetricKind.QUALITY, 0.80),
            _score(MetricKind.LATENCY_MS, 200.0, higher_is_better=False),
            _score(MetricKind.SAFETY, 0.80),
        )
        d = self.harness().promotion_gate(base, cand)
        assert d.promote is False
        assert MetricKind.QUALITY in d.blocked_metrics
        assert MetricKind.LATENCY_MS in d.blocked_metrics
        assert MetricKind.SAFETY in d.blocked_metrics
        assert len(d.blocked_metrics) == 3


# ---------------------------------------------------------------------------
# EvaluationHarness — run_benchmark
# ---------------------------------------------------------------------------


class TestHarnessRunBenchmark:
    def test_returns_valid_benchmark_result(self) -> None:
        h = EvaluationHarness()
        scores = (_score(MetricKind.QUALITY, 0.9),)
        r = h.run_benchmark("cand-1", "suite-a", scores)
        assert r.candidate_id == "cand-1"
        assert r.suite_id == "suite-a"
        assert isinstance(r.run_id, str)
        assert r.run_id.startswith("eval-")
        assert len(r.run_id) > 5

    def test_unique_run_ids(self) -> None:
        h = EvaluationHarness()
        scores = (_score(MetricKind.QUALITY, 0.9),)
        r1 = h.run_benchmark("c", "s", scores)
        r2 = h.run_benchmark("c", "s", scores)
        assert r1.run_id != r2.run_id


# ---------------------------------------------------------------------------
# EvaluationHarness — construction
# ---------------------------------------------------------------------------


class TestHarnessConstruction:
    def test_default_significance_threshold(self) -> None:
        h = EvaluationHarness()
        assert h.significance_threshold == 0.02

    def test_custom_threshold(self) -> None:
        h = EvaluationHarness(significance_threshold=0.05)
        assert h.significance_threshold == 0.05

    def test_negative_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match="significance_threshold"):
            EvaluationHarness(significance_threshold=-0.1)

    def test_zero_threshold_allowed(self) -> None:
        h = EvaluationHarness(significance_threshold=0.0)
        assert h.significance_threshold == 0.0

    def test_default_hard_block_is_safety(self) -> None:
        h = EvaluationHarness()
        assert h.hard_block_metrics == _HARD_BLOCK_METRICS
        assert MetricKind.SAFETY in h.hard_block_metrics
        assert MetricKind.QUALITY not in h.hard_block_metrics


# ---------------------------------------------------------------------------
# Integration-style: full candidate-comparison workflow
# ---------------------------------------------------------------------------


class TestFullWorkflow:
    def test_candidate_matches_baseline_promotes(self) -> None:
        h = EvaluationHarness(significance_threshold=0.02)
        scores = (
            _score(MetricKind.QUALITY, 0.92),
            _score(MetricKind.SAFETY, 0.88),
            _score(MetricKind.LATENCY_MS, 95.0, higher_is_better=False),
            _score(MetricKind.COST_USD, 0.002, higher_is_better=False),
        )
        baseline = h.run_benchmark("prod-model", "production-suite", scores)
        candidate = h.run_benchmark("candidate-xyz", "production-suite", scores)
        d = h.promotion_gate(baseline, candidate)
        assert d.promote is True

    def test_latency_regression_blocks_promotion(self) -> None:
        h = EvaluationHarness(significance_threshold=0.02)
        baseline = _result(
            "prod",
            "s",
            "r1",
            _score(MetricKind.QUALITY, 0.9),
            _score(MetricKind.LATENCY_MS, 100.0, higher_is_better=False),
        )
        candidate = _result(
            "cand",
            "s",
            "r2",
            _score(MetricKind.QUALITY, 0.9),
            _score(MetricKind.LATENCY_MS, 150.0, higher_is_better=False),
        )
        d = h.promotion_gate(baseline, candidate)
        assert d.promote is False
        assert MetricKind.LATENCY_MS in d.blocked_metrics
        assert len(d.regressions) == 1

    def test_safety_regression_always_blocks(self) -> None:
        h = EvaluationHarness(significance_threshold=1000.0)
        baseline = _result("prod", "s", "r1", _score(MetricKind.SAFETY, 0.99))
        candidate = _result("cand", "s", "r2", _score(MetricKind.SAFETY, 0.98))
        d = h.promotion_gate(baseline, candidate)
        assert d.promote is False

    def test_compare_handles_empty_metrics(self) -> None:
        h = EvaluationHarness()
        base = _result("base", "s", "r1", _score(MetricKind.QUALITY, 0.9))
        cand = _result("cand", "s", "r2", _score(MetricKind.QUALITY, 0.9))
        v = h.compare(base, cand)
        assert v.is_regression is False


class TestEdgeCases:
    def test_overall_score_empty_scores_after_iteration(self) -> None:
        """The property handles edge case where scores tuple is empty (defensive)."""
        _result("c", "s", "r", _score(MetricKind.QUALITY, 0.5))
        r_blank = BenchmarkResult(
            candidate_id="c",
            suite_id="s",
            run_id="r",
            scores=(MetricScore(metric=MetricKind.QUALITY, value=0.5, higher_is_better=True),),
        )
        assert r_blank.overall_score == pytest.approx(0.5)

    def test_all_verdicts_only_matches_overlapping_metrics(self) -> None:
        h = EvaluationHarness()
        base = _result(
            "base",
            "s",
            "r1",
            _score(MetricKind.QUALITY, 0.9),
            _score(MetricKind.ROBUSTNESS, 0.7),
        )
        cand = _result(
            "cand",
            "s",
            "r2",
            _score(MetricKind.QUALITY, 0.8),
            _score(MetricKind.CALIBRATION, 0.05, higher_is_better=False),
        )
        verdicts = h._all_verdicts(base, cand)
        assert len(verdicts) == 1
        assert verdicts[0].metric is MetricKind.QUALITY
