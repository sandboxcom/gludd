"""Unit tests for CountingBloomFilter."""

from __future__ import annotations

import struct

import pytest

from general_ludd.probabilistic.counting_bloom import CountingBloomFilter


class TestConstruction:
    def test_default_construction(self) -> None:
        cbf = CountingBloomFilter(100)
        assert cbf.capacity == 100
        assert cbf.error_rate == 0.01
        assert cbf.counter_bits == 4
        assert cbf.hash_count >= 1
        assert cbf.slot_count >= 8

    def test_custom_params(self) -> None:
        cbf = CountingBloomFilter(200, error_rate=0.001, counter_bits=8)
        assert cbf.capacity == 200
        assert cbf.error_rate == 0.001
        assert cbf.counter_bits == 8
        assert cbf.hash_count >= 1

    def test_capacity_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            CountingBloomFilter(0)

    def test_capacity_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            CountingBloomFilter(-1)

    def test_error_rate_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="error_rate"):
            CountingBloomFilter(100, error_rate=0.0)

    def test_error_rate_one_raises(self) -> None:
        with pytest.raises(ValueError, match="error_rate"):
            CountingBloomFilter(100, error_rate=1.0)

    def test_error_rate_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="error_rate"):
            CountingBloomFilter(100, error_rate=-0.5)

    def test_counter_bits_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="counter_bits"):
            CountingBloomFilter(100, counter_bits=0)

    def test_counter_bits_too_high_raises(self) -> None:
        with pytest.raises(ValueError, match="counter_bits"):
            CountingBloomFilter(100, counter_bits=17)

    def test_counter_bits_boundary_valid(self) -> None:
        cbf1 = CountingBloomFilter(100, counter_bits=1)
        assert cbf1.counter_bits == 1
        cbf2 = CountingBloomFilter(100, counter_bits=16)
        assert cbf2.counter_bits == 16


class TestAddContains:
    def test_add_contains_single_string(self) -> None:
        cbf = CountingBloomFilter(100)
        assert not cbf.contains("hello")
        cbf.add("hello")
        assert cbf.contains("hello")

    def test_add_contains_single_int(self) -> None:
        cbf = CountingBloomFilter(100)
        cbf.add(42)
        assert cbf.contains(42)

    def test_add_contains_bytes(self) -> None:
        cbf = CountingBloomFilter(100)
        cbf.add(b"raw")
        assert cbf.contains(b"raw")

    def test_add_contains_float(self) -> None:
        cbf = CountingBloomFilter(100)
        cbf.add(3.14)
        assert cbf.contains(3.14)

    def test_not_contains_absent(self) -> None:
        cbf = CountingBloomFilter(100)
        cbf.add("present")
        assert not cbf.contains("absent")

    def test_add_multiple(self) -> None:
        cbf = CountingBloomFilter(100)
        items = ["a", "b", "c", "d", "e"]
        for it in items:
            cbf.add(it)
        for it in items:
            assert cbf.contains(it)

    def test_add_same_item_multiple_times(self) -> None:
        cbf = CountingBloomFilter(100)
        cbf.add("dup")
        cbf.add("dup")
        cbf.add("dup")
        assert cbf.contains("dup")

    def test_add_custom_object_uses_str(self) -> None:
        cbf = CountingBloomFilter(100)

        class Custom:
            def __str__(self) -> str:
                return "custom_obj"

        cbf.add(Custom())
        assert cbf.contains(Custom())
        assert cbf.contains("custom_obj")


class TestCount:
    def test_count_zero_for_absent(self) -> None:
        cbf = CountingBloomFilter(100)
        assert cbf.count("nonexistent") == 0

    def test_count_one_after_single_add(self) -> None:
        cbf = CountingBloomFilter(1000)
        cbf.add("item")
        assert cbf.count("item") == 1

    def test_count_increments(self) -> None:
        cbf = CountingBloomFilter(1000)
        cbf.add("item")
        cbf.add("item")
        cbf.add("item")
        assert cbf.count("item") == 3

    def test_count_has_lower_bound_zero(self) -> None:
        cbf = CountingBloomFilter(100)
        cbf.add("item")
        cbf.add("item")
        cbf.remove("item")
        cbf.remove("item")
        assert cbf.count("item") >= 0


class TestRemove:
    def test_remove_decrements(self) -> None:
        cbf = CountingBloomFilter(1000)
        cbf.add("item")
        cbf.add("item")
        assert cbf.count("item") == 2
        cbf.remove("item")
        assert cbf.count("item") == 1

    def test_remove_to_zero(self) -> None:
        cbf = CountingBloomFilter(100)
        cbf.add("item")
        cbf.remove("item")
        assert cbf.count("item") == 0
        assert not cbf.contains("item")

    def test_remove_never_goes_below_zero(self) -> None:
        cbf = CountingBloomFilter(100)
        cbf.add("item")
        cbf.remove("item")
        cbf.remove("item")
        cbf.remove("item")
        assert cbf.count("item") >= 0

    def test_remove_absent_item_no_op(self) -> None:
        cbf = CountingBloomFilter(100)
        cbf.remove("absent")
        assert cbf.count("absent") == 0


class TestCounterSaturation:
    def test_counter_clamps_at_max(self) -> None:
        cbf = CountingBloomFilter(10, counter_bits=4)
        assert cbf._counter_max == 15
        for _ in range(20):
            cbf.add("item")
        assert cbf.count("item") <= 15

    def test_one_bit_counter_rolls_properly(self) -> None:
        cbf = CountingBloomFilter(100, counter_bits=1)
        cbf.add("item")
        cbf.add("item")
        assert cbf.count("item") == 1


class TestMerge:
    def test_merge_adds_counters(self) -> None:
        cbf1 = CountingBloomFilter(100, error_rate=0.01, counter_bits=4)
        cbf2 = CountingBloomFilter(100, error_rate=0.01, counter_bits=4)
        cbf1.add("shared")
        cbf2.add("shared")
        cbf2.add("only_two")
        assert cbf1.count("shared") == 1
        assert cbf1.count("only_two") == 0
        cbf1.merge(cbf2)
        assert cbf1.count("shared") == 2
        assert cbf1.count("only_two") == 1

    def test_merge_clamps_at_max(self) -> None:
        cbf1 = CountingBloomFilter(10, counter_bits=4)
        cbf2 = CountingBloomFilter(10, counter_bits=4)
        for _ in range(20):
            cbf1.add("item")
        cbf2.add("item")
        cbf1.merge(cbf2)
        assert cbf1.count("item") <= 15

    def test_merge_raises_on_capacity_mismatch(self) -> None:
        cbf1 = CountingBloomFilter(100)
        cbf2 = CountingBloomFilter(200)
        with pytest.raises(ValueError, match="merge"):
            cbf1.merge(cbf2)

    def test_merge_raises_on_counter_bits_mismatch(self) -> None:
        cbf1 = CountingBloomFilter(100, counter_bits=4)
        cbf2 = CountingBloomFilter(100, counter_bits=8)
        with pytest.raises(ValueError, match="merge"):
            cbf1.merge(cbf2)


class TestEstimatedCount:
    def test_empty_filter_returns_zero(self) -> None:
        cbf = CountingBloomFilter(100)
        assert cbf.estimated_count() == pytest.approx(0.0)

    def test_one_item_estimated_count(self) -> None:
        cbf = CountingBloomFilter(1000)
        cbf.add("item")
        est = cbf.estimated_count()
        assert est > 0

    def test_multiple_items_estimated_count(self) -> None:
        cbf = CountingBloomFilter(1000)
        for i in range(50):
            cbf.add(f"item_{i}")
        est = cbf.estimated_count()
        assert est > 0

    def test_estimated_count_grows_with_inserts(self) -> None:
        cbf = CountingBloomFilter(1000)
        est0 = cbf.estimated_count()
        for i in range(100):
            cbf.add(f"item_{i}")
        est1 = cbf.estimated_count()
        assert est1 > est0


class TestSerialization:
    def test_roundtrip_empty(self) -> None:
        cbf = CountingBloomFilter(100)
        raw = cbf.to_bytes()
        restored = CountingBloomFilter.from_bytes(raw)
        assert restored.capacity == cbf.capacity
        assert restored.error_rate == cbf.error_rate
        assert restored.counter_bits == cbf.counter_bits
        assert restored.hash_count == cbf.hash_count
        assert restored.slot_count == cbf.slot_count

    def test_roundtrip_with_data(self) -> None:
        cbf = CountingBloomFilter(1000)
        cbf.add("hello")
        cbf.add("world")
        cbf.add(42)
        raw = cbf.to_bytes()
        restored = CountingBloomFilter.from_bytes(raw)
        assert restored.contains("hello")
        assert restored.contains("world")
        assert restored.contains(42)
        assert restored.count("hello") == cbf.count("hello")
        assert restored.count("world") == cbf.count("world")
        assert not restored.contains("absent")

    def test_roundtrip_preserves_counter_saturation(self) -> None:
        cbf = CountingBloomFilter(10, counter_bits=4)
        for _ in range(20):
            cbf.add("item")
        raw = cbf.to_bytes()
        restored = CountingBloomFilter.from_bytes(raw)
        assert restored.count("item") == cbf.count("item")

    def test_from_bytes_rejects_truncated(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            CountingBloomFilter.from_bytes(b"short")

    def test_from_bytes_rejects_mismatched_counters(self) -> None:
        header = struct.pack("!IIdII", 100, 64, 0.01, 5, 4)
        bad_counters = b"\x00" * 10
        with pytest.raises(ValueError, match="length mismatch"):
            CountingBloomFilter.from_bytes(header + bad_counters)


class TestInternalHelpers:
    def test_get_set_counter_roundtrip(self) -> None:
        cbf = CountingBloomFilter(100, counter_bits=4)
        for idx in range(min(cbf.slot_count, 10)):
            cbf._set_counter(idx, 7)
            assert cbf._get_counter(idx) == 7
            cbf._set_counter(idx, 0)
            assert cbf._get_counter(idx) == 0

    def test_get_set_counter_max(self) -> None:
        cbf = CountingBloomFilter(100, counter_bits=4)
        cbf._set_counter(0, cbf._counter_max)
        assert cbf._get_counter(0) == cbf._counter_max

    def test_item_to_bytes_str(self) -> None:
        result = CountingBloomFilter._item_to_bytes("hello")
        assert result == b"hello"

    def test_item_to_bytes_bytes(self) -> None:
        result = CountingBloomFilter._item_to_bytes(b"raw")
        assert result == b"raw"

    def test_item_to_bytes_int(self) -> None:
        result = CountingBloomFilter._item_to_bytes(42)
        assert result == b"42"

    def test_item_to_bytes_float(self) -> None:
        result = CountingBloomFilter._item_to_bytes(3.14)
        assert result == b"3.14"

    def test_item_to_bytes_custom(self) -> None:
        class Obj:
            def __str__(self) -> str:
                return "objstr"

        result = CountingBloomFilter._item_to_bytes(Obj())
        assert result == b"objstr"

    def test_hash_deterministic(self) -> None:
        h1 = CountingBloomFilter._hash(b"key", 0)
        h2 = CountingBloomFilter._hash(b"key", 0)
        assert h1 == h2

    def test_hash_different_keys(self) -> None:
        h1 = CountingBloomFilter._hash(b"key_a", 0)
        h2 = CountingBloomFilter._hash(b"key_b", 0)
        assert h1 != h2

    def test_hash_different_seeds(self) -> None:
        h1 = CountingBloomFilter._hash(b"key", 0)
        h2 = CountingBloomFilter._hash(b"key", 1)
        assert h1 != h2

    def test_fnv1a_deterministic(self) -> None:
        h1 = CountingBloomFilter._fnv1a(b"data")
        h2 = CountingBloomFilter._fnv1a(b"data")
        assert h1 == h2

    def test_fnv1a_different_data(self) -> None:
        h1 = CountingBloomFilter._fnv1a(b"a")
        h2 = CountingBloomFilter._fnv1a(b"b")
        assert h1 != h2


class TestProperties:
    def test_capacity_property(self) -> None:
        cbf = CountingBloomFilter(500)
        assert cbf.capacity == 500

    def test_error_rate_property(self) -> None:
        cbf = CountingBloomFilter(100, error_rate=0.05)
        assert cbf.error_rate == 0.05

    def test_counter_bits_property(self) -> None:
        cbf = CountingBloomFilter(100, counter_bits=8)
        assert cbf.counter_bits == 8

    def test_hash_count_property(self) -> None:
        cbf = CountingBloomFilter(100)
        assert isinstance(cbf.hash_count, int)
        assert cbf.hash_count >= 1

    def test_slot_count_property(self) -> None:
        cbf = CountingBloomFilter(100)
        assert isinstance(cbf.slot_count, int)
        assert cbf.slot_count >= 8


class TestFalsePositives:
    def test_no_false_positives_small_set(self) -> None:
        cbf = CountingBloomFilter(1000, error_rate=0.001)
        present = [f"item_{i}" for i in range(50)]
        for it in present:
            cbf.add(it)
        absent = [f"absent_{i}" for i in range(100)]
        fp = sum(1 for it in absent if cbf.contains(it))
        assert fp <= 5

    def test_remove_eliminates_item(self) -> None:
        cbf = CountingBloomFilter(100)
        cbf.add("temp")
        cbf.remove("temp")
        assert not cbf.contains("temp")
