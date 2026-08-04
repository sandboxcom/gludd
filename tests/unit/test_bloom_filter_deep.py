"""Deep tests for src/general_ludd/bloom_filter.py — probabilistic set data structure."""

from __future__ import annotations

import math
import pickle

import pytest

from general_ludd.bloom_filter import BloomFilter


class TestBloomFilterAddContains:
    def test_add_and_contains_single(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        assert not bf.contains("hello")
        bf.add("hello")
        assert bf.contains("hello")

    def test_add_and_contains_multiple(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        items = ["alpha", "beta", "gamma", "delta", "epsilon"]
        for item in items:
            bf.add(item)
        for item in items:
            assert bf.contains(item)

    def test_add_union_semantics(self):
        bf = BloomFilter(capacity=500, error_rate=0.05)
        items = [f"key_{i}" for i in range(200)]
        for item in items:
            bf.add(item)
        assert all(bf.contains(item) for item in items)

    def test_non_member_not_reported(self):
        bf = BloomFilter(capacity=10000, error_rate=0.001)
        bf.add("present")
        assert not bf.contains("absent")


class TestFalsePositiveRate:
    def test_empirical_fpr_within_tolerance(self):
        capacity = 10000
        error_rate = 0.01
        bf = BloomFilter(capacity=capacity, error_rate=error_rate)
        inserted = {f"inserted_{i}" for i in range(capacity // 2)}
        for item in inserted:
            bf.add(item)
        trials = 100000
        fps = sum(1 for i in range(trials) if bf.contains(f"absent_{i}") and f"absent_{i}" not in inserted)
        empirical_fpr = fps / trials
        assert empirical_fpr < error_rate * 3

    def test_fpr_increases_near_capacity(self):
        bf_small = BloomFilter(capacity=1000, error_rate=0.05)
        for i in range(1000):
            bf_small.add(f"item_{i}")
        trials = 50000
        fps = sum(1 for i in range(trials) if bf_small.contains(f"test_{i}"))
        fpr = fps / trials
        assert fpr < 0.30

    def test_fpr_stays_zero_below_capacity(self):
        bf = BloomFilter(capacity=100000, error_rate=0.0001)
        for i in range(100):
            bf.add(f"low_{i}")
        trials = 50000
        fps = sum(1 for i in range(trials) if bf.contains(f"check_{i}"))
        fpr = fps / trials
        assert fpr < 0.01


class TestCapacityCalculation:
    def test_minimal_capacity(self):
        bf = BloomFilter(capacity=1, error_rate=0.01)
        assert bf.capacity == 1
        bf.add("x")
        assert bf.contains("x")

    def test_large_capacity(self):
        bf = BloomFilter(capacity=1_000_000, error_rate=0.001)
        assert bf.capacity == 1_000_000

    def test_error_rate_affects_size(self):
        bf_tight = BloomFilter(capacity=1000, error_rate=0.001)
        bf_loose = BloomFilter(capacity=1000, error_rate=0.1)
        assert bf_tight.size > 0
        assert bf_loose.size > 0
        assert bf_tight.size > bf_loose.size

    def test_bit_size_formula(self):
        capacity = 5000
        error_rate = 0.01
        bf = BloomFilter(capacity=capacity, error_rate=error_rate)
        expected = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        assert bf.size == expected


class TestHashFunctionCount:
    def test_optimal_hash_count(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        assert bf.hash_count > 0

    def test_hash_count_formula(self):
        capacity = 5000
        error_rate = 0.01
        bf = BloomFilter(capacity=capacity, error_rate=error_rate)
        expected = max(1, round((bf.size / capacity) * math.log(2)))
        assert bf.hash_count == expected

    def test_different_error_rates_different_hash_count(self):
        bf1 = BloomFilter(capacity=1000, error_rate=0.001)
        bf2 = BloomFilter(capacity=1000, error_rate=0.1)
        assert bf1.hash_count != bf2.hash_count


class TestSerialization:
    def test_pickle_roundtrip(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        items = [f"pickle_{i}" for i in range(100)]
        for item in items:
            bf.add(item)
        data = pickle.dumps(bf)
        restored: BloomFilter = pickle.loads(data)
        for item in items:
            assert restored.contains(item)

    def test_to_bytes_and_from_bytes(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        items = [f"bytes_{i}" for i in range(50)]
        for item in items:
            bf.add(item)
        raw = bf.to_bytes()
        restored = BloomFilter.from_bytes(raw)
        for item in items:
            assert restored.contains(item)

    def test_to_bytes_roundtrip_preserves_parameters(self):
        bf = BloomFilter(capacity=2000, error_rate=0.05)
        raw = bf.to_bytes()
        restored = BloomFilter.from_bytes(raw)
        assert restored.capacity == bf.capacity
        assert restored.size == bf.size
        assert restored.hash_count == bf.hash_count

    def test_empty_filter_to_bytes(self):
        bf = BloomFilter(capacity=100, error_rate=0.1)
        raw = bf.to_bytes()
        restored = BloomFilter.from_bytes(raw)
        assert not restored.contains("anything")


class TestMerge:
    def test_merge_two_filters(self):
        bf1 = BloomFilter(capacity=1000, error_rate=0.01)
        bf2 = BloomFilter(capacity=1000, error_rate=0.01)
        bf1.add("a")
        bf1.add("b")
        bf2.add("c")
        bf2.add("d")
        bf1.merge(bf2)
        assert bf1.contains("a")
        assert bf1.contains("b")
        assert bf1.contains("c")
        assert bf1.contains("d")

    def test_merge_rejects_mismatched_params(self):
        bf1 = BloomFilter(capacity=1000, error_rate=0.01)
        bf2 = BloomFilter(capacity=500, error_rate=0.01)
        with pytest.raises(ValueError):
            bf1.merge(bf2)

    def test_merge_empty_filter(self):
        bf_full = BloomFilter(capacity=1000, error_rate=0.01)
        bf_full.add("x")
        bf_empty = BloomFilter(capacity=1000, error_rate=0.01)
        bf_full.merge(bf_empty)
        assert bf_full.contains("x")

    def test_merge_idempotent(self):
        bf1 = BloomFilter(capacity=500, error_rate=0.05)
        bf1.add("stable")
        bf2 = BloomFilter(capacity=500, error_rate=0.05)
        bf2.add("stable")
        bf1.merge(bf2)
        assert bf1.contains("stable")
        bf1.merge(bf2)
        assert bf1.contains("stable")


class TestEdgeCases:
    def test_empty_string(self):
        bf = BloomFilter(capacity=100, error_rate=0.1)
        bf.add("")
        assert bf.contains("")

    def test_unicode_characters(self):
        bf = BloomFilter(capacity=500, error_rate=0.05)
        items = ["café", "naïve", "こんにちは", "😀"]
        for item in items:
            bf.add(item)
        for item in items:
            assert bf.contains(item)

    def test_very_long_string(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        long_str = "x" * 10000
        bf.add(long_str)
        assert bf.contains(long_str)

    def test_add_int_and_other_types(self):
        bf = BloomFilter(capacity=500, error_rate=0.05)
        bf.add(42)
        bf.add(3.14)
        bf.add(b"bytes")
        assert bf.contains(42)
        assert bf.contains(3.14)
        assert bf.contains(b"bytes")
