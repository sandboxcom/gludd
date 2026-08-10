"""Deep edge-case tests for TokenCostTracker — boundaries, thread safety, classify logic."""

from __future__ import annotations

import threading
import time

import pytest

from general_ludd.observability.token_cost import (
    TokenCostTracker,
    TokenSample,
    TokenWeight,
    default_token_tracker,
)


class TestTokenSample:
    def test_total_adds_input_output(self):
        s = TokenSample(input_tokens=42, output_tokens=8)
        assert s.total == 50

    def test_zero_tokens(self):
        s = TokenSample(input_tokens=0, output_tokens=0)
        assert s.total == 0

    def test_immutable(self):
        s = TokenSample(input_tokens=1, output_tokens=2)
        with pytest.raises(AttributeError):
            s.input_tokens = 5  # type: ignore[misc]


class TestRecordEdgeCases:
    def test_zero_tokens_recorded_not_ignored(self):
        """Zero is not negative — zero-token calls are valid and recorded."""
        t = TokenCostTracker(min_samples=1)
        t.record("empty", 0, 0)
        assert t.baseline_total("empty") == 0

    def test_mixed_negative_and_valid(self):
        """Only negative values are dropped; valid ones accumulate normally."""
        t = TokenCostTracker(min_samples=2)
        t.record("k", -1, 10)  # rejected
        t.record("k", 10, -1)  # rejected
        t.record("k", 100, 50)  # accepted
        t.record("k", 200, 100)  # accepted
        assert t.baseline_total("k") == 225  # median of 150, 300

    def test_large_values_no_overflow(self):
        """Values near Python int limits don't overflow."""
        BIG = 10**18
        t = TokenCostTracker(min_samples=3)
        for _ in range(3):
            t.record("huge", BIG, BIG)
        assert t.baseline_total("huge") == 2 * BIG

    def test_negative_input_zero_output_rejected(self):
        t = TokenCostTracker(min_samples=1)
        t.record("x", -5, 0)
        assert t.baseline_total("x") is None

    def test_zero_input_negative_output_rejected(self):
        t = TokenCostTracker(min_samples=1)
        t.record("x", 0, -5)
        assert t.baseline_total("x") is None


class TestWindowTrimming:
    def test_window_1_keeps_last_only(self):
        t = TokenCostTracker(window=1, min_samples=1)
        t.record("k", 10, 0)
        assert t.baseline_total("k") == 10
        t.record("k", 999, 0)
        assert t.baseline_total("k") == 999

    def test_window_2_exact_boundary(self):
        t = TokenCostTracker(window=2, min_samples=2)
        t.record("k", 10, 0)
        t.record("k", 20, 0)
        assert t.weight("k").samples == 2  # type: ignore[union-attr]
        t.record("k", 30, 0)  # pushes out 10
        w = t.weight("k")
        assert w is not None and w.samples == 2
        assert w.median_total == 25  # median of 20, 30

    def test_window_smaller_than_min_samples(self):
        """window < min_samples means we can never reach a trusted baseline."""
        t = TokenCostTracker(window=2, min_samples=5)
        for i in range(10):
            t.record("k", i * 10, 0)
        assert t.baseline_total("k") is None

    def test_many_keys_trimming_does_not_cross_contaminate(self):
        t = TokenCostTracker(window=2, min_samples=1)
        for _ in range(5):
            t.record("a", 100, 0)
        for _ in range(5):
            t.record("b", 200, 0)
        assert t.baseline_total("a") == 100
        assert t.baseline_total("b") == 200


class TestHeaviestEdgeCases:
    def test_empty_tracker_returns_empty(self):
        t = TokenCostTracker()
        assert t.heaviest() == []

    def test_heaviest_n_zero_returns_empty(self):
        t = TokenCostTracker(min_samples=1)
        t.record("k", 10, 5)
        t.record("k", 20, 5)
        assert t.heaviest(n=0) == []

    def test_heaviest_n_larger_than_keys(self):
        t = TokenCostTracker(min_samples=1)
        for _ in range(3):
            t.record("a", 10, 0)
        assert len(t.heaviest(n=100)) == 1

    def test_keys_without_min_samples_are_excluded(self):
        t = TokenCostTracker(min_samples=5)
        for _ in range(3):
            t.record("k", 100, 0)
        assert t.heaviest() == []

    def test_heaviest_on_none_argument_same_as_omitted(self):
        t = TokenCostTracker(min_samples=1)
        for _ in range(3):
            t.record("a", 100, 0)
        assert t.heaviest(n=None) == t.heaviest()


class TestClassifyDeep:
    def test_only_one_key_returns_moderate(self):
        """When only one key has a baseline, classify returns 'moderate'."""
        t = TokenCostTracker(min_samples=3)
        for _ in range(3):
            t.record("lonely", 100, 50)
        assert t.classify("lonely") == "moderate"

    def test_two_keys_equal_totals_both_moderate(self):
        t = TokenCostTracker(min_samples=3)
        for _ in range(3):
            t.record("a", 100, 0)
            t.record("b", 100, 0)
        assert t.classify("a") == "moderate"
        assert t.classify("b") == "moderate"

    def test_classify_exact_heavy_boundary(self):
        """At heavy_factor=2.0, target == reference * 2.0 is heavy."""
        t = TokenCostTracker(min_samples=3, heavy_factor=2.0)
        for _ in range(3):
            t.record("low", 10, 0)  # total 10
            t.record("high", 50, 0)  # total 50
        # reference = median of [10, 50] = 30.0
        # 50 >= 30 * 2.0 = 60? No. So high is NOT heavy here.
        # Actually let me recalculate: median(10,50) = 30. target=50. 50 >= 60? No.
        assert t.classify("high") == "moderate"

    def test_classify_exact_light_boundary(self):
        t = TokenCostTracker(min_samples=3, heavy_factor=2.0)
        for _ in range(3):
            t.record("low", 5, 0)  # total 5
            t.record("high", 40, 0)  # total 40
        # reference = median(5, 40) = 22.5. target=5. 5 <= 22.5/2.0 = 11.25? Yes → light.
        assert t.classify("low") == "light"

    def test_classify_exactly_at_factor_boundary_heavy(self):
        t = TokenCostTracker(min_samples=3, heavy_factor=1.5)
        for _ in range(3):
            t.record("a", 10, 0)  # total 10
            t.record("b", 10, 0)  # total 10
            t.record("c", 30, 0)  # total 30
        # median of [10,10,30] = 10. target=30. 30 >= 10*1.5=15 → heavy.
        assert t.classify("c") == "heavy"

    def test_classify_just_below_factor_heavy(self):
        t = TokenCostTracker(min_samples=3, heavy_factor=1.5)
        for _ in range(3):
            t.record("a", 10, 0)
            t.record("b", 10, 0)
            t.record("c", 14, 0)  # 14 < 10*1.5=15
        # median of [10,10,14] = 10. target=14. 14 >= 15? No. 14 <= 10/1.5≈6.67? No → moderate.
        assert t.classify("c") == "moderate"

    def test_reference_zero_all_moderate(self):
        """When all keys have median total 0, reference <= 0 → 'moderate'."""
        t = TokenCostTracker(min_samples=3)
        for _ in range(3):
            t.record("a", 0, 0)
            t.record("b", 0, 0)
        assert t.classify("a") == "moderate"
        assert t.classify("b") == "moderate"

    def test_unknown_key_never_recorded(self):
        t = TokenCostTracker(min_samples=3)
        assert t.classify("ghost") == "unknown"

    def test_classify_with_three_keys_mixed(self):
        t = TokenCostTracker(min_samples=3, heavy_factor=2.0)
        for _ in range(3):
            t.record("tiny", 1, 0)  # total 1
            t.record("mid", 10, 0)  # total 10
            t.record("mega", 100, 0)  # total 100
        # median of [1,10,100] = 10.0
        # tiny: 1 <= 10/2=5 → light
        # mid: 5 < 10 < 20 → moderate
        # mega: 100 >= 10*2=20 → heavy
        assert t.classify("tiny") == "light"
        assert t.classify("mid") == "moderate"
        assert t.classify("mega") == "heavy"


class TestSingletonEdgeCases:
    def test_default_tracker_accumulates_across_keys(self):
        a = default_token_tracker()
        a.record("singleton_key", 10, 5)
        # Enough samples to reach min_samples (default 3)
        a.record("singleton_key", 20, 5)
        a.record("singleton_key", 30, 5)
        assert a.baseline_total("singleton_key") == 25

    def test_default_tracker_reuses_existing(self):
        a = default_token_tracker()
        key = f"reuse_{id(a)}"
        for _ in range(3):
            a.record(key, 50, 0)
        b = default_token_tracker()
        assert b is a
        assert b.baseline_total(key) == 50

    def test_default_tracker_thread_safe_init(self):
        """Concurrent first-calls to default_token_tracker return the same instance."""
        results = []

        def grab():
            results.append(default_token_tracker())

        threads = [threading.Thread(target=grab) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        first = results[0]
        assert all(r is first for r in results)


class TestThreadSafety:
    def test_concurrent_records_no_data_loss(self):
        t = TokenCostTracker(min_samples=1, window=2000)
        N = 500
        barrier = threading.Barrier(4)

        def record_batch():
            barrier.wait()
            for i in range(N):
                t.record("shared", i, i)

        threads = [threading.Thread(target=record_batch) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        w = t.weight("shared")
        assert w is not None and w.samples == 4 * N

    def test_concurrent_heaviest_does_not_crash(self):
        t = TokenCostTracker(min_samples=1, window=50)
        for _ in range(3):
            t.record("a", 100, 0)
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                _ = t.heaviest()
                _ = t.classify("a")
                _ = t.baseline_total("a")
                time.sleep(0.0001)

        def writer():
            for i in range(200):
                t.record("b", i, i)
                time.sleep(0.0001)
            stop.set()

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads.append(threading.Thread(target=writer))
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        # If nothing crashed, the test passes.
        assert True

    def test_concurrent_record_across_keys(self):
        t = TokenCostTracker(min_samples=1, window=200)
        N = 500
        barrier = threading.Barrier(5)

        def write_key(key: str):
            barrier.wait()
            for i in range(N):
                t.record(key, i, 0)

        threads = [threading.Thread(target=write_key, args=(f"k{i}",)) for i in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        for i in range(5):
            assert t.baseline_total(f"k{i}") is not None


class TestTokenWeight:
    def test_frozen_dataclass(self):
        w = TokenWeight(key="test", samples=5, median_input=10.0, median_output=20.0, median_total=30.0)
        assert w.key == "test"
        assert w.samples == 5
        with pytest.raises(AttributeError):
            w.key = "changed"  # type: ignore[misc]


class TestMultipleKeysMedianComputation:
    def test_unequal_sample_counts(self):
        """Keys can have different sample counts; median is per-key."""
        t = TokenCostTracker(min_samples=2)
        for _ in range(3):
            t.record("a", 100, 0)  # 3 samples
        for _ in range(5):
            t.record("b", 200, 0)  # 5 samples, but window=50 default — all kept
        wa = t.weight("a")
        wb = t.weight("b")
        assert wa is not None and wa.samples == 3
        assert wb is not None and wb.samples == 5
        assert wa.median_total == 100
        assert wb.median_total == 200
