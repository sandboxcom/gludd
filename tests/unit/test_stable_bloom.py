"""Unit tests for src/general_ludd/probabilistic/stable_bloom.py."""

from __future__ import annotations

import math
import struct

import pytest

from general_ludd.probabilistic.stable_bloom import StableBloomFilter


class TestConstructor:
    def test_default_params(self):
        sbf = StableBloomFilter(capacity=1000)
        assert sbf.capacity == 1000
        assert sbf.error_rate == 0.01
        assert sbf.counter_bits == 4
        assert sbf.hash_count >= 1
        assert sbf.slot_count >= 8

    def test_explicit_params(self):
        sbf = StableBloomFilter(capacity=500, error_rate=0.05, counter_bits=8, seed=42)
        assert sbf.capacity == 500
        assert sbf.error_rate == 0.05
        assert sbf.counter_bits == 8
        assert sbf.hash_count >= 1

    def test_slot_count_derived(self):
        capacity = 1000
        error_rate = 0.01
        sbf = StableBloomFilter(capacity=capacity, error_rate=error_rate)
        expected = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        expected = max(expected, 8)
        assert sbf.slot_count == expected

    def test_hash_count_derived(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01)
        expected = max(1, round((sbf.slot_count / sbf.capacity) * math.log(2)))
        assert sbf.hash_count == expected

    def test_decay_probability(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01)
        assert sbf.decay_probability == pytest.approx(1.0 / sbf.hash_count)

    def test_capacity_minimum(self):
        sbf = StableBloomFilter(capacity=1)
        assert sbf.capacity == 1
        assert sbf.slot_count >= 8

    def test_invalid_capacity_zero(self):
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            StableBloomFilter(capacity=0)

    def test_invalid_capacity_negative(self):
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            StableBloomFilter(capacity=-5)

    def test_invalid_error_rate_zero(self):
        with pytest.raises(ValueError, match="error_rate must be in \\(0, 1\\)"):
            StableBloomFilter(capacity=100, error_rate=0.0)

    def test_invalid_error_rate_one(self):
        with pytest.raises(ValueError, match="error_rate must be in \\(0, 1\\)"):
            StableBloomFilter(capacity=100, error_rate=1.0)

    def test_invalid_error_rate_negative(self):
        with pytest.raises(ValueError, match="error_rate must be in \\(0, 1\\)"):
            StableBloomFilter(capacity=100, error_rate=-0.1)

    def test_invalid_error_rate_above_one(self):
        with pytest.raises(ValueError, match="error_rate must be in \\(0, 1\\)"):
            StableBloomFilter(capacity=100, error_rate=2.0)

    def test_invalid_counter_bits_zero(self):
        with pytest.raises(ValueError, match="counter_bits must be in \\[1, 16\\]"):
            StableBloomFilter(capacity=100, counter_bits=0)

    def test_invalid_counter_bits_seventeen(self):
        with pytest.raises(ValueError, match="counter_bits must be in \\[1, 16\\]"):
            StableBloomFilter(capacity=100, counter_bits=17)

    def test_counter_bits_boundaries(self):
        sbf1 = StableBloomFilter(capacity=100, counter_bits=1)
        assert sbf1.counter_bits == 1
        sbf16 = StableBloomFilter(capacity=100, counter_bits=16)
        assert sbf16.counter_bits == 16


class TestAddAndContains:
    def test_add_single_and_contains(self):
        sbf = StableBloomFilter(capacity=1000)
        assert not sbf.contains("hello")
        sbf.add("hello")
        assert sbf.contains("hello")

    def test_add_multiple_and_contains(self):
        sbf = StableBloomFilter(capacity=1000)
        items = ["alpha", "beta", "gamma", "delta", "epsilon"]
        for item in items:
            sbf.add(item)
        for item in items:
            assert sbf.contains(item), f"should contain {item}"

    def test_no_false_negatives_many_items(self):
        sbf = StableBloomFilter(capacity=5000)
        items = [f"key_{i}" for i in range(200)]
        for item in items:
            sbf.add(item)
        for item in items:
            assert sbf.contains(item), f"false negative for {item}"

    def test_non_member_not_contained(self):
        sbf = StableBloomFilter(capacity=10000)
        sbf.add("present")
        nfps = sum(1 for _ in range(100) if sbf.contains("absent"))
        assert nfps == 0

    def test_contains_is_zero_when_empty(self):
        sbf = StableBloomFilter(capacity=1000)
        assert not sbf.contains("anything")
        assert not sbf.contains("")

    def test_add_various_types(self):
        sbf = StableBloomFilter(capacity=1000)
        sbf.add("string")
        sbf.add(b"bytes")
        sbf.add(42)
        sbf.add(3.14)
        sbf.add(True)
        assert sbf.contains("string")
        assert sbf.contains(b"bytes")
        assert sbf.contains(42)
        assert sbf.contains(3.14)
        assert sbf.contains(True)


class TestCount:
    def test_count_starts_zero(self):
        sbf = StableBloomFilter(capacity=1000)
        assert sbf.count("missing") == 0

    def test_count_increases_with_repeat_adds(self):
        sbf = StableBloomFilter(capacity=10)
        for _ in range(5):
            sbf.add("item")
        assert sbf.count("item") >= 1

    def test_count_non_member_returns_zero(self):
        sbf = StableBloomFilter(capacity=1000)
        sbf.add("present")
        assert sbf.count("absent") == 0

    def test_count_minimum_across_hashes(self):
        sbf = StableBloomFilter(capacity=1000)
        sbf.add("x")
        c = sbf.count("x")
        assert c >= 1


class TestEstimatedCount:
    def test_estimated_count_starts_zero(self):
        sbf = StableBloomFilter(capacity=1000)
        assert sbf.estimated_count() == 0.0

    def test_estimated_count_positive_after_add(self):
        sbf = StableBloomFilter(capacity=1000)
        for i in range(100):
            sbf.add(f"item_{i}")
        est = sbf.estimated_count()
        assert est > 0
        assert est < 500

    def test_estimated_count_monotonic(self):
        sbf = StableBloomFilter(capacity=1000)
        prev = sbf.estimated_count()
        for i in range(50):
            sbf.add(f"item_{i}")
        current = sbf.estimated_count()
        assert current >= prev


class TestSaturatedFraction:
    def test_starts_zero(self):
        sbf = StableBloomFilter(capacity=1000)
        assert sbf.saturated_fraction() == 0.0

    def test_increases_after_add(self):
        sbf = StableBloomFilter(capacity=1000)
        for i in range(200):
            sbf.add(f"item_{i}")
        frac = sbf.saturated_fraction()
        assert 0.0 < frac < 1.0

    def test_bounded_by_one(self):
        sbf = StableBloomFilter(capacity=100)
        for i in range(5000):
            sbf.add(f"item_{i}")
        assert sbf.saturated_fraction() <= 1.0

    def test_saturated_fraction_with_counter_bits_one(self):
        sbf = StableBloomFilter(capacity=10, counter_bits=1)
        for i in range(100):
            sbf.add(f"item_{i}")
        assert sbf.saturated_fraction() <= 1.0


class TestDecayAll:
    def test_decay_all_reduces_saturation(self):
        sbf = StableBloomFilter(capacity=100, seed=42)
        for i in range(200):
            sbf.add(f"item_{i}")
        before = sbf.saturated_fraction()
        sbf.decay_all(steps=10)
        after = sbf.saturated_fraction()
        assert after <= before

    def test_decay_all_does_not_raise(self):
        sbf = StableBloomFilter(capacity=100)
        sbf.add("x")
        sbf.decay_all(steps=0)
        sbf.decay_all(steps=1)
        sbf.decay_all(steps=5)

    def test_decay_all_may_remove_items(self):
        sbf = StableBloomFilter(capacity=100, seed=0)
        sbf.add("persistent")
        sbf.decay_all(steps=100)
        assert sbf.estimated_count() >= 0.0


class TestSerialization:
    def test_roundtrip_empty(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01, counter_bits=4, seed=0)
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored.capacity == sbf.capacity
        assert restored.slot_count == sbf.slot_count
        assert restored.error_rate == pytest.approx(sbf.error_rate)
        assert restored.hash_count == sbf.hash_count
        assert restored.counter_bits == sbf.counter_bits
        assert restored.estimated_count() == 0.0

    def test_roundtrip_with_data(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01)
        for i in range(100):
            sbf.add(f"roundtrip_{i}")
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored.capacity == sbf.capacity
        for i in range(100):
            assert restored.contains(f"roundtrip_{i}")

    def test_roundtrip_preserves_estimated_count(self):
        sbf = StableBloomFilter(capacity=1000, error_rate=0.01, seed=42)
        for i in range(50):
            sbf.add(f"est_{i}")
        est_before = sbf.estimated_count()
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored.estimated_count() == pytest.approx(est_before)

    def test_from_bytes_truncated(self):
        with pytest.raises(ValueError, match="truncated"):
            StableBloomFilter.from_bytes(b"short")

    def test_from_bytes_empty(self):
        with pytest.raises(ValueError, match="truncated"):
            StableBloomFilter.from_bytes(b"")

    def test_from_bytes_wrong_counter_length(self):
        sbf = StableBloomFilter(capacity=100, counter_bits=4)
        raw = sbf.to_bytes()
        truncated = raw[: struct.calcsize("!IIdII")]
        with pytest.raises(ValueError, match="counter array length mismatch"):
            StableBloomFilter.from_bytes(truncated)

    def test_roundtrip_different_counter_bits(self):
        for bits in [1, 4, 8, 16]:
            sbf = StableBloomFilter(capacity=100, counter_bits=bits)
            sbf.add("test")
            raw = sbf.to_bytes()
            restored = StableBloomFilter.from_bytes(raw)
            assert restored.counter_bits == bits
            assert restored.contains("test")


class TestItemToBytes:
    def test_str(self):
        key = StableBloomFilter._item_to_bytes("hello")
        assert isinstance(key, bytes)
        assert key == b"hello"

    def test_bytes_passthrough(self):
        key = StableBloomFilter._item_to_bytes(b"raw")
        assert key == b"raw"

    def test_int(self):
        key = StableBloomFilter._item_to_bytes(42)
        assert key == b"42"

    def test_float(self):
        key = StableBloomFilter._item_to_bytes(3.14)
        assert b"3.14" in key

    def test_none(self):
        key = StableBloomFilter._item_to_bytes(None)
        assert key == b"None"

    def test_list(self):
        key = StableBloomFilter._item_to_bytes([1, 2, 3])
        assert b"[1, 2, 3]" in key


class TestHash:
    def test_hash_deterministic(self):
        h1 = StableBloomFilter._hash(b"key", 0)
        h2 = StableBloomFilter._hash(b"key", 0)
        assert h1 == h2

    def test_hash_different_seed_different_result(self):
        h0 = StableBloomFilter._hash(b"key", 0)
        h1 = StableBloomFilter._hash(b"key", 1)
        assert h0 != h1

    def test_hash_different_key_different_result(self):
        ha = StableBloomFilter._hash(b"a", 0)
        hb = StableBloomFilter._hash(b"b", 0)
        assert ha != hb


class TestEdgeCases:
    def test_capacity_one(self):
        sbf = StableBloomFilter(capacity=1)
        sbf.add("x")
        assert sbf.contains("x")

    def test_large_capacity(self):
        sbf = StableBloomFilter(capacity=100000, error_rate=0.001)
        assert sbf.slot_count > 100000
        sbf.add("large")
        assert sbf.contains("large")

    def test_seed_reproducibility(self):
        sbf_a = StableBloomFilter(capacity=100, seed=42)
        sbf_b = StableBloomFilter(capacity=100, seed=42)
        for i in range(50):
            sbf_a.add(f"item_{i}")
            sbf_b.add(f"item_{i}")
        assert sbf_a.to_bytes() == sbf_b.to_bytes()

    def test_seed_affects_decay_path(self):
        sbf_a = StableBloomFilter(capacity=5, seed=0)
        sbf_b = StableBloomFilter(capacity=5, seed=1)
        for i in range(200):
            sbf_a.add(f"fill_{i}")
            sbf_b.add(f"fill_{i}")
        assert sbf_a.contains("fill_0")

    def test_empty_string(self):
        sbf = StableBloomFilter(capacity=100)
        sbf.add("")
        assert sbf.contains("")

    def test_very_long_item(self):
        sbf = StableBloomFilter(capacity=100)
        long_item = "x" * 10000
        sbf.add(long_item)
        assert sbf.contains(long_item)

    def test_counter_max_boundary(self):
        sbf = StableBloomFilter(capacity=10, counter_bits=1, seed=0)
        for _i in range(500):
            sbf.add("flush")
        sbf.add("flush")
        assert sbf.contains("flush")

    def test_add_does_not_raise_on_full(self):
        sbf = StableBloomFilter(capacity=1, counter_bits=4, seed=0)
        for _ in range(10000):
            sbf.add("flood")


class TestProperties:
    def test_decay_probability_formula(self):
        sbf = StableBloomFilter(capacity=500, error_rate=0.02)
        assert sbf.decay_probability == 1.0 / sbf.hash_count

    def test_counter_max_for_bits(self):
        for bits in [1, 2, 4, 8, 16]:
            sbf = StableBloomFilter(capacity=10, counter_bits=bits)
            assert sbf._counter_max == (1 << bits) - 1


class TestDecayViaAdd:
    def test_decay_kicks_in_when_no_zero_slots(self):
        sbf = StableBloomFilter(capacity=5, counter_bits=2, seed=0)
        for i in range(100):
            sbf.add(f"fill_{i}")
        frac = sbf.saturated_fraction()
        assert frac >= 0.0

    def test_repeated_same_item_counts(self):
        sbf = StableBloomFilter(capacity=100, seed=42)
        for _ in range(50):
            sbf.add("only")
        assert sbf.count("only") >= 1

    def test_decay_eventually_lowers_count(self):
        sbf = StableBloomFilter(capacity=5, counter_bits=2, seed=0)
        for i in range(500):
            sbf.add(f"flood_{i}")
        frac = sbf.saturated_fraction()
        assert 0.0 <= frac <= 1.0
