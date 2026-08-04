"""Deep tests for probabilistic data structures v2 — StableBloomFilter, CuckooFilter, CountingBloomFilter."""

from __future__ import annotations

import pytest

from general_ludd.probabilistic.counting_bloom import CountingBloomFilter
from general_ludd.probabilistic.cuckoo_filter import CuckooFilter
from general_ludd.probabilistic.stable_bloom import StableBloomFilter


class TestStableBloomFilterAddContains:
    def test_add_and_contains_single(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01)
        assert not sbf.contains("hello")
        sbf.add("hello")
        assert sbf.contains("hello")

    def test_add_and_contains_multiple(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01)
        items = ["alpha", "beta", "gamma", "delta", "epsilon"]
        for item in items:
            sbf.add(item)
        for item in items:
            assert sbf.contains(item)

    def test_non_member_not_reported(self):
        sbf = StableBloomFilter(capacity=10000, error_rate=0.001)
        sbf.add("present")
        assert not sbf.contains("absent")

    def test_empty_filter_contains_nothing(self):
        sbf = StableBloomFilter(capacity=100, error_rate=0.1)
        assert not sbf.contains("anything")


class TestStableBloomFilterDecay:
    def test_decay_reduces_count_over_time(self):
        sbf = StableBloomFilter(capacity=100, error_rate=0.01, seed=42)
        for i in range(100):
            sbf.add(f"item_{i}")
        assert sbf.contains("item_50")
        sbf.decay_all(steps=50)

    def test_decay_all_zeroes_eventually(self):
        sbf = StableBloomFilter(capacity=10, error_rate=0.5, counter_bits=4, seed=42)
        sbf.add("a")
        assert sbf.contains("a")
        sbf.decay_all(steps=200)
        assert sbf.count("a") <= 1

    def test_saturated_fraction_increases_with_adds(self):
        sbf = StableBloomFilter(capacity=50, error_rate=0.01, seed=42)
        frac_before = sbf.saturated_fraction()
        for i in range(100):
            sbf.add(f"item_{i}")
        frac_after = sbf.saturated_fraction()
        assert frac_after >= frac_before

    def test_saturated_fraction_bounded(self):
        sbf = StableBloomFilter(capacity=500, error_rate=0.05)
        for i in range(500):
            sbf.add(f"item_{i}")
        frac = sbf.saturated_fraction()
        assert 0.0 < frac <= 1.0


class TestStableBloomFilterProperties:
    def test_capacity_and_error_rate(self):
        sbf = StableBloomFilter(capacity=500, error_rate=0.05)
        assert sbf.capacity == 500
        assert sbf.error_rate == 0.05

    def test_hash_count_positive(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01)
        assert sbf.hash_count > 0

    def test_slot_count_positive(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01)
        assert sbf.slot_count > 0

    def test_decay_probability_positive(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01)
        assert 0.0 < sbf.decay_probability <= 1.0

    def test_estimated_count_after_inserts(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01)
        for i in range(200):
            sbf.add(f"item_{i}")
        est = sbf.estimated_count()
        assert est > 0

    def test_invalid_capacity_raises(self):
        with pytest.raises(ValueError):
            StableBloomFilter(capacity=0, error_rate=0.01)

    def test_invalid_error_rate_raises(self):
        with pytest.raises(ValueError):
            StableBloomFilter(capacity=100, error_rate=1.5)


class TestStableBloomFilterSerialization:
    def test_to_bytes_and_from_bytes(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01, seed=42)
        items = [f"bytes_{i}" for i in range(50)]
        for item in items:
            sbf.add(item)
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        for item in items:
            assert restored.contains(item)

    def test_to_bytes_preserves_parameters(self):
        sbf = StableBloomFilter(capacity=2000, error_rate=0.05)
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored.capacity == sbf.capacity
        assert restored.slot_count == sbf.slot_count
        assert restored.hash_count == sbf.hash_count

    def test_empty_filter_to_bytes(self):
        sbf = StableBloomFilter(capacity=100, error_rate=0.1)
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert not restored.contains("anything")


class TestCuckooFilterAddContains:
    def test_add_and_contains_single(self):
        cf = CuckooFilter(capacity=1000, error_rate=0.01)
        assert not cf.contains("hello")
        assert cf.add("hello")
        assert cf.contains("hello")

    def test_add_and_contains_multiple(self):
        cf = CuckooFilter(capacity=1000, error_rate=0.01)
        items = ["alpha", "beta", "gamma", "delta", "epsilon"]
        for item in items:
            assert cf.add(item)
        for item in items:
            assert cf.contains(item)

    def test_add_duplicate_ok(self):
        cf = CuckooFilter(capacity=1000, error_rate=0.01)
        assert cf.add("x")
        assert cf.add("x")
        assert cf.contains("x")

    def test_remove_existing_item(self):
        cf = CuckooFilter(capacity=1000, error_rate=0.01)
        cf.add("x")
        cf.add("y")
        assert cf.remove("x")
        assert not cf.contains("x")
        assert cf.contains("y")

    def test_remove_non_existent(self):
        cf = CuckooFilter(capacity=1000, error_rate=0.01)
        assert not cf.remove("absent")

    def test_remove_then_readd(self):
        cf = CuckooFilter(capacity=1000, error_rate=0.01)
        cf.add("x")
        cf.remove("x")
        cf.add("x")
        assert cf.contains("x")

    def test_non_member_not_reported(self):
        cf = CuckooFilter(capacity=10000, error_rate=0.001)
        cf.add("present")
        assert not cf.contains("absent")


class TestCuckooFilterProperties:
    def test_capacity_and_error_rate(self):
        cf = CuckooFilter(capacity=500, error_rate=0.05)
        assert cf.capacity == 500
        assert cf.error_rate == 0.05
        assert cf.num_buckets >= 2
        assert cf.bucket_size >= 2
        assert cf.fingerprint_bits >= 4

    def test_size_tracks_count(self):
        cf = CuckooFilter(capacity=1000, error_rate=0.01)
        assert cf.size == 0
        cf.add("a")
        assert cf.size == 1
        cf.add("b")
        assert cf.size == 2
        cf.remove("a")
        assert cf.size == 1

    def test_load_factor_below_capacity(self):
        cf = CuckooFilter(capacity=1000, error_rate=0.01)
        for i in range(500):
            cf.add(f"item_{i}")
        lf = cf.load_factor()
        assert 0.0 < lf < 1.0

    def test_invalid_capacity_raises(self):
        with pytest.raises(ValueError):
            CuckooFilter(capacity=0, error_rate=0.01)

    def test_invalid_error_rate_raises(self):
        with pytest.raises(ValueError):
            CuckooFilter(capacity=100, error_rate=1.5)


class TestCuckooFilterSerialization:
    def test_to_bytes_and_from_bytes(self):
        cf = CuckooFilter(capacity=1000, error_rate=0.01)
        items = [f"bytes_{i}" for i in range(50)]
        for item in items:
            cf.add(item)
        raw = cf.to_bytes()
        restored = CuckooFilter.from_bytes(raw)
        for item in items:
            assert restored.contains(item)

    def test_to_bytes_preserves_parameters(self):
        cf = CuckooFilter(capacity=2000, error_rate=0.05)
        raw = cf.to_bytes()
        restored = CuckooFilter.from_bytes(raw)
        assert restored.capacity == cf.capacity
        assert restored.num_buckets == cf.num_buckets
        assert restored.size == cf.size

    def test_empty_filter_to_bytes(self):
        cf = CuckooFilter(capacity=100, error_rate=0.1)
        raw = cf.to_bytes()
        restored = CuckooFilter.from_bytes(raw)
        assert restored.size == 0
        assert not restored.contains("anything")


class TestCuckooFilterEdgeCases:
    def test_unicode_characters(self):
        cf = CuckooFilter(capacity=500, error_rate=0.05)
        items = ["café", "naïve", "こんにちは", "😀"]
        for item in items:
            cf.add(item)
        for item in items:
            assert cf.contains(item)

    def test_very_long_string(self):
        cf = CuckooFilter(capacity=1000, error_rate=0.01)
        long_str = "x" * 10000
        cf.add(long_str)
        assert cf.contains(long_str)

    def test_add_until_near_capacity(self):
        cf = CuckooFilter(capacity=500, error_rate=0.01)
        ok = 0
        for i in range(400):
            if cf.add(f"item_{i}"):
                ok += 1
        assert ok > 0
        lf = cf.load_factor()
        assert lf > 0.3

    def test_remove_returns_to_zero(self):
        cf = CuckooFilter(capacity=500, error_rate=0.01)
        cf.add("only")
        assert cf.remove("only")
        assert cf.size == 0
        assert not cf.contains("only")


class TestCountingBloomFilterV2:
    def test_add_and_count(self):
        cbf = CountingBloomFilter(capacity=1000, error_rate=0.01, counter_bits=4)
        cbf.add("x")
        assert cbf.count("x") == 1
        cbf.add("x")
        assert cbf.count("x") == 2

    def test_remove_decrements_count(self):
        cbf = CountingBloomFilter(capacity=1000, error_rate=0.01)
        cbf.add("x")
        cbf.add("x")
        cbf.remove("x")
        assert cbf.count("x") == 1

    def test_remove_below_zero(self):
        cbf = CountingBloomFilter(capacity=1000, error_rate=0.01)
        cbf.remove("absent")
        assert cbf.count("absent") == 0

    def test_counter_saturation(self):
        cbf = CountingBloomFilter(capacity=10, error_rate=0.5, counter_bits=2)
        for _ in range(20):
            cbf.add("x")
        assert cbf.count("x") <= 3

    def test_merge_preserves_counts(self):
        cbf1 = CountingBloomFilter(capacity=1000, error_rate=0.01)
        cbf2 = CountingBloomFilter(capacity=1000, error_rate=0.01)
        cbf1.add("a")
        cbf1.add("a")
        cbf2.add("a")
        cbf1.merge(cbf2)
        assert cbf1.count("a") == 3

    def test_estimated_count_bounded(self):
        cbf = CountingBloomFilter(capacity=1000, error_rate=0.01)
        for i in range(200):
            cbf.add(f"item_{i}")
        est = cbf.estimated_count()
        assert est > 0

    def test_to_bytes_and_from_bytes(self):
        cbf = CountingBloomFilter(capacity=500, error_rate=0.05, counter_bits=4)
        items = [f"ser_{i}" for i in range(30)]
        for item in items:
            cbf.add(item)
        raw = cbf.to_bytes()
        restored = CountingBloomFilter.from_bytes(raw)
        for item in items:
            assert restored.contains(item)

    def test_empty_counting_estimated_zero(self):
        cbf = CountingBloomFilter(capacity=100, error_rate=0.1)
        assert cbf.estimated_count() == 0.0


class TestV2EdgeCases:
    def test_stable_bloom_long_string(self):
        sbf = StableBloomFilter(capacity=500, error_rate=0.05)
        long_str = "z" * 10000
        sbf.add(long_str)
        assert sbf.contains(long_str)

    def test_cuckoo_empty_string(self):
        cf = CuckooFilter(capacity=100, error_rate=0.1)
        cf.add("")
        assert cf.contains("")

    def test_counting_bloom_unicode(self):
        cbf = CountingBloomFilter(capacity=500, error_rate=0.05)
        items = ["café", "naïve", "こんにちは"]
        for item in items:
            cbf.add(item)
        for item in items:
            assert cbf.contains(item)
