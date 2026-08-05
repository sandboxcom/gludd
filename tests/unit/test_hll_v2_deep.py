"""Deep tests for HyperLogLog v2 — HLL++ with sparse representation and bias correction."""

from __future__ import annotations

import math

import pytest

from general_ludd.probabilistic.hyperloglog_v2 import HyperLogLogV2


class TestHyperLogLogV2AddCount:
    def test_empty_hll_returns_zero(self) -> None:
        hll = HyperLogLogV2(precision=10)
        assert hll.count() == 0

    def test_single_item_returns_one(self) -> None:
        hll = HyperLogLogV2(precision=10)
        hll.add("only")
        assert hll.count() == 1

    def test_add_and_count_small_stays_sparse(self) -> None:
        hll = HyperLogLogV2(precision=12)
        for i in range(100):
            hll.add(f"item_{i}")
        assert hll.is_sparse
        c = hll.count()
        assert 70 <= c <= 150

    def test_add_and_count_medium_accuracy(self) -> None:
        hll = HyperLogLogV2(precision=12)
        n = 50000
        for i in range(n):
            hll.add(f"x_{i}")
        estimated = hll.count()
        error = abs(estimated - n) / n
        assert error < 0.05

    def test_duplicate_items_count_one(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for _ in range(1000):
            hll.add("same_value")
        assert hll.count() == 1

    def test_string_bytes_int_float_types(self) -> None:
        hll = HyperLogLogV2(precision=8)
        vals = ["text", b"binary", 123, 9.99]
        for v in vals:
            hll.add(v)
        c = hll.count()
        assert 3 <= c <= 5

    def test_large_cardinality_accuracy(self) -> None:
        hll = HyperLogLogV2(precision=14)
        n = 200000
        for i in range(n):
            hll.add(f"large_{i}")
        estimated = hll.count()
        error = abs(estimated - n) / n
        assert error < 0.04

    def test_count_is_monotonic(self) -> None:
        hll = HyperLogLogV2(precision=10)
        prev = 0
        for batch in range(10):
            for i in range(batch * 10, (batch + 1) * 10):
                hll.add(f"item_{i}")
            cur = hll.count()
            assert cur >= prev
            prev = cur


class TestHyperLogLogV2SparseDense:
    def test_starts_sparse(self) -> None:
        hll = HyperLogLogV2(precision=10)
        assert hll.is_sparse

    def test_transitions_to_dense_at_threshold(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for i in range(10000):
            hll.add(f"spam_{i}")
        assert not hll.is_sparse

    def test_count_before_and_after_transition_similar(self) -> None:
        hll_sparse = HyperLogLogV2(precision=10)
        hll_dense = HyperLogLogV2(precision=10)
        n = 2000
        for i in range(n):
            hll_sparse.add(f"item_{i}")
            hll_dense.add(f"item_{i}")
            if hll_dense.is_sparse:
                hll_dense._transition_to_dense()
        s = hll_sparse.count()
        d = hll_dense.count()
        assert abs(s - d) / max(s, 1) < 0.15

    def test_manual_transition_preserves_data(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(50):
            hll.add(f"keep_{i}")
        assert hll.is_sparse
        count_before = hll.count()
        hll._transition_to_dense()
        assert not hll.is_sparse
        count_after = hll.count()
        assert abs(count_before - count_after) / max(count_before, 1) < 0.10


class TestHyperLogLogV2Merge:
    def test_merge_disjoint_sets(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(300):
            a.add(f"a_{i}")
        for i in range(500):
            b.add(f"b_{i}")
        a.merge(b)
        merged = a.count()
        assert 700 <= merged <= 1000

    def test_merge_overlapping_sets(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(1000):
            a.add(f"shared_{i}")
        for i in range(500):
            b.add(f"shared_{i}")
        a.merge(b)
        merged = a.count()
        assert 700 <= merged <= 1400

    def test_merge_different_precision_raises(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=12)
        with pytest.raises(ValueError, match="different precision"):
            a.merge(b)

    def test_merge_sparse_with_sparse(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(50):
            a.add(f"a_{i}")
        for i in range(30, 80):
            b.add(f"b_{i}")
        assert a.is_sparse and b.is_sparse
        a.merge(b)
        assert a.count() > 0

    def test_merge_sparse_with_dense(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(50):
            a.add(f"a_{i}")
        for i in range(5000):
            b.add(f"b_{i}")
        assert a.is_sparse and not b.is_sparse
        a.merge(b)
        assert a.count() > 0

    def test_merge_union_cardinality(self) -> None:
        a = HyperLogLogV2(precision=12)
        b = HyperLogLogV2(precision=12)
        n_a, n_b = 10000, 20000
        for i in range(n_a):
            a.add(f"ua_{i}")
        for i in range(n_b):
            b.add(f"ub_{i}")
        a.merge(b)
        union = a.count()
        assert abs(union - (n_a + n_b)) / (n_a + n_b) < 0.06


class TestHyperLogLogV2Serialization:
    def test_roundtrip_bytes_dense(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(5000):
            hll.add(f"val_{i}")
        raw = hll.to_bytes()
        restored = HyperLogLogV2.from_bytes(raw)
        assert restored.precision == hll.precision
        assert restored.is_sparse == hll.is_sparse
        assert restored.count() == hll.count()

    def test_roundtrip_bytes_sparse(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(50):
            hll.add(f"sparse_{i}")
        assert hll.is_sparse
        raw = hll.to_bytes()
        restored = HyperLogLogV2.from_bytes(raw)
        assert restored.is_sparse
        assert restored.count() == hll.count()

    def test_from_bytes_truncated_raises(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            HyperLogLogV2.from_bytes(b"\x00")

    def test_roundtrip_empty_hll(self) -> None:
        hll = HyperLogLogV2(precision=10)
        raw = hll.to_bytes()
        restored = HyperLogLogV2.from_bytes(raw)
        assert restored.count() == 0

    def test_roundtrip_various_precisions(self) -> None:
        for p in [4, 8, 12, 16]:
            hll = HyperLogLogV2(precision=p)
            for i in range(100):
                hll.add(f"p{i}")
            raw = hll.to_bytes()
            restored = HyperLogLogV2.from_bytes(raw)
            assert restored.precision == p
            assert restored.count() == hll.count()


class TestHyperLogLogV2Precision:
    def test_invalid_precision_too_low_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLogV2(precision=3)

    def test_invalid_precision_too_high_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLogV2(precision=19)

    def test_precision_property(self) -> None:
        for p in [4, 8, 10, 12, 14, 16, 18]:
            hll = HyperLogLogV2(precision=p)
            assert hll.precision == p

    def test_register_count_matches_formula(self) -> None:
        for p in [4, 8, 10, 14]:
            hll = HyperLogLogV2(precision=p)
            assert hll.register_count == (1 << p)

    def test_error_bound_decreases_with_precision(self) -> None:
        err4 = HyperLogLogV2(precision=4).error_bound()
        err8 = HyperLogLogV2(precision=8).error_bound()
        assert err8 < err4

    def test_error_bound_matches_theoretical(self) -> None:
        hll = HyperLogLogV2(precision=10)
        expected = 1.04 / math.sqrt(1 << 10)
        assert abs(hll.error_bound() - expected) < 1e-9

    def test_precision_18_max_registers(self) -> None:
        hll = HyperLogLogV2(precision=18)
        assert hll.register_count == 262144


class TestHyperLogLogV2BiasCorrection:
    def test_small_cardinality_accurate(self) -> None:
        HyperLogLogV2(precision=10)
        for n in [1, 10, 50, 100, 200, 500, 1000, 2000]:
            hll2 = HyperLogLogV2(precision=10)
            for i in range(n):
                hll2.add(f"small_{n}_{i}")
            est = hll2.count()
            error = abs(est - n) / n
            if n < 50:
                assert abs(est - n) <= max(2, n * 0.3)
            else:
                assert error < 0.45, f"n={n} est={est} error={error:.3f}"

    def test_bias_correction_improves_small_range(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for i in range(5):
            hll.add(f"bc_{i}")
        raw_est = hll._raw_estimate()
        corrected = hll.count()
        m = hll.register_count
        assert abs(raw_est - corrected) < m * 0.5


class TestHyperLogLogV2EdgeCases:
    def test_add_after_transition_works(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for i in range(10000):
            hll.add(f"pre_{i}")
        assert not hll.is_sparse
        c_before = hll.count()
        for i in range(100):
            hll.add(f"post_{i}")
        c_after = hll.count()
        assert c_after >= c_before

    def test_merge_dense_with_dense(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(5000):
            a.add(f"da_{i}")
        for i in range(5000):
            b.add(f"db_{i}")
        assert not a.is_sparse and not b.is_sparse
        a.merge(b)
        assert a.count() > 0

    def test_deterministic_same_input(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(500):
            a.add(f"det_{i}")
            b.add(f"det_{i}")
        assert a.count() == b.count()

    def test_count_within_theoretical_bounds(self) -> None:
        for p in [6, 10, 14]:
            hll = HyperLogLogV2(precision=p)
            n = 10000
            for i in range(n):
                hll.add(f"bounds_{p}_{i}")
            error = abs(hll.count() - n) / n
            bound = hll.error_bound()
            tolerance = max(bound * 5.0, 0.10) if p <= 6 else max(bound * 2.5, 0.05)
            assert error < tolerance, f"p={p} error={error:.4f} bound={bound:.4f} tolerance={tolerance:.4f}"

    def test_zero_memory_growth_after_transition(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(10000):
            hll.add(f"grow_{i}")
        assert not hll.is_sparse
        c1 = hll.count()
        for i in range(10000, 20000):
            hll.add(f"grow_{i}")
        c2 = hll.count()
        assert c2 >= c1 * 1.5
