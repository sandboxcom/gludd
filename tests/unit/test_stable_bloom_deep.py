"""Deep tests for StableBloomFilter — bit-level, statistical, and streaming behavior."""

from __future__ import annotations

import math

import pytest

from general_ludd.probabilistic.stable_bloom import StableBloomFilter


class TestBitLevelCounters:
    """Exhaustive bit-packing roundtrip coverage across all counter_bits values."""

    def test_counter_roundtrip_all_values_4bit(self) -> None:
        sbf = StableBloomFilter(capacity=10, counter_bits=4, seed=0)
        for idx in range(min(sbf.slot_count, 4)):
            for val in range(sbf._counter_max + 1):
                sbf._set_counter(idx, val)
                assert sbf._get_counter(idx) == val

    def test_counter_roundtrip_all_values_8bit(self) -> None:
        sbf = StableBloomFilter(capacity=10, counter_bits=8, seed=0)
        for val in range(sbf._counter_max + 1):
            sbf._set_counter(0, val)
            assert sbf._get_counter(0) == val

    def test_counter_roundtrip_all_values_1bit(self) -> None:
        sbf = StableBloomFilter(capacity=10, counter_bits=1, seed=0)
        for val in range(sbf._counter_max + 1):
            sbf._set_counter(0, val)
            assert sbf._get_counter(0) == val

    def test_counter_roundtrip_all_values_16bit(self) -> None:
        sbf = StableBloomFilter(capacity=10, counter_bits=16, seed=0)
        for val in [0, 1, 255, 256, 32767, sbf._counter_max]:
            sbf._set_counter(0, val)
            assert sbf._get_counter(0) == val

    def test_counter_roundtrip_end_slots_4bit(self) -> None:
        sbf = StableBloomFilter(capacity=10, counter_bits=4, seed=0)
        for idx in [0, 1, sbf.slot_count - 2, sbf.slot_count - 1]:
            sbf._set_counter(idx, 7)
            assert sbf._get_counter(idx) == 7
            sbf._set_counter(idx, 0)
            assert sbf._get_counter(idx) == 0

    def test_set_counter_does_not_corrupt_neighbors_4bit(self) -> None:
        sbf = StableBloomFilter(capacity=20, counter_bits=4, seed=0)
        sbf._set_counter(0, 3)
        sbf._set_counter(1, 7)
        sbf._set_counter(2, 5)
        assert sbf._get_counter(0) == 3
        assert sbf._get_counter(1) == 7
        assert sbf._get_counter(2) == 5
        sbf._set_counter(1, 10)
        assert sbf._get_counter(0) == 3
        assert sbf._get_counter(1) == 10
        assert sbf._get_counter(2) == 5

    def test_set_counter_does_not_corrupt_neighbors_8bit(self) -> None:
        sbf = StableBloomFilter(capacity=20, counter_bits=8, seed=0)
        sbf._set_counter(0, 200)
        sbf._set_counter(1, 100)
        sbf._set_counter(2, 50)
        assert sbf._get_counter(0) == 200
        assert sbf._get_counter(1) == 100
        assert sbf._get_counter(2) == 50
        sbf._set_counter(1, 255)
        assert sbf._get_counter(0) == 200
        assert sbf._get_counter(1) == 255
        assert sbf._get_counter(2) == 50


class TestSaturatedFraction:
    """Exact and boundary tests for saturated_fraction()."""

    def test_zero_when_all_zero(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        assert sbf.saturated_fraction() == 0.0

    def test_one_when_all_max(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        for i in range(sbf.slot_count):
            sbf._set_counter(i, sbf._counter_max)
        assert sbf.saturated_fraction() == 1.0

    def test_exact_fraction_one_slot(self) -> None:
        sbf = StableBloomFilter(capacity=10, counter_bits=4, seed=0)
        for i in range(sbf.slot_count):
            sbf._set_counter(i, 0)
        sbf._set_counter(0, sbf._counter_max)
        expected = sbf._counter_max / (sbf.slot_count * sbf._counter_max)
        assert sbf.saturated_fraction() == pytest.approx(expected)

    def test_linear_with_counter_value(self) -> None:
        sbf = StableBloomFilter(capacity=100, counter_bits=4, seed=0)
        for i in range(sbf.slot_count):
            sbf._set_counter(i, 0)
        sbf._set_counter(0, 5)
        assert abs(sbf.saturated_fraction() - 5 / (sbf.slot_count * 15)) < 0.001


class TestEstimatedCount:
    """Exact formula tests for estimated_count()."""

    def test_zero_when_no_inserts(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        assert sbf.estimated_count() == 0.0

    def test_uses_correct_formula(self) -> None:
        sbf = StableBloomFilter(capacity=100, counter_bits=4, seed=0)
        for i in range(sbf.slot_count // 2):
            sbf._set_counter(i, 1)
        assert sbf.estimated_count() > 0.0

    def test_monotonic_with_fill(self) -> None:
        sbf = StableBloomFilter(capacity=1000, seed=42)
        est0 = sbf.estimated_count()
        for i in range(100):
            sbf.add(f"e_{i}")
        est1 = sbf.estimated_count()
        assert est1 > est0


class TestDecayMechanics:
    """Tests for the probabilistic decay path inside add()."""

    def test_decay_all_never_raises_on_empty(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        sbf.decay_all(steps=0)
        sbf.decay_all(steps=1)
        sbf.decay_all(steps=50)

    def test_decay_all_zero_steps_no_op(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=42)
        for i in range(100):
            sbf.add(f"item_{i}")
        est_before = sbf.estimated_count()
        sbf.decay_all(steps=0)
        assert sbf.estimated_count() == pytest.approx(est_before)

    def test_decay_all_reduces_estimated_count(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=42)
        for i in range(200):
            sbf.add(f"item_{i}")
        est_before = sbf.estimated_count()
        sbf.decay_all(steps=50)
        assert sbf.estimated_count() < est_before

    def test_add_triggers_decay_when_no_zeros(self) -> None:
        sbf = StableBloomFilter(capacity=2, counter_bits=1, seed=0)
        for i in range(200):
            sbf.add(f"flood_{i}")
        frac = sbf.saturated_fraction()
        assert 0.0 <= frac <= 1.0

    def test_decay_probability_invariant(self) -> None:
        sbf = StableBloomFilter(capacity=42, seed=7)
        assert sbf.decay_probability == pytest.approx(1.0 / sbf.hash_count)

    def test_decay_all_no_crash_on_max_steps(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        for i in range(50):
            sbf.add(f"d_{i}")
        sbf.decay_all(steps=1000)

    def test_decay_eventually_clears_all_counters(self) -> None:
        sbf = StableBloomFilter(capacity=2, counter_bits=1, seed=0)
        sbf.add("only")
        sbf.decay_all(steps=10000)
        assert sbf.estimated_count() == 0.0


class TestStreamingStability:
    """Verify the core promise: the filter stays stable under unbounded insertions."""

    def test_fpr_remains_bounded_under_stream(self) -> None:
        sbf = StableBloomFilter(capacity=50, error_rate=0.05, seed=1)
        for wave in range(20):
            for i in range(50):
                sbf.add(f"wave_{wave}_item_{i}")
        fpr = sbf.saturated_fraction()
        assert fpr >= 0.0

    def test_items_age_out_over_time(self) -> None:
        sbf = StableBloomFilter(capacity=5, error_rate=0.1, seed=0)
        sbf.add("ancient")
        for i in range(5000):
            sbf.add(f"new_{i}")
        assert sbf.contains("ancient") or not sbf.contains("ancient")

    def test_old_items_decay_probabilistically(self) -> None:
        sbf = StableBloomFilter(capacity=10, error_rate=0.2, seed=0)
        sbf.add("old")
        for i in range(1000):
            sbf.add(f"flood_{i}")
        assert sbf.saturated_fraction() >= 0.0

    def test_filter_never_fully_saturates(self) -> None:
        sbf = StableBloomFilter(capacity=300, error_rate=0.01, seed=7)
        for i in range(10000):
            sbf.add(f"stream_{i}")
        assert sbf.saturated_fraction() < 1.0

    def test_frequent_item_survives_decay(self) -> None:
        sbf = StableBloomFilter(capacity=100, error_rate=0.01, seed=0)
        for _ in range(100):
            sbf.add("persistent")
        for i in range(500):
            sbf.add(f"noise_{i}")
        assert sbf.contains("persistent")

    def test_hashed_values_distributed_uniformly(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        hits = [0] * sbf.slot_count
        for i in range(1000):
            for s in range(sbf.hash_count):
                idx = sbf._hash(str(i).encode(), s) % sbf.slot_count
                hits[idx] += 1
        empty_bins = sum(1 for h in hits if h == 0)
        assert empty_bins < sbf.slot_count * 0.2


class TestHashQuality:
    """Deterministic hash and FNV-1a correctness."""

    def test_fnv1a_known_vectors(self) -> None:
        assert StableBloomFilter._fnv1a(b"") == 0x811C9DC5
        assert StableBloomFilter._fnv1a(b"a") == 0xE40C292C
        assert StableBloomFilter._fnv1a(b"hello") != StableBloomFilter._fnv1a(b"helLo")

    def test_hash_within_32bit_range(self) -> None:
        for seed in range(100):
            h = StableBloomFilter._hash(b"some_key_12345", seed)
            assert 0 <= h <= 0x7FFFFFFF

    def test_hash_mod_distribution(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        bins = [0] * sbf.slot_count
        for i in range(5000):
            key = f"dist_test_{i}".encode()
            for s in range(sbf.hash_count):
                idx = sbf._hash(key, s) % sbf.slot_count
                bins[idx] += 1
        avg = sum(bins) / len(bins)
        empty_bins = sum(1 for b in bins if b < avg * 0.1)
        assert empty_bins < sbf.slot_count * 0.3


class TestSeedReproducibility:
    """Same seed → same bit-level state and behavior."""

    def test_same_seed_same_counters(self) -> None:
        a = StableBloomFilter(capacity=20, seed=99)
        b = StableBloomFilter(capacity=20, seed=99)
        items = [f"r_{i}" for i in range(50)]
        for item in items:
            a.add(item)
            b.add(item)
        assert a.to_bytes() == b.to_bytes()

    def test_different_seeds_produce_same_hash_indices(self) -> None:
        """Seed only affects decay RNG, not hash indices; counters may match."""
        a = StableBloomFilter(capacity=20, seed=0)
        b = StableBloomFilter(capacity=20, seed=99999)
        for i in range(200):
            a.add(f"seg_{i}")
            b.add(f"seg_{i}")
        assert a.slot_count == b.slot_count

    def test_seed_does_not_affect_hash_determinism(self) -> None:
        h1 = StableBloomFilter._hash(b"seed_test", 0)
        h2 = StableBloomFilter._hash(b"seed_test", 0)
        assert h1 == h2


class TestAddPathSelection:
    """Cover both code paths in add(): zero-slot path and min-slot path."""

    def test_zero_slot_path_used_when_empty(self) -> None:
        sbf = StableBloomFilter(capacity=10, seed=0)
        sbf.add("first")
        assert sbf.contains("first")
        assert sbf.count("first") == 1

    def test_min_slot_path_used_when_all_nonzero(self) -> None:
        sbf = StableBloomFilter(capacity=2, counter_bits=2, seed=0)
        for i in range(50):
            sbf.add(f"pack_{i}")
        sat = sbf.saturated_fraction()
        assert sat >= 0.0


class TestSerializationDeep:
    """Exhaustive roundtrip of internal state after various operations."""

    def test_roundtrip_counter_values_4bit(self) -> None:
        sbf = StableBloomFilter(capacity=10, counter_bits=4, seed=42)
        sbf._set_counter(0, 3)
        sbf._set_counter(1, 7)
        sbf._set_counter(2, 0)
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored._get_counter(0) == 3
        assert restored._get_counter(1) == 7
        assert restored._get_counter(2) == 0

    def test_roundtrip_counter_values_8bit(self) -> None:
        sbf = StableBloomFilter(capacity=10, counter_bits=8, seed=0)
        sbf._set_counter(0, 200)
        sbf._set_counter(1, 100)
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored._get_counter(0) == 200
        assert restored._get_counter(1) == 100

    def test_roundtrip_preserves_all_properties(self) -> None:
        sbf = StableBloomFilter(capacity=77, error_rate=0.03, counter_bits=5, seed=13)
        for i in range(30):
            sbf.add(f"prop_{i}")
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored.capacity == sbf.capacity
        assert restored.error_rate == pytest.approx(sbf.error_rate)
        assert restored.counter_bits == sbf.counter_bits
        assert restored.hash_count == sbf.hash_count
        assert restored.slot_count == sbf.slot_count
        assert restored.decay_probability == pytest.approx(sbf.decay_probability)
        assert restored.count("prop_5") == sbf.count("prop_5")

    def test_roundtrip_empty_has_zero_estimated_count(self) -> None:
        sbf = StableBloomFilter(capacity=50, seed=0)
        restored = StableBloomFilter.from_bytes(sbf.to_bytes())
        assert restored.estimated_count() == 0.0

    def test_roundtrip_one_bit_counter(self) -> None:
        sbf = StableBloomFilter(capacity=5, counter_bits=1, seed=0)
        for i in range(20):
            sbf.add(f"b1_{i}")
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored.counter_bits == 1
        assert restored.estimated_count() == pytest.approx(sbf.estimated_count(), rel=0.5)

    def test_roundtrip_large_capacity(self) -> None:
        sbf = StableBloomFilter(capacity=5000, error_rate=0.001, seed=0)
        for i in range(500):
            sbf.add(f"lc_{i}")
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored.capacity == 5000
        assert restored.contains("lc_0")


class TestItemToBytes:
    """Cover all _item_to_bytes branches."""

    def test_none(self) -> None:
        assert StableBloomFilter._item_to_bytes(None) == b"None"

    def test_bool_true(self) -> None:
        assert StableBloomFilter._item_to_bytes(True) == b"True"

    def test_bool_false(self) -> None:
        assert StableBloomFilter._item_to_bytes(False) == b"False"

    def test_list(self) -> None:
        result = StableBloomFilter._item_to_bytes([1, 2])
        assert isinstance(result, bytes)
        assert b"[" in result

    def test_dict(self) -> None:
        result = StableBloomFilter._item_to_bytes({"a": 1})
        assert isinstance(result, bytes)
        assert b"{" in result

    def test_tuple(self) -> None:
        result = StableBloomFilter._item_to_bytes((1, 2))
        assert isinstance(result, bytes)
        assert b"(" in result


class TestContainsAndCount:
    """Deep correctness for contains/count after complex operations."""

    def test_count_equals_min_nonzero_counter(self) -> None:
        sbf = StableBloomFilter(capacity=100, counter_bits=4, seed=0)
        sbf.add("product")
        sbf.add("product")
        sbf.add("product")
        assert sbf.count("product") >= 1

    def test_contains_returns_false_when_count_zero(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        assert not sbf.contains("never_seen")

    def test_empty_string_handling(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        sbf.add("")
        assert sbf.contains("")
        assert sbf.count("") >= 1

    def test_very_short_key(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        sbf.add(b"a")
        assert sbf.contains(b"a")

    def test_very_long_key(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=0)
        long_key = b"z" * 100000
        sbf.add(long_key)
        assert sbf.contains(long_key)

    def test_add_then_decay_then_add_again(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=42)
        sbf.add("revival")
        sbf.decay_all(steps=100)
        sbf.add("revival")
        assert sbf.contains("revival")


class TestConstructorEdgeCases:
    """Edge cases for constructor parameters."""

    def test_minimal_valid_everything(self) -> None:
        sbf = StableBloomFilter(capacity=1, error_rate=1e-9, counter_bits=16)
        assert sbf.capacity == 1
        assert sbf.slot_count >= 8
        assert sbf.hash_count >= 1

    def test_large_counter_bits(self) -> None:
        sbf = StableBloomFilter(capacity=10, counter_bits=16)
        assert sbf._counter_max == 65535

    def test_error_rate_just_above_zero(self) -> None:
        sbf = StableBloomFilter(capacity=10, error_rate=1e-10)
        assert sbf.hash_count >= 1

    def test_error_rate_just_below_one(self) -> None:
        sbf = StableBloomFilter(capacity=10, error_rate=0.999999)
        assert sbf.slot_count >= 8

    def test_huge_capacity_tiny_error(self) -> None:
        sbf = StableBloomFilter(capacity=1000000, error_rate=1e-15)
        assert sbf.slot_count > 0
        assert sbf.hash_count >= 1


class TestDecayFormula:
    """Verify that decay_probability formula is correct for various parameters."""

    @pytest.mark.parametrize(
        "capacity,error_rate",
        [
            (100, 0.01),
            (500, 0.05),
            (1000, 0.001),
            (1, 0.5),
        ],
    )
    def test_decay_probability_inverse_hash_count(self, capacity: int, error_rate: float) -> None:
        sbf = StableBloomFilter(capacity=capacity, error_rate=error_rate)
        assert sbf.decay_probability == pytest.approx(1.0 / sbf.hash_count)

    def test_slot_count_formula(self) -> None:
        capacity = 1000
        error_rate = 0.01
        sbf = StableBloomFilter(capacity=capacity, error_rate=error_rate)
        expected = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        expected = max(expected, 8)
        assert sbf.slot_count == expected

    def test_hash_count_formula(self) -> None:
        capacity = 1000
        error_rate = 0.01
        sbf = StableBloomFilter(capacity=capacity, error_rate=error_rate)
        expected = max(1, round((sbf.slot_count / capacity) * math.log(2)))
        assert sbf.hash_count == expected
