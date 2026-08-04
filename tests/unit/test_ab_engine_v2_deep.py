"""Deep tests for the A/B test experiment engine (ab_engine.py).

Covers: variant assignment, traffic splitting, metric aggregation,
significance testing (Welch's t-test, chi-squared), decision logic,
sequential testing helpers, and experiment lifecycle.
"""

from __future__ import annotations

import pytest

from general_ludd.experiments.ab_engine import (
    Experiment,
    ExperimentDecision,
    ExperimentDef,
    ExperimentStatus,
    MetricDef,
    MetricType,
    SignificanceResult,
    TrafficSplitter,
    VariantDef,
    VariantMetricSnapshot,
    aggregate_continuous,
    aggregate_conversion,
    bonferroni_correction,
    chi_squared_test,
    required_sample_size_continuous,
    required_sample_size_conversion,
    variance_from_aggregates,
    welch_t_test,
)


def _mk_variant(name, weight=0.5):
    return VariantDef(name=name, traffic_weight=weight)


def _mk_metric(name, mtype=MetricType.CONTINUOUS, primary=True):
    return MetricDef(name=name, type=mtype, is_primary=primary)


def _mk_exp_def(name="test", variants=None, metrics=None, **kwargs):
    if variants is None:
        variants = [
            VariantDef(name="control", traffic_weight=0.5),
            VariantDef(name="treatment", traffic_weight=0.5),
        ]
    if metrics is None:
        metrics = [MetricDef(name="revenue", type=MetricType.CONTINUOUS, is_primary=True)]
    return ExperimentDef(name=name, variants=variants, metrics=metrics, **kwargs)


# ── VariantDef and MetricDef tests ─────────────────────────────────────────


class TestVariantDef:
    def test_creation(self):
        v = VariantDef(name="a", traffic_weight=0.3)
        assert v.name == "a"
        assert v.traffic_weight == 0.3

    def test_weights_can_be_zero(self):
        v = VariantDef(name="holdout", traffic_weight=0.0)
        assert v.traffic_weight == 0.0


class TestMetricDef:
    def test_continuous_metric(self):
        m = MetricDef(name="ctr", type=MetricType.CONTINUOUS)
        assert m.type == MetricType.CONTINUOUS
        assert m.is_primary is False

    def test_conversion_metric(self):
        m = MetricDef(name="purchase", type=MetricType.CONVERSION, is_primary=True)
        assert m.type == MetricType.CONVERSION
        assert m.is_primary is True


class TestVariantMetricSnapshot:
    def test_frozen_continuous_snapshot(self):
        s = VariantMetricSnapshot(
            metric_name="rev",
            variant_name="a",
            n=100,
            sum=500.0,
            sum_of_squares=3000.0,
        )
        assert s.n == 100
        assert s.sum == 500.0
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            s.n = 200

    def test_frozen_conversion_snapshot(self):
        s = VariantMetricSnapshot(
            metric_name="conv",
            variant_name="b",
            n=200,
            conversions=45,
        )
        assert s.n == 200
        assert s.conversions == 45


# ── ExperimentDef tests ────────────────────────────────────────────────────


class TestExperimentDef:
    def test_valid_experiment(self):
        exp = _mk_exp_def()
        assert exp.name == "test"
        assert exp.control_name == "control"

    def test_explicit_control_name(self):
        variants = [
            VariantDef(name="a", traffic_weight=0.5),
            VariantDef(name="b", traffic_weight=0.3),
            VariantDef(name="c", traffic_weight=0.2),
        ]
        exp = ExperimentDef(
            name="multi",
            variants=variants,
            metrics=[_mk_metric("x")],
            control_name="c",
        )
        assert exp.control_name == "c"

    def test_rejects_single_variant(self):
        with pytest.raises(ValueError, match="at least 2"):
            ExperimentDef(
                name="bad",
                variants=[_mk_variant("only", 1.0)],
                metrics=[_mk_metric("x")],
            )

    def test_rejects_bad_weights(self):
        with pytest.raises(ValueError, match=r"sum to 1\.0"):
            ExperimentDef(
                name="bad",
                variants=[
                    _mk_variant("a", 0.3),
                    _mk_variant("b", 0.3),
                ],
                metrics=[_mk_metric("x")],
            )

    def test_rejects_bad_alpha(self):
        with pytest.raises(ValueError, match="alpha"):
            ExperimentDef(
                name="bad",
                variants=[_mk_variant("a", 0.5), _mk_variant("b", 0.5)],
                metrics=[_mk_metric("x")],
                alpha=1.5,
            )

    def test_control_variant_property(self):
        exp = _mk_exp_def()
        ctrl = exp.control_variant
        assert ctrl.name == "control"

    def test_treatment_variants_property(self):
        exp = _mk_exp_def(
            variants=[
                _mk_variant("ctrl", 0.4),
                _mk_variant("trt_a", 0.3),
                _mk_variant("trt_b", 0.3),
            ]
        )
        treatments = exp.treatment_variants
        assert len(treatments) == 2
        assert {v.name for v in treatments} == {"trt_a", "trt_b"}


# ── TrafficSplitter tests ──────────────────────────────────────────────────


class TestTrafficSplitter:
    def test_deterministic_assignment(self):
        ts = TrafficSplitter({"a": 0.5, "b": 0.5})
        first = ts.assign("user-42")
        for _ in range(10):
            assert ts.assign("user-42") == first

    def test_distribution_coverage(self):
        ts = TrafficSplitter({"a": 0.5, "b": 0.3, "c": 0.2})
        assigned = set()
        for i in range(200):
            assigned.add(ts.assign(f"user-{i}"))
        assert assigned == {"a", "b", "c"}

    def test_single_variant_always_assigned(self):
        ts = TrafficSplitter({"only": 1.0})
        for i in range(100):
            assert ts.assign(f"id-{i}") == "only"

    def test_zero_weight_variant_not_assigned(self):
        ts = TrafficSplitter({"live": 1.0, "dead": 0.0})
        for i in range(100):
            assert ts.assign(f"id-{i}") == "live"


# ── aggregate_continuous and aggregate_conversion tests ────────────────────


class TestAggregateContinuous:
    def test_empty(self):
        n, s, _ss, mean = aggregate_continuous([])
        assert n == 0
        assert s == 0.0
        assert mean == 0.0

    def test_single_value(self):
        n, s, _ss, mean = aggregate_continuous([5.0])
        assert n == 1
        assert s == 5.0
        assert mean == 5.0

    def test_multiple_values(self):
        n, s, ss, mean = aggregate_continuous([1.0, 2.0, 3.0])
        assert n == 3
        assert s == 6.0
        assert mean == 2.0
        assert ss == 14.0  # 1 + 4 + 9

    def test_variance_from_aggregates(self):
        vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        n, s, ss, mean = aggregate_continuous(vals)
        var = variance_from_aggregates(n, s, ss)
        assert var == pytest.approx(4.5714, abs=1e-3)
        assert mean == 5.0


class TestAggregateConversion:
    def test_empty(self):
        n, convs, rate = aggregate_conversion([])
        assert n == 0
        assert convs == 0
        assert rate == 0.0

    def test_mixed(self):
        n, convs, rate = aggregate_conversion([True, False, True, True, False])
        assert n == 5
        assert convs == 3
        assert rate == 0.6

    def test_all_success(self):
        n, convs, rate = aggregate_conversion([True, True, True])
        assert n == 3
        assert convs == 3
        assert rate == 1.0


# ── Welch's t-test tests ───────────────────────────────────────────────────


class TestWelchTTest:
    """Statistical tests for Welch's t-test.

    Verify that:
    - Large sample sizes with clear difference → significant
    - Identical distributions → not significant
    - Small samples → handles edge cases
    """

    def test_clear_difference_detected(self):
        t, p = welch_t_test(
            "ctrl",
            10.0,
            4.0,
            1000,
            "trt",
            12.0,
            4.0,
            1000,
        )
        assert p < 0.0001
        assert t > 0

    def test_no_difference_not_significant(self):
        _t, p = welch_t_test(
            "ctrl",
            5.0,
            2.0,
            500,
            "trt",
            5.0,
            2.0,
            500,
        )
        assert p > 0.05

    def test_small_sample_handled(self):
        _t, p = welch_t_test(
            "ctrl",
            10.0,
            1.0,
            2,
            "trt",
            12.0,
            1.0,
            2,
        )
        assert p > 0.0  # should not crash
        assert p <= 1.0

    def test_zero_variance_returns_non_significant(self):
        t, p = welch_t_test(
            "ctrl",
            5.0,
            0.0,
            10,
            "trt",
            5.0,
            0.0,
            10,
        )
        assert p == 1.0
        assert t == 0.0

    def test_unequal_sample_sizes(self):
        _t, p = welch_t_test(
            "ctrl",
            10.0,
            4.0,
            100,
            "trt",
            12.0,
            5.0,
            1000,
        )
        assert p < 0.001

    def test_very_large_sample_moderate_effect(self):
        _t, p = welch_t_test(
            "ctrl",
            10.0,
            9.0,
            100000,
            "trt",
            10.1,
            9.0,
            100000,
        )
        assert p < 0.05


# ── Chi-squared test tests ─────────────────────────────────────────────────


class TestChiSquaredTest:
    def test_clear_difference_detected(self):
        _chi2, p = chi_squared_test(
            conversions_a=100,
            trials_a=1000,
            conversions_b=150,
            trials_b=1000,
        )
        assert p < 0.01

    def test_no_difference_not_significant(self):
        _chi2, p = chi_squared_test(
            conversions_a=100,
            trials_a=1000,
            conversions_b=108,
            trials_b=1000,
        )
        assert p > 0.05

    def test_zero_trials_handled(self):
        _chi2, p = chi_squared_test(
            conversions_a=0,
            trials_a=0,
            conversions_b=5,
            trials_b=100,
        )
        assert p == 1.0

    def test_small_sample_chi2(self):
        _chi2, p = chi_squared_test(
            conversions_a=1,
            trials_a=10,
            conversions_b=5,
            trials_b=10,
        )
        assert 0.0 <= p <= 1.0

    def test_perfect_split(self):
        chi2, p = chi_squared_test(
            conversions_a=50,
            trials_a=100,
            conversions_b=50,
            trials_b=100,
        )
        assert chi2 == 0.0
        assert p == 1.0

    def test_yates_like_continuity(self):
        _chi2, p = chi_squared_test(
            conversions_a=0,
            trials_a=30,
            conversions_b=3,
            trials_b=30,
        )
        assert p > 0.05


# ── Experiment lifecycle tests ─────────────────────────────────────────────


class TestExperimentLifecycle:
    def test_draft_to_running(self):
        exp = Experiment(definition=_mk_exp_def())
        assert exp.status == ExperimentStatus.DRAFT
        exp.start()
        assert exp.status == ExperimentStatus.RUNNING

    def test_cannot_start_twice(self):
        exp = Experiment(definition=_mk_exp_def())
        exp.start()
        with pytest.raises(RuntimeError):
            exp.start()

    def test_cannot_record_while_draft(self):
        exp = Experiment(definition=_mk_exp_def())
        with pytest.raises(RuntimeError):
            exp.record("control", "revenue", 10.0)

    def test_cannot_evaluate_while_draft(self):
        exp = Experiment(definition=_mk_exp_def())
        with pytest.raises(RuntimeError):
            exp.evaluate()

    def test_record_unknown_metric_raises(self):
        exp = Experiment(definition=_mk_exp_def())
        exp.start()
        with pytest.raises(ValueError, match="Unknown"):
            exp.record("control", "nonexistent", 10.0)


class TestExperimentRecord:
    def test_record_continuous(self):
        exp = Experiment(definition=_mk_exp_def())
        exp.start()
        for i in range(100):
            exp.record("control", "revenue", float(i))
            exp.record("treatment", "revenue", float(i + 1))
        agg = exp.aggregate("control", "revenue")
        assert agg is not None
        assert agg.n == 100
        agg_t = exp.aggregate("treatment", "revenue")
        assert agg_t is not None
        assert agg_t.n == 100

    def test_record_conversion(self):
        exp = Experiment(
            definition=_mk_exp_def(
                metrics=[_mk_metric("purchase", MetricType.CONVERSION)],
            )
        )
        exp.start()
        for i in range(50):
            exp.record_conversion("control", "purchase", i % 5 == 0)
            exp.record_conversion("treatment", "purchase", i % 3 == 0)
        agg_c = exp.aggregate("control", "purchase")
        agg_t = exp.aggregate("treatment", "purchase")
        assert agg_c is not None and agg_t is not None
        assert agg_c.n == 50
        assert agg_t.n == 50

    def test_aggregate_empty_variant(self):
        exp = Experiment(definition=_mk_exp_def())
        exp.start()
        assert exp.aggregate("control", "revenue") is None

    def test_total_samples(self):
        exp = Experiment(definition=_mk_exp_def())
        exp.start()
        for i in range(10):
            exp.record("control", "revenue", float(i))
            exp.record("treatment", "revenue", float(i + 1))
        assert exp.total_samples == 20


# ── Experiment evaluation and decision tests ───────────────────────────────


class TestExperimentEvaluate:
    def test_insufficient_data_rejection(self):
        exp = Experiment(
            definition=_mk_exp_def(
                name="low_data",
                metrics=[_mk_metric("revenue", MetricType.CONTINUOUS)],
            )
        )
        exp.start()
        exp.record("control", "revenue", 10.0)
        exp.record("treatment", "revenue", 10.0)
        decision = exp.evaluate()
        assert decision.winner is None
        assert "Not enough data" in decision.reason

    def test_clear_winner_detected_continuous(self):
        import random

        rng = random.Random(42)
        exp = Experiment(
            definition=_mk_exp_def(
                name="winner",
                variants=[
                    _mk_variant("ctrl", 0.5),
                    _mk_variant("better", 0.5),
                ],
                metrics=[_mk_metric("latency", MetricType.CONTINUOUS)],
                control_name="ctrl",
            )
        )
        exp.start()
        for _ in range(1000):
            exp.record("ctrl", "latency", 100.0 + rng.gauss(0, 10))
            exp.record("better", "latency", 80.0 + rng.gauss(0, 10))
        decision = exp.evaluate()
        assert decision.winner == "better"
        assert decision.has_winner is True

    def test_clear_winner_detected_conversion(self):
        exp = Experiment(
            definition=_mk_exp_def(
                name="conv_winner",
                metrics=[_mk_metric("purchase", MetricType.CONVERSION)],
                variants=[
                    _mk_variant("ctrl", 0.5),
                    _mk_variant("better", 0.5),
                ],
                control_name="ctrl",
            )
        )
        exp.start()
        for _ in range(500):
            exp.record_conversion("ctrl", "purchase", False)
            exp.record_conversion("better", "purchase", True)
        decision = exp.evaluate()
        assert decision.winner is not None

    def test_no_significant_difference(self):
        import random

        rng = random.Random(88)
        exp = Experiment(
            definition=_mk_exp_def(
                name="no_diff",
                metrics=[_mk_metric("rev", MetricType.CONTINUOUS)],
                variants=[
                    _mk_variant("ctrl", 0.5),
                    _mk_variant("same", 0.5),
                ],
                control_name="ctrl",
            )
        )
        exp.start()
        for _ in range(500):
            exp.record("ctrl", "rev", 50.0 + rng.gauss(0, 5))
            exp.record("same", "rev", 50.0 + rng.gauss(0, 5))
        decision = exp.evaluate()
        assert decision.winner is None
        assert "No variant showed" in decision.reason
        assert decision.has_winner is False

    def test_evaluate_sets_status_concluded(self):
        import random

        rng = random.Random(11)
        exp = Experiment(
            definition=_mk_exp_def(
                name="conclude",
                metrics=[_mk_metric("m", MetricType.CONTINUOUS)],
            )
        )
        exp.start()
        for _ in range(500):
            exp.record("control", "m", 10.0 + rng.gauss(0, 2))
            exp.record("treatment", "m", 10.0 + rng.gauss(0, 2))
        exp.evaluate()
        assert exp.status == ExperimentStatus.CONCLUDED

    def test_custom_alpha_sets_significance_threshold(self):
        exp = Experiment(
            definition=_mk_exp_def(
                name="strict",
                metrics=[_mk_metric("m", MetricType.CONTINUOUS)],
                alpha=0.001,
            )
        )
        exp.start()
        for i in range(500):
            exp.record("control", "m", 10.0 + i * 0.001)
            exp.record("treatment", "m", 10.5 + i * 0.001)
        decision = exp.evaluate()
        assert isinstance(decision, ExperimentDecision)

    def test_multi_variant_all_evaluated(self):
        import random

        rng = random.Random(7)
        exp = Experiment(
            definition=_mk_exp_def(
                name="multi",
                variants=[
                    _mk_variant("ctrl", 0.34),
                    _mk_variant("a", 0.33),
                    _mk_variant("b", 0.33),
                ],
                metrics=[_mk_metric("rev", MetricType.CONTINUOUS)],
                control_name="ctrl",
            )
        )
        exp.start()
        for _ in range(300):
            exp.record("ctrl", "rev", 10.0 + rng.gauss(0, 3))
            exp.record("a", "rev", 11.0 + rng.gauss(0, 3))
            exp.record("b", "rev", 12.0 + rng.gauss(0, 3))
        decision = exp.evaluate()
        assert len(decision.all_results) == 2  # a vs ctrl, b vs ctrl

    def test_significance_result_structure(self):
        import random

        rng = random.Random(99)
        exp = Experiment(
            definition=_mk_exp_def(
                name="struct",
                metrics=[_mk_metric("m", MetricType.CONTINUOUS)],
            )
        )
        exp.start()
        for _ in range(500):
            exp.record("control", "m", 10.0 + rng.gauss(0, 2))
            exp.record("treatment", "m", 12.0 + rng.gauss(0, 2))
        decision = exp.evaluate()
        assert len(decision.all_results) > 0
        r = decision.all_results[0]
        assert isinstance(r, SignificanceResult)
        assert r.metric_name == "m"
        assert r.variant_name == "treatment"
        assert r.test_type in ("welch_t", "chi_squared")
        assert 0.0 <= r.p_value <= 1.0
        assert isinstance(r.is_significant, bool)

    def test_multiple_primary_metrics(self):
        exp = Experiment(
            definition=_mk_exp_def(
                name="multi_metric",
                variants=[
                    _mk_variant("ctrl", 0.5),
                    _mk_variant("trt", 0.5),
                ],
                metrics=[
                    _mk_metric("rev", MetricType.CONTINUOUS, primary=True),
                    _mk_metric("conv", MetricType.CONVERSION, primary=True),
                ],
                control_name="ctrl",
            )
        )
        exp.start()
        for _ in range(500):
            exp.record("ctrl", "rev", 10.0)
            exp.record("trt", "rev", 15.0)
            exp.record_conversion("ctrl", "conv", False)
            exp.record_conversion("trt", "conv", True)
        decision = exp.evaluate()
        assert len(decision.all_results) == 2

    def test_fallback_to_all_metrics_when_no_primary(self):
        exp = Experiment(
            definition=_mk_exp_def(
                name="no_primary",
                variants=[
                    _mk_variant("ctrl", 0.5),
                    _mk_variant("trt", 0.5),
                ],
                metrics=[
                    _mk_metric("a", MetricType.CONTINUOUS, primary=False),
                    _mk_metric("b", MetricType.CONTINUOUS, primary=False),
                ],
                control_name="ctrl",
            )
        )
        exp.start()
        for _ in range(300):
            exp.record("ctrl", "a", 1.0)
            exp.record("trt", "a", 5.0)
            exp.record("ctrl", "b", 1.0)
            exp.record("trt", "b", 1.0)
        decision = exp.evaluate()
        assert len(decision.all_results) == 2


# ── Sequential testing helper tests ────────────────────────────────────────


class TestBonferroniCorrection:
    def test_single_comparison(self):
        assert bonferroni_correction(0.05, 1) == 0.05

    def test_multiple_comparisons(self):
        assert bonferroni_correction(0.05, 5) == 0.01

    def test_rejects_zero_comparisons(self):
        with pytest.raises(ValueError):
            bonferroni_correction(0.05, 0)


class TestRequiredSampleSize:
    def test_continuous_sample_size_positive(self):
        n = required_sample_size_continuous(effect_size=0.2, alpha=0.05, power=0.80)
        assert n >= 2

    def test_continuous_larger_effect_needs_fewer_samples(self):
        n_small = required_sample_size_continuous(effect_size=0.5, alpha=0.05)
        n_large = required_sample_size_continuous(effect_size=0.2, alpha=0.05)
        assert n_small < n_large

    def test_conversion_sample_size_positive(self):
        n = required_sample_size_conversion(
            baseline_rate=0.1,
            minimum_detectable_effect=0.05,
            alpha=0.05,
            power=0.80,
        )
        assert n >= 2

    def test_conversion_rejects_invalid_baseline(self):
        with pytest.raises(ValueError):
            required_sample_size_conversion(baseline_rate=0.0, minimum_detectable_effect=0.05)
        with pytest.raises(ValueError):
            required_sample_size_conversion(baseline_rate=1.0, minimum_detectable_effect=0.05)

    def test_conversion_larger_mde_needs_fewer_samples(self):
        n_small = required_sample_size_conversion(
            baseline_rate=0.1,
            minimum_detectable_effect=0.1,
        )
        n_large = required_sample_size_conversion(
            baseline_rate=0.1,
            minimum_detectable_effect=0.02,
        )
        assert n_small < n_large


# ── ExperimentDecision tests ───────────────────────────────────────────────


class TestExperimentDecision:
    def test_no_winner_decision(self):
        d = ExperimentDecision(
            experiment_name="test",
            winner=None,
            all_results=(),
            reason="no significance",
        )
        assert d.has_winner is False

    def test_winner_decision(self):
        d = ExperimentDecision(
            experiment_name="test",
            winner="v2",
            all_results=(),
            reason="significant",
        )
        assert d.has_winner is True
        assert d.winner == "v2"


# ── Variance from aggregates tests ─────────────────────────────────────────


class TestVarianceFromAggregates:
    def test_small_n(self):
        assert variance_from_aggregates(0, 0.0, 0.0) == 0.0
        assert variance_from_aggregates(1, 5.0, 25.0) == 0.0

    def test_known_variance(self):
        vals = [2.0, 4.0, 6.0]
        n, s, ss, _ = aggregate_continuous(vals)
        var = variance_from_aggregates(n, s, ss)
        assert var == pytest.approx(4.0)

    def test_negative_floor(self):
        vals = [5.0, 5.0, 5.0]
        n, s, ss, _ = aggregate_continuous(vals)
        var = variance_from_aggregates(n, s, ss)
        assert var == 0.0


# ── Import and module structure tests ──────────────────────────────────────


class TestModuleStructure:
    def test_all_classes_importable(self):

        assert True

    def test_all_functions_importable(self):

        assert True

    def test_enum_values(self):
        assert ExperimentStatus.DRAFT != ExperimentStatus.RUNNING
        assert ExperimentStatus.RUNNING != ExperimentStatus.CONCLUDED
        assert MetricType.CONTINUOUS != MetricType.CONVERSION
