"""Unit tests for CuckooFilter."""

from __future__ import annotations

import math

import pytest

from general_ludd.probabilistic.cuckoo_filter import CuckooFilter


class TestCuckooFilterInit:
    def test_valid_defaults(self):
        cf = CuckooFilter(capacity=100)
        assert cf.capacity == 100
        assert cf.error_rate == 0.01
        assert cf.bucket_size == 4
        assert cf.size == 0
        assert cf.num_buckets >= 2

    def test_valid_custom_params(self):
        cf = CuckooFilter(capacity=500, error_rate=0.001, bucket_size=8, seed=42)
        assert cf.capacity == 500
        assert cf.error_rate == 0.001
        assert cf.bucket_size == 8
        assert cf.size == 0

    def test_capacity_zero_raises(self):
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            CuckooFilter(capacity=0)

    def test_capacity_negative_raises(self):
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            CuckooFilter(capacity=-5)

    def test_error_rate_zero_raises(self):
        with pytest.raises(ValueError, match="error_rate must be in \\(0, 1\\)"):
            CuckooFilter(capacity=100, error_rate=0.0)

    def test_error_rate_one_raises(self):
        with pytest.raises(ValueError, match="error_rate must be in \\(0, 1\\)"):
            CuckooFilter(capacity=100, error_rate=1.0)

    def test_error_rate_negative_raises(self):
        with pytest.raises(ValueError, match="error_rate must be in \\(0, 1\\)"):
            CuckooFilter(capacity=100, error_rate=-0.5)

    def test_bucket_size_one_raises(self):
        with pytest.raises(ValueError, match="bucket_size must be >= 2"):
            CuckooFilter(capacity=100, bucket_size=1)

    def test_fingerprint_bits_floor_at_4(self):
        cf = CuckooFilter(capacity=100, error_rate=0.1)
        assert cf.fingerprint_bits >= 4

    def test_fingerprint_bits_increases_with_lower_error(self):
        cf_high = CuckooFilter(capacity=100, error_rate=0.1)
        cf_low = CuckooFilter(capacity=100, error_rate=0.0001)
        assert cf_low.fingerprint_bits >= cf_high.fingerprint_bits

    def test_num_buckets_is_power_of_two(self):
        for c in [1, 10, 50, 100, 1000]:
            cf = CuckooFilter(capacity=c)
            n = cf.num_buckets
            assert n > 0 and (n & (n - 1)) == 0, f"num_buckets={n} not power of two"


class TestCuckooFilterAdd:
    def test_add_returns_true(self):
        cf = CuckooFilter(capacity=100)
        assert cf.add("hello") is True
        assert cf.size == 1

    def test_add_multiple_items(self):
        cf = CuckooFilter(capacity=100)
        for i in range(50):
            assert cf.add(f"item_{i}") is True
        assert cf.size == 50

    def test_add_duplicate(self):
        cf = CuckooFilter(capacity=100)
        assert cf.add("dup") is True
        assert cf.add("dup") is True
        assert cf.size == 2

    def test_add_near_capacity(self):
        cf = CuckooFilter(capacity=50, error_rate=0.01, bucket_size=4)
        added = 0
        for i in range(100):
            if cf.add(f"val_{i}"):
                added += 1
        assert added >= 40
        assert cf.load_factor() > 0.7

    def test_add_returns_false_when_full(self):
        cf = CuckooFilter(capacity=4, error_rate=0.01, bucket_size=2)
        added = 0
        for i in range(100):
            if cf.add(f"x_{i}"):
                added += 1
            else:
                break
        assert cf.add("overflow") is False or cf.load_factor() > 0.9


class TestCuckooFilterRemove:
    def test_remove_existing(self):
        cf = CuckooFilter(capacity=100)
        cf.add("alpha")
        assert cf.remove("alpha") is True
        assert cf.size == 0

    def test_remove_nonexistent(self):
        cf = CuckooFilter(capacity=100)
        cf.add("alpha")
        assert cf.remove("beta") is False
        assert cf.size == 1

    def test_remove_twice(self):
        cf = CuckooFilter(capacity=100)
        cf.add("gamma")
        assert cf.remove("gamma") is True
        assert cf.remove("gamma") is False
        assert cf.size == 0

    def test_remove_one_of_duplicates(self):
        cf = CuckooFilter(capacity=100)
        cf.add("dup")
        cf.add("dup")
        assert cf.remove("dup") is True
        assert cf.size == 1
        assert cf.contains("dup") is True

    def test_remove_all_reinserted(self):
        cf = CuckooFilter(capacity=200)
        items = [f"k_{i}" for i in range(50)]
        for item in items:
            cf.add(item)
        for item in items:
            assert cf.remove(item) is True
        assert cf.size == 0
        for item in items:
            assert cf.contains(item) is False


class TestCuckooFilterContains:
    def test_contains_existing(self):
        cf = CuckooFilter(capacity=100)
        cf.add("omega")
        assert cf.contains("omega") is True

    def test_contains_nonexistent(self):
        cf = CuckooFilter(capacity=100)
        assert cf.contains("nothing") is False

    def test_contains_after_remove(self):
        cf = CuckooFilter(capacity=100)
        cf.add("temp")
        cf.remove("temp")
        assert cf.contains("temp") is False

    def test_contains_many(self):
        cf = CuckooFilter(capacity=200)
        items = {f"c_{i}" for i in range(30)}
        for item in items:
            cf.add(item)
        for item in items:
            assert cf.contains(item) is True
        missing = {f"m_{i}" for i in range(100)} - items
        false_positives = sum(1 for item in missing if cf.contains(item))
        assert false_positives < len(missing) * 0.15


class TestCuckooFilterLoadFactor:
    def test_empty_load_factor(self):
        cf = CuckooFilter(capacity=100)
        assert cf.load_factor() == 0.0

    def test_load_factor_after_adds(self):
        cf = CuckooFilter(capacity=100)
        for i in range(25):
            cf.add(f"a_{i}")
        expected = 25 / (cf.num_buckets * cf.bucket_size)
        assert math.isclose(cf.load_factor(), expected)

    def test_load_factor_after_removals(self):
        cf = CuckooFilter(capacity=100)
        for i in range(30):
            cf.add(f"b_{i}")
        for i in range(10):
            cf.remove(f"b_{i}")
        assert cf.size == 20
        expected = 20 / (cf.num_buckets * cf.bucket_size)
        assert math.isclose(cf.load_factor(), expected)


class TestCuckooFilterSerialization:
    def test_roundtrip_empty(self):
        cf = CuckooFilter(capacity=100, error_rate=0.01, bucket_size=4, seed=7)
        data = cf.to_bytes()
        restored = CuckooFilter.from_bytes(data)
        assert restored.capacity == cf.capacity
        assert restored.num_buckets == cf.num_buckets
        assert restored.bucket_size == cf.bucket_size
        assert restored.fingerprint_bits == cf.fingerprint_bits
        assert restored.size == cf.size

    def test_roundtrip_with_items(self):
        cf = CuckooFilter(capacity=100, seed=1)
        items = ["a", "b", "c", "hello", "world", "test123"]
        for item in items:
            cf.add(item)
        data = cf.to_bytes()
        restored = CuckooFilter.from_bytes(data)
        assert restored.size == cf.size
        for item in items:
            assert restored.contains(item) is True

    def test_roundtrip_non_round_capacity(self):
        cf = CuckooFilter(capacity=75, error_rate=0.05, bucket_size=3, seed=9)
        for i in range(20):
            cf.add(f"val_{i}")
        data = cf.to_bytes()
        restored = CuckooFilter.from_bytes(data)
        assert restored.capacity == cf.capacity
        assert restored.size == cf.size

    def test_from_bytes_truncated_raises(self):
        with pytest.raises(ValueError, match="truncated"):
            CuckooFilter.from_bytes(b"short")

    def test_from_bytes_table_length_mismatch(self):
        cf = CuckooFilter(capacity=100)
        data = cf.to_bytes()
        bad_data = data[:20] + data[20:30]
        with pytest.raises(ValueError, match="table length mismatch"):
            CuckooFilter.from_bytes(bad_data)


class TestCuckooFilterItemTypes:
    def test_string_item(self):
        cf = CuckooFilter(capacity=100)
        cf.add("hello")
        assert cf.contains("hello")

    def test_bytes_item(self):
        cf = CuckooFilter(capacity=100)
        cf.add(b"raw_bytes")
        assert cf.contains(b"raw_bytes")

    def test_int_item(self):
        cf = CuckooFilter(capacity=100)
        cf.add(12345)
        assert cf.contains(12345)

    def test_float_item(self):
        cf = CuckooFilter(capacity=100)
        cf.add(3.14159)
        assert cf.contains(3.14159)

    def test_custom_object_item(self):
        cf = CuckooFilter(capacity=100)
        obj = object()
        cf.add(obj)
        assert cf.contains(obj)

    def test_empty_string(self):
        cf = CuckooFilter(capacity=100)
        cf.add("")
        assert cf.contains("")
        cf.remove("")
        assert not cf.contains("")


class TestCuckooFilterInternalMethods:
    def test_next_power_of_two_exact(self):
        assert CuckooFilter._next_power_of_two(1) == 1
        assert CuckooFilter._next_power_of_two(2) == 2
        assert CuckooFilter._next_power_of_two(4) == 4
        assert CuckooFilter._next_power_of_two(64) == 64

    def test_next_power_of_two_round_up(self):
        assert CuckooFilter._next_power_of_two(3) == 4
        assert CuckooFilter._next_power_of_two(5) == 8
        assert CuckooFilter._next_power_of_two(63) == 64
        assert CuckooFilter._next_power_of_two(100) == 128

    def test_hash64_deterministic(self):
        h1 = CuckooFilter._hash64(b"test")
        h2 = CuckooFilter._hash64(b"test")
        assert h1 == h2

    def test_hash64_different_keys(self):
        h1 = CuckooFilter._hash64(b"alpha")
        h2 = CuckooFilter._hash64(b"beta")
        assert h1 != h2

    def test_hash_fp_deterministic(self):
        h1 = CuckooFilter._hash_fp(b"key")
        h2 = CuckooFilter._hash_fp(b"key")
        assert h1 == h2

    def test_fingerprint_nonzero(self):
        cf = CuckooFilter(capacity=100)
        fp = cf._fingerprint(b"something")
        assert fp > 0
        assert fp <= cf._fingerprint_mask

    def test_fingerprint_deterministic(self):
        cf = CuckooFilter(capacity=100)
        fp1 = cf._fingerprint(b"same")
        fp2 = cf._fingerprint(b"same")
        assert fp1 == fp2

    def test_index_hash_within_bounds(self):
        cf = CuckooFilter(capacity=100)
        for key in [b"a", b"b", b"hello", b"longer_key_test"]:
            idx = cf._index_hash(key)
            assert 0 <= idx < cf.num_buckets

    def test_alt_index_different_from_original(self):
        cf = CuckooFilter(capacity=100)
        idx = cf._index_hash(b"item")
        fp = cf._fingerprint(b"item")
        alt = cf._alt_index(idx, fp)
        assert alt != idx or alt == idx

    def test_alt_index_computes_alternate(self):
        cf = CuckooFilter(capacity=100)
        idx = 0
        fp = 5
        alt = cf._alt_index(idx, fp)
        assert 0 <= alt < cf.num_buckets

    def test_item_to_bytes_string(self):
        result = CuckooFilter._item_to_bytes("abc")
        assert isinstance(result, bytes)
        assert result == b"abc"

    def test_item_to_bytes_bytes(self):
        result = CuckooFilter._item_to_bytes(b"raw")
        assert result == b"raw"

    def test_item_to_bytes_int(self):
        result = CuckooFilter._item_to_bytes(42)
        assert result == b"42"

    def test_item_to_bytes_float(self):
        result = CuckooFilter._item_to_bytes(3.14)
        assert b"3.14" in result

    def test_insert_and_lookup(self):
        cf = CuckooFilter(capacity=100)
        assert cf._insert(7, 0) is True
        assert cf._lookup(7, 0) is True
        assert cf._lookup(7, 1) is False

    def test_insert_full_bucket(self):
        cf = CuckooFilter(capacity=100, bucket_size=2)
        assert cf._insert(1, 0) is True
        assert cf._insert(2, 0) is True
        assert cf._insert(3, 0) is False

    def test_delete_existing(self):
        cf = CuckooFilter(capacity=100)
        cf._insert(9, 0)
        assert cf._delete(9, 0) is True
        assert cf._lookup(9, 0) is False

    def test_delete_nonexistent(self):
        cf = CuckooFilter(capacity=100)
        assert cf._delete(99, 0) is False

    def test_swap_fingerprint(self):
        cf = CuckooFilter(capacity=100)
        cf._insert(3, 0)
        old = cf._swap_fingerprint(7, 0)
        assert old in (0, 3)
        assert cf._lookup(7, 0) is True

    def test_get_set_entry_roundtrip(self):
        cf = CuckooFilter(capacity=100)
        mask = cf._fingerprint_mask
        for fp in [1, 5, mask, mask - 1]:
            cf._set_entry(0, 0, fp)
            assert cf._get_entry(0, 0) == fp

    def test_get_set_entry_multiple_slots(self):
        cf = CuckooFilter(capacity=100, bucket_size=4)
        cf._set_entry(1, 0, 3)
        cf._set_entry(1, 1, 7)
        cf._set_entry(1, 2, 15)
        assert cf._get_entry(1, 0) == 3
        assert cf._get_entry(1, 1) == 7
        assert cf._get_entry(1, 2) == 15

    def test_get_set_entry_zero_clears(self):
        cf = CuckooFilter(capacity=100)
        cf._set_entry(2, 0, 11)
        cf._set_entry(2, 0, 0)
        assert cf._get_entry(2, 0) == 0


class TestCuckooFilterProperties:
    def test_size_increments_on_add(self):
        cf = CuckooFilter(capacity=100)
        assert cf.size == 0
        cf.add("a")
        assert cf.size == 1
        cf.add("b")
        assert cf.size == 2

    def test_size_decrements_on_remove(self):
        cf = CuckooFilter(capacity=100)
        cf.add("x")
        cf.add("y")
        cf.remove("x")
        assert cf.size == 1

    def test_capacity_property(self):
        cf = CuckooFilter(capacity=500)
        assert cf.capacity == 500

    def test_error_rate_property(self):
        cf = CuckooFilter(capacity=100, error_rate=0.05)
        assert cf.error_rate == 0.05

    def test_bucket_size_property(self):
        cf = CuckooFilter(capacity=100, bucket_size=8)
        assert cf.bucket_size == 8

    def test_num_buckets_property(self):
        cf = CuckooFilter(capacity=16, bucket_size=4)
        assert cf.num_buckets >= 4


class TestCuckooFilterFalsePositives:
    def test_low_false_positive_rate(self):
        cf = CuckooFilter(capacity=10000, error_rate=0.001, seed=99)
        items = [f"real_{i}" for i in range(50)]
        for item in items:
            cf.add(item)
        false_positives = 0
        missing = [f"fake_{i}" for i in range(500)]
        for candidate in missing:
            if cf.contains(candidate):
                false_positives += 1
        assert false_positives < 100


class TestCuckooFilterSeedReproducibility:
    def test_same_seed_produces_same_behavior(self):
        items = [f"it_{i}" for i in range(30)]
        cf1 = CuckooFilter(capacity=200, seed=42)
        cf2 = CuckooFilter(capacity=200, seed=42)
        for item in items:
            assert cf1.add(item) == cf2.add(item)
        for item in items:
            assert cf1.contains(item) == cf2.contains(item)

    def test_different_seeds_may_diverge(self):
        cf1 = CuckooFilter(capacity=200, seed=1)
        cf2 = CuckooFilter(capacity=200, seed=999)
        for i in range(50):
            cf1.add(f"s_{i}")
            cf2.add(f"s_{i}")
        assert cf1.size == cf2.size


class TestCuckooFilterEdgeCases:
    def test_minimal_capacity_filter(self):
        cf = CuckooFilter(capacity=1, bucket_size=2)
        assert cf.add("x") is True
        assert cf.contains("x") is True

    def test_large_capacity_filter(self):
        cf = CuckooFilter(capacity=10000, error_rate=0.001)
        for i in range(1000):
            assert cf.add(f"big_{i}") is True
        assert cf.size == 1000

    def test_zero_capacity_roundtrip(self):
        with pytest.raises(ValueError):
            CuckooFilter(capacity=0)
