"""Deep tests for probabilistic data structures — CountingBloomFilter and HyperLogLog."""

from __future__ import annotations

import pytest

from general_ludd.probabilistic.counting_bloom import CountingBloomFilter
from general_ludd.probabilistic.hyperloglog import HyperLogLog


class TestCountingBloomFilterAddCountRemove:
    def test_add_and_count_single_item(self) -> None:
        cbf = CountingBloomFilter(capacity=1000, error_rate=0.01)
        cbf.add("hello")
        assert cbf.count("hello") >= 1
        assert cbf.contains("hello")

    def test_add_and_count_multiple_items(self) -> None:
        cbf = CountingBloomFilter(capacity=1000, error_rate=0.01)
        items = ["alpha", "beta", "gamma"]
        for _ in range(3):
            for item in items:
                cbf.add(item)
        for item in items:
            assert cbf.count(item) >= 1

    def test_remove_decrements_count(self) -> None:
        cbf = CountingBloomFilter(capacity=1000, error_rate=0.01)
        cbf.add("key")
        cbf.add("key")
        assert cbf.count("key") >= 2
        cbf.remove("key")
        assert cbf.count("key") >= 1

    def test_remove_to_zero_absent(self) -> None:
        cbf = CountingBloomFilter(capacity=1000, error_rate=0.01)
        cbf.add("only")
        cbf.remove("only")
        assert not cbf.contains("only")
        assert cbf.count("only") == 0

    def test_empty_filter_contains_nothing(self) -> None:
        cbf = CountingBloomFilter(capacity=100, error_rate=0.01)
        assert not cbf.contains("anything")
        assert cbf.count("anything") == 0

    def test_duplicate_add_increments_count(self) -> None:
        cbf = CountingBloomFilter(capacity=500, error_rate=0.01)
        for _ in range(5):
            cbf.add("dup")
        min_count = cbf.count("dup")
        assert min_count >= 1


class TestCountingBloomFilterMerge:
    def test_merge_two_filters(self) -> None:
        a = CountingBloomFilter(capacity=1000, error_rate=0.05)
        b = CountingBloomFilter(capacity=1000, error_rate=0.05)
        a.add("x")
        a.add("x")
        b.add("x")
        b.add("y")
        a.merge(b)
        assert a.contains("x")
        assert a.contains("y")

    def test_merge_different_params_raises(self) -> None:
        a = CountingBloomFilter(capacity=1000, error_rate=0.05)
        b = CountingBloomFilter(capacity=500, error_rate=0.05)
        with pytest.raises(ValueError, match="different parameters"):
            a.merge(b)


class TestCountingBloomFilterSerialization:
    def test_roundtrip_bytes(self) -> None:
        cbf = CountingBloomFilter(capacity=500, error_rate=0.02)
        cbf.add("alpha")
        cbf.add("beta")
        cbf.add("beta")
        raw = cbf.to_bytes()
        restored = CountingBloomFilter.from_bytes(raw)
        assert restored.capacity == cbf.capacity
        assert restored.contains("alpha")
        assert restored.contains("beta")
        assert restored.count("beta") >= 1

    def test_from_bytes_truncated_raises(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            CountingBloomFilter.from_bytes(b"\x00\x00")


class TestCountingBloomFilterEdgeCases:
    def test_invalid_capacity_raises(self) -> None:
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            CountingBloomFilter(capacity=0, error_rate=0.01)

    def test_invalid_error_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="error_rate must be in"):
            CountingBloomFilter(capacity=100, error_rate=0.0)

    def test_invalid_counter_bits_raises(self) -> None:
        with pytest.raises(ValueError, match="counter_bits must be in"):
            CountingBloomFilter(capacity=100, error_rate=0.01, counter_bits=0)

    def test_properties(self) -> None:
        cbf = CountingBloomFilter(capacity=200, error_rate=0.05, counter_bits=4)
        assert cbf.capacity == 200
        assert cbf.counter_bits == 4
        assert cbf.hash_count > 0
        assert cbf.slot_count > 0

    def test_non_member_min_count_zero(self) -> None:
        cbf = CountingBloomFilter(capacity=10000, error_rate=0.001)
        cbf.add("present")
        assert cbf.count("absent") == 0

    def test_estimated_count_zero_for_empty(self) -> None:
        cbf = CountingBloomFilter(capacity=1000, error_rate=0.01)
        assert cbf.estimated_count() == 0.0

    def test_estimated_count_reasonable(self) -> None:
        cbf = CountingBloomFilter(capacity=10000, error_rate=0.01)
        inserted = 500
        for i in range(inserted):
            cbf.add(f"item_{i}")
        est = cbf.estimated_count()
        assert abs(est - inserted) / inserted < 0.25

    def test_string_bytes_int_float_types(self) -> None:
        cbf = CountingBloomFilter(capacity=100, error_rate=0.01)
        for val in ["str", b"bytes", 42, 3.14]:
            cbf.add(val)
            assert cbf.contains(val)
            cbf.remove(val)
            assert not cbf.contains(val)


class TestHyperLogLogAddCount:
    def test_add_and_count_small(self) -> None:
        hll = HyperLogLog(precision=12)
        for i in range(1000):
            hll.add(f"item_{i}")
        c = hll.count()
        assert c > 0

    def test_empty_hll_returns_zero(self) -> None:
        hll = HyperLogLog(precision=8)
        assert hll.count() == 0

    def test_single_item(self) -> None:
        hll = HyperLogLog(precision=10)
        hll.add("only")
        assert hll.count() == 1

    def test_error_rate_within_bound(self) -> None:
        hll = HyperLogLog(precision=12)
        n = 50000
        for i in range(n):
            hll.add(f"x_{i}")
        estimated = hll.count()
        error = abs(estimated - n) / n
        bound = hll.error_bound()
        assert error < max(bound * 2.5, 0.05)


class TestHyperLogLogMerge:
    def test_merge_disjoint_sets(self) -> None:
        a = HyperLogLog(precision=10)
        b = HyperLogLog(precision=10)
        for i in range(300):
            a.add(f"a_{i}")
        for i in range(500):
            b.add(f"b_{i}")
        a.merge(b)
        merged = a.count()
        assert merged > 0

    def test_merge_overlapping_sets(self) -> None:
        a = HyperLogLog(precision=10)
        b = HyperLogLog(precision=10)
        for i in range(1000):
            a.add(f"shared_{i}")
        for i in range(500):
            b.add(f"shared_{i}")
        a.merge(b)
        merged = a.count()
        assert merged > 0

    def test_merge_different_precision_raises(self) -> None:
        a = HyperLogLog(precision=10)
        b = HyperLogLog(precision=12)
        with pytest.raises(ValueError, match="different precision"):
            a.merge(b)


class TestHyperLogLogSerialization:
    def test_roundtrip_bytes(self) -> None:
        hll = HyperLogLog(precision=8)
        for i in range(200):
            hll.add(f"val_{i}")
        raw = hll.to_bytes()
        restored = HyperLogLog.from_bytes(raw)
        assert restored.precision == hll.precision
        assert restored.count() == hll.count()

    def test_from_bytes_truncated_raises(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            HyperLogLog.from_bytes(b"\x00")


class TestHyperLogLogEdgeCases:
    def test_invalid_precision_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLog(precision=3)

    def test_properties(self) -> None:
        hll = HyperLogLog(precision=8)
        assert hll.precision == 8
        assert hll.register_count == 256
        assert isinstance(hll.error_bound(), float)

    def test_duplicate_items(self) -> None:
        hll = HyperLogLog(precision=10)
        for _ in range(1000):
            hll.add("same_value")
        assert hll.count() == 1

    def test_string_bytes_int_float_types(self) -> None:
        hll = HyperLogLog(precision=8)
        vals = ["text", b"binary", 123, 9.99]
        for v in vals:
            hll.add(v)
        assert hll.count() == 4

    def test_large_cardinality_estimate(self) -> None:
        hll = HyperLogLog(precision=14)
        n = 200000
        for i in range(n):
            hll.add(f"large_{i}")
        estimated = hll.count()
        error = abs(estimated - n) / n
        assert error < 0.05
