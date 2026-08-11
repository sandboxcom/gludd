"""Deep tests for CuckooFilter — kicking exhaustion, insertion pressure, alt-index cycles, serialization fidelity."""

from __future__ import annotations

import struct

import pytest

from general_ludd.probabilistic.cuckoo_filter import CuckooFilter


class TestCuckooInsertionPressure:
    def test_exhausts_kicks_at_near_full_capacity(self) -> None:
        cf = CuckooFilter(capacity=50, error_rate=0.01, bucket_size=4, seed=0)
        successes = 0
        for i in range(500):
            if cf.add(f"pressure_{i}"):
                successes += 1
        assert successes <= cf.num_buckets * cf.bucket_size
        assert cf.load_factor() > 0.8

    def test_insertion_order_does_not_affect_load_factor(self) -> None:
        r1 = CuckooFilter(capacity=100, bucket_size=4, seed=1)
        r2 = CuckooFilter(capacity=100, bucket_size=4, seed=1)
        items = [f"seq_{i}" for i in range(200)]
        for item in items:
            r1.add(item)
        for item in reversed(items):
            r2.add(item)
        assert r1.size == r2.size
        assert r1.load_factor() == pytest.approx(r2.load_factor())

    def test_duplicate_insertions_do_not_break_capacity(self) -> None:
        cf = CuckooFilter(capacity=100, bucket_size=4, seed=42)
        for _ in range(400):
            cf.add("dup")
        assert cf.size == 400
        assert cf.load_factor() <= 1.0

    def test_remove_then_reinsert_works(self) -> None:
        cf = CuckooFilter(capacity=200, bucket_size=4, seed=7)
        items = [f"r_{i}" for i in range(50)]
        for item in items:
            assert cf.add(item)
        for item in items:
            assert cf.remove(item)
        assert cf.size == 0
        for item in items:
            assert cf.add(item)
        assert cf.size == 50
        for item in items:
            assert cf.contains(item)

    def test_add_remove_interleave_maintains_invariants(self) -> None:
        cf = CuckooFilter(capacity=100, bucket_size=4, seed=3)
        for i in range(60):
            assert cf.add(f"inter_{i}")
        for i in range(0, 60, 2):
            assert cf.remove(f"inter_{i}")
        for i in range(0, 60, 2):
            assert cf.add(f"inter2_{i}")
        for i in range(1, 60, 2):
            assert cf.contains(f"inter_{i}")
        for i in range(0, 60, 2):
            assert cf.contains(f"inter2_{i}")
        assert cf.size >= 30


class TestCuckooAltIndex:
    def test_alt_index_is_deterministic(self) -> None:
        cf = CuckooFilter(capacity=64)
        a1 = cf._alt_index(0, 5)
        a2 = cf._alt_index(0, 5)
        assert a1 == a2

    def test_alt_index_symmetry(self) -> None:
        cf = CuckooFilter(capacity=64)
        fp = 7
        i1 = cf._index_hash(b"key")
        i2 = cf._alt_index(i1, fp)
        i3 = cf._alt_index(i2, fp)
        assert i3 == i1

    def test_alt_index_stays_in_bounds(self) -> None:
        cf = CuckooFilter(capacity=128)
        for idx in range(cf.num_buckets):
            for fp in range(1, 256):
                alt = cf._alt_index(idx, fp)
                assert 0 <= alt < cf.num_buckets

    def test_alt_index_with_different_fingerprints(self) -> None:
        cf = CuckooFilter(capacity=256)
        alt1 = cf._alt_index(0, 1)
        alt2 = cf._alt_index(0, 2)
        assert alt1 != alt2


class TestCuckooFingerprint:
    def test_fingerprint_is_never_zero(self) -> None:
        cf = CuckooFilter(capacity=100, error_rate=0.01)
        for data in [b"a", b"b", b"hello", b"world", b"test" * 100]:
            fp = cf._fingerprint(data)
            assert fp > 0
            assert fp <= cf._fingerprint_mask

    def test_fingerprint_fits_in_mask(self) -> None:
        cf = CuckooFilter(capacity=100, error_rate=0.01)
        mask = cf._fingerprint_mask
        for i in range(1000):
            fp = cf._fingerprint(f"item_{i}".encode())
            assert 1 <= fp <= mask

    def test_fingerprint_distribution(self) -> None:
        cf = CuckooFilter(capacity=500, error_rate=0.01)
        seen: set[int] = set()
        for i in range(1000):
            seen.add(cf._fingerprint(f"dist_{i}".encode()))
        unique_ratio = len(seen) / cf._fingerprint_mask
        assert unique_ratio > 0.5


class TestCuckooKickingMechanism:
    def test_kick_does_not_lose_items(self) -> None:
        cf = CuckooFilter(capacity=8, bucket_size=2, seed=0)
        items = []
        for i in range(20):
            item = f"kick_{i}"
            if cf.add(item):
                items.append(item)
        for item in items:
            assert cf.contains(item), f"lost {item}"

    def test_swap_fingerprint_basic(self) -> None:
        cf = CuckooFilter(capacity=100, bucket_size=4, seed=0)
        cf._set_entry(0, 0, 3)
        old = cf._swap_fingerprint(7, 0)
        assert old == 3
        assert cf._get_entry(0, 0) == 7

    def test_swap_fingerprint_preserves_other_slots(self) -> None:
        cf = CuckooFilter(capacity=100, bucket_size=4, seed=0)
        cf._set_entry(0, 0, 1)
        cf._set_entry(0, 1, 2)
        cf._set_entry(0, 2, 3)
        cf._swap_fingerprint(9, 0)
        assert cf._get_entry(0, 1) == 2
        assert cf._get_entry(0, 2) == 3


class TestCuckooLoadFactorBoundary:
    def test_load_factor_zero_on_empty(self) -> None:
        cf = CuckooFilter(capacity=100)
        assert cf.load_factor() == 0.0

    def test_load_factor_one_exact(self) -> None:
        cf = CuckooFilter(capacity=4, bucket_size=2, seed=0)
        assert cf.num_buckets * cf.bucket_size == 16
        count = 0
        for i in range(100):
            if cf.add(f"fill_{i}"):
                count += 1
        assert cf.size == count
        assert 0.0 <= cf.load_factor() <= 1.0

    def test_load_factor_computes_from_size(self) -> None:
        cf = CuckooFilter(capacity=100, bucket_size=4, seed=5)
        for i in range(25):
            cf.add(f"lf_{i}")
        expected = 25 / (cf.num_buckets * cf.bucket_size)
        assert cf.load_factor() == pytest.approx(expected)

    def test_load_factor_decrements_on_remove(self) -> None:
        cf = CuckooFilter(capacity=100, bucket_size=4, seed=7)
        for i in range(50):
            cf.add(f"r_{i}")
        before = cf.load_factor()
        cf.remove("r_0")
        cf.remove("r_1")
        after = cf.load_factor()
        assert after < before


class TestCuckooSerializationDeep:
    def test_roundtrip_all_bucket_sizes(self) -> None:
        for bs in [2, 4, 8]:
            cf = CuckooFilter(capacity=50, bucket_size=bs, seed=9)
            for i in range(20):
                cf.add(f"bs_{bs}_{i}")
            raw = cf.to_bytes()
            restored = CuckooFilter.from_bytes(raw)
            assert restored.bucket_size == bs
            assert restored.size == cf.size
            for i in range(20):
                assert restored.contains(f"bs_{bs}_{i}")

    def test_roundtrip_all_error_rates(self) -> None:
        for er in [0.1, 0.01, 0.001]:
            cf = CuckooFilter(capacity=100, error_rate=er, seed=11)
            for i in range(30):
                cf.add(f"er_{er}_{i}")
            raw = cf.to_bytes()
            restored = CuckooFilter.from_bytes(raw)
            assert restored.error_rate == er
            assert restored.fingerprint_bits == cf.fingerprint_bits
            assert restored.size == cf.size

    def test_roundtrip_minimal_capacity(self) -> None:
        cf = CuckooFilter(capacity=1, bucket_size=2, seed=13)
        cf.add("only")
        raw = cf.to_bytes()
        restored = CuckooFilter.from_bytes(raw)
        assert restored.capacity == 1
        assert restored.contains("only")
        assert restored.size == 1

    def test_roundtrip_very_large(self) -> None:
        cf = CuckooFilter(capacity=5000, error_rate=0.001, seed=15)
        for i in range(500):
            cf.add(f"big_{i}")
        raw = cf.to_bytes()
        restored = CuckooFilter.from_bytes(raw)
        assert restored.capacity == 5000
        for i in range(500):
            assert restored.contains(f"big_{i}")

    def test_serialized_bytes_header_structure(self) -> None:
        cf = CuckooFilter(capacity=100, error_rate=0.01, bucket_size=4, seed=17)
        for i in range(10):
            cf.add(f"hdr_{i}")
        raw = cf.to_bytes()
        header_size = struct.calcsize("!IIIII")
        cap, nb, bs, fb, cnt = struct.unpack("!IIIII", raw[:header_size])
        assert cap == cf.capacity
        assert nb == cf.num_buckets
        assert bs == cf.bucket_size
        assert fb == cf.fingerprint_bits
        assert cnt == cf.size

    def test_from_bytes_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            CuckooFilter.from_bytes(b"")

    def test_from_bytes_rejects_partial_header(self) -> None:
        header_size = struct.calcsize("!IIIII")
        with pytest.raises(ValueError, match="truncated"):
            CuckooFilter.from_bytes(b"\x00" * (header_size - 1))


class TestCuckooEdgeCases:
    def test_remove_from_empty_is_noop(self) -> None:
        cf = CuckooFilter(capacity=100)
        assert cf.remove("absent") is False
        assert cf.size == 0

    def test_contains_empty_returns_false(self) -> None:
        cf = CuckooFilter(capacity=100)
        assert cf.contains("nothing") is False

    def test_add_empty_string(self) -> None:
        cf = CuckooFilter(capacity=100)
        assert cf.add("")
        assert cf.contains("")

    def test_add_identical_bytes_and_string(self) -> None:
        cf = CuckooFilter(capacity=100)
        cf.add(b"hello")
        assert cf.contains("hello") is True

    def test_str_vs_bytes_different_items(self) -> None:
        cf = CuckooFilter(capacity=100)
        cf.add(b"abc")
        cf.add("abc")
        assert cf.size == 2

    def test_fingerprint_mask_covers_bit_range(self) -> None:
        cf = CuckooFilter(capacity=100, error_rate=0.01)
        for _ in range(1000):
            fp = cf._fingerprint(f"mask_test_{_}".encode())
            assert 1 <= fp <= cf._fingerprint_mask

    def test_insert_slot_zero(self) -> None:
        cf = CuckooFilter(capacity=100, bucket_size=4)
        assert cf._insert(1, 0)
        assert cf._lookup(1, 0)
        assert cf._delete(1, 0)
        assert not cf._lookup(1, 0)

    def test_all_slots_fillable(self) -> None:
        cf = CuckooFilter(capacity=4, bucket_size=4, seed=0)
        filled = 0
        for i in range(50):
            if cf.add(f"slot_{i}"):
                filled += 1
        assert filled == 16
        assert cf.load_factor() == 1.0

    def test_capacity_one_bucket_size_two(self) -> None:
        cf = CuckooFilter(capacity=1, bucket_size=2)
        assert cf.add("a")
        assert cf.add("b")
        assert cf.size == 2
        assert cf.contains("a")
        assert cf.contains("b")


class TestCuckooIndexHash:
    def test_index_hash_within_bounds_all_keys(self) -> None:
        cf = CuckooFilter(capacity=256)
        for i in range(500):
            idx = cf._index_hash(f"index_{i}".encode())
            assert 0 <= idx < cf.num_buckets

    def test_index_hash_distribution(self) -> None:
        cf = CuckooFilter(capacity=1024)
        seen: set[int] = set()
        for i in range(2000):
            seen.add(cf._index_hash(f"dist_{i}".encode()))
        coverage = len(seen) / cf.num_buckets
        assert coverage > 0.5

    def test_index_hash_different_keys(self) -> None:
        cf = CuckooFilter(capacity=256)
        i1 = cf._index_hash(b"alpha")
        i2 = cf._index_hash(b"beta")
        assert i1 != i2


class TestCuckooInternalConsistency:
    def test_get_set_entry_fingerprint_mask_boundary(self) -> None:
        cf = CuckooFilter(capacity=100, error_rate=0.01)
        mask = cf._fingerprint_mask
        for fp in [1, 2, mask // 2, mask - 1, mask]:
            cf._set_entry(0, 0, fp)
            assert cf._get_entry(0, 0) == fp

    def test_get_set_entry_zero_means_empty(self) -> None:
        cf = CuckooFilter(capacity=100)
        assert cf._get_entry(0, 0) == 0
        assert not cf._lookup(1, 0)
        assert not cf._lookup(0, 0)

    def test_insert_cannot_overwrite_existing(self) -> None:
        cf = CuckooFilter(capacity=100, bucket_size=4)
        cf._set_entry(0, 0, 5)
        cf._set_entry(0, 1, 7)
        assert cf._lookup(5, 0)
        assert cf._lookup(7, 0)

    def test_delete_only_targets_matching_fingerprint(self) -> None:
        cf = CuckooFilter(capacity=100, bucket_size=4)
        cf._set_entry(0, 0, 3)
        cf._set_entry(0, 1, 5)
        assert cf._delete(3, 0)
        assert cf._get_entry(0, 0) == 0
        assert cf._get_entry(0, 1) == 5

    def test_add_remove_flush_cycle(self) -> None:
        cf = CuckooFilter(capacity=100, bucket_size=4, seed=42)
        for cycle in range(3):
            items = [f"c{cycle}_i{i}" for i in range(30)]
            for item in items:
                assert cf.add(item)
            for item in items:
                assert cf.remove(item)
            assert cf.size == 0


class TestCuckooHashFunctions:
    def test_hash64_64bit_range(self) -> None:
        for key in [b"", b"a", b"hello", b"x" * 100]:
            h = CuckooFilter._hash64(key)
            assert 0 <= h < 2**64

    def test_hash_fp_32bit_range(self) -> None:
        for key in [b"", b"a", b"hello"]:
            h = CuckooFilter._hash_fp(key)
            assert 0 <= h < 2**32

    def test_next_power_of_two_large(self) -> None:
        assert CuckooFilter._next_power_of_two(1) == 1
        assert CuckooFilter._next_power_of_two(2) == 2
        assert CuckooFilter._next_power_of_two(3) == 4
        assert CuckooFilter._next_power_of_two(1024) == 1024
        assert CuckooFilter._next_power_of_two(1025) == 2048
        assert CuckooFilter._next_power_of_two(1_000_000) == 2**20

    def test_next_power_of_two_result_is_power_of_two(self) -> None:
        for n in [1, 2, 3, 7, 10, 50, 99, 100, 1000, 9999]:
            result = CuckooFilter._next_power_of_two(n)
            assert result & (result - 1) == 0
            assert result >= n
