"""Deep tests for token_cost.py — TokenCostTracker, TokenSample, TokenWeight."""

from __future__ import annotations

import contextlib

from general_ludd.observability.token_cost import (
    TokenCostTracker,
    TokenSample,
    TokenWeight,
    default_token_tracker,
)


class TestTokenSample:
    def test_total_is_sum(self):
        s = TokenSample(input_tokens=100, output_tokens=50)
        assert s.total == 150

    def test_frozen_prevents_mutation(self):
        s = TokenSample(input_tokens=10, output_tokens=20)
        with contextlib.suppress(Exception):
            s.input_tokens = 999  # type: ignore
        assert s.input_tokens == 10

    def test_zero_values(self):
        s = TokenSample(input_tokens=0, output_tokens=0)
        assert s.total == 0


class TestTokenWeight:
    def test_all_fields_present(self):
        w = TokenWeight(key="code", samples=5, median_input=100.0, median_output=200.0, median_total=300.0)
        assert w.key == "code"
        assert w.samples == 5
        assert w.median_input == 100.0
        assert w.median_output == 200.0
        assert w.median_total == 300.0


class TestRecord:
    def test_record_stores_sample(self):
        t = TokenCostTracker()
        t.record("code", input_tokens=100, output_tokens=50)
        w = t.weight("code")
        assert w is None  # below min_samples=3

    def test_record_ignores_negative_input(self):
        t = TokenCostTracker()
        t.record("code", input_tokens=-1, output_tokens=10)
        assert t.weight("code") is None

    def test_record_ignores_negative_output(self):
        t = TokenCostTracker()
        t.record("code", input_tokens=10, output_tokens=-1)
        assert t.weight("code") is None

    def test_record_window_bounded(self):
        t = TokenCostTracker(window=3, min_samples=1)
        for i in range(10):
            t.record("code", input_tokens=i * 10, output_tokens=i)
        w = t.weight("code")
        assert w is not None
        assert w.samples == 3

    def test_min_samples_gate(self):
        t = TokenCostTracker(min_samples=5)
        for _i in range(4):
            t.record("code", input_tokens=10, output_tokens=10)
        assert t.weight("code") is None
        t.record("code", input_tokens=10, output_tokens=10)
        assert t.weight("code") is not None


class TestWeight:
    def test_weight_returns_medians(self):
        t = TokenCostTracker(window=50)
        for inp, out in [(100, 50), (200, 100), (300, 150), (150, 75), (250, 125)]:
            t.record("key", input_tokens=inp, output_tokens=out)
        w = t.weight("key")
        assert w is not None
        assert w.median_input == 200.0  # median of 100,150,200,250,300
        assert w.median_output == 100.0  # median of 50,75,100,125,150
        assert w.median_total == 300.0  # median of 150,225,300,375,450

    def test_weight_unknown_key_returns_none(self):
        t = TokenCostTracker()
        assert t.weight("never_recorded") is None


class TestBaselineTotal:
    def test_baseline_total_known_key(self):
        t = TokenCostTracker(min_samples=3)
        for inp, out in [(10, 5), (20, 10), (30, 15)]:
            t.record("k", input_tokens=inp, output_tokens=out)
        assert t.baseline_total("k") == 30.0  # totals: 15,30,45 → median 30

    def test_baseline_total_unknown_key(self):
        t = TokenCostTracker()
        assert t.baseline_total("unknown") is None


class TestHeaviest:
    def test_heaviest_ranked_desc_by_median_total(self):
        t = TokenCostTracker(min_samples=3)
        # light task
        for _ in range(5):
            t.record("light", input_tokens=10, output_tokens=10)
        # heavy task
        for _ in range(5):
            t.record("heavy", input_tokens=100, output_tokens=100)
        # medium task
        for _ in range(5):
            t.record("medium", input_tokens=50, output_tokens=50)

        ranked = t.heaviest()
        assert len(ranked) == 3
        assert ranked[0].key == "heavy"
        assert ranked[1].key == "medium"
        assert ranked[2].key == "light"

    def test_heaviest_excludes_insufficient_samples(self):
        t = TokenCostTracker(min_samples=5)
        for _ in range(5):
            t.record("enough", input_tokens=10, output_tokens=10)
        for _ in range(3):
            t.record("few", input_tokens=100, output_tokens=100)
        ranked = t.heaviest()
        assert len(ranked) == 1
        assert ranked[0].key == "enough"

    def test_heaviest_n_caps_result(self):
        t = TokenCostTracker(min_samples=2)
        for k in ["a", "b", "c", "d"]:
            for _ in range(3):
                t.record(k, input_tokens=10, output_tokens=10)
        assert len(t.heaviest(n=2)) == 2

    def test_heaviest_empty_tracker(self):
        t = TokenCostTracker()
        assert t.heaviest() == []


class TestClassify:
    def test_classify_unknown_key(self):
        t = TokenCostTracker()
        assert t.classify("never_recorded") == "unknown"

    def test_classify_moderate_sole_key(self):
        t = TokenCostTracker(min_samples=3)
        for _ in range(5):
            t.record("only", input_tokens=10, output_tokens=10)
        assert t.classify("only") == "moderate"

    def test_classify_heavy_vs_light(self):
        t = TokenCostTracker(min_samples=3, heavy_factor=1.5)
        for _ in range(5):
            t.record("heavy", input_tokens=100, output_tokens=100)
        for _ in range(5):
            t.record("light", input_tokens=10, output_tokens=10)
        assert t.classify("heavy") == "heavy"
        assert t.classify("light") == "light"

    def test_classify_moderate_between_thresholds(self):
        t = TokenCostTracker(min_samples=3, heavy_factor=1.5)
        for _ in range(5):
            t.record("heavy", input_tokens=100, output_tokens=100)
        for _ in range(5):
            t.record("light", input_tokens=10, output_tokens=10)
        for _ in range(5):
            t.record("mid", input_tokens=40, output_tokens=40)
        assert t.classify("mid") == "moderate"

    def test_classify_zero_reference_is_moderate(self):
        t = TokenCostTracker(min_samples=2)
        t.record("a", input_tokens=0, output_tokens=0)
        t.record("a", input_tokens=0, output_tokens=0)
        t.record("b", input_tokens=0, output_tokens=0)
        t.record("b", input_tokens=0, output_tokens=0)
        assert t.classify("a") == "moderate"


class TestConstructorValidation:
    def test_negative_window_raises(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            TokenCostTracker(window=0)

    def test_negative_min_samples_raises(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            TokenCostTracker(min_samples=0)

    def test_heavy_factor_leq_one_raises(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            TokenCostTracker(heavy_factor=1.0)


class TestSharedTracker:
    def test_default_token_tracker_is_singleton(self):
        a = default_token_tracker()
        b = default_token_tracker()
        assert a is b

    def test_default_token_tracker_is_functional(self):
        t = default_token_tracker()
        t.record("test_key", input_tokens=10, output_tokens=10)
        assert t.classify("test_key") == "unknown"  # below min_samples
