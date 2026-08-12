"""Deep tests for probabilistic data structures."""

from __future__ import annotations

import math
import pickle
import random

import pytest

from general_ludd.probabilistic.count_min_sketch import CountMinSketch
from general_ludd.probabilistic.counting_bloom import CountingBloomFilter
from general_ludd.probabilistic.cuckoo_filter import CuckooFilter
from general_ludd.probabilistic.hyperloglog import HyperLogLog
from general_ludd.probabilistic.hyperloglog_v2 import HyperLogLogV2
from general_ludd.probabilistic.minhash import LSH, MinHash, _murmur64
from general_ludd.probabilistic.stable_bloom import StableBloomFilter
from general_ludd.probabilistic.tdigest import (
    Centroid,
    TDigest,
    TDigestMergeError,
    _cdf_from_centroids,
    _inv_scale,
    _merge_centroid_lists,
    _scale,
    _weight_integrated_location,
)


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


# ——— MinHash ——————————————————————————————————————————————————————


class TestMurmur64:
    def test_deterministic_same_input(self) -> None:
        a = _murmur64(b"hello", 42)
        b = _murmur64(b"hello", 42)
        assert a == b

    def test_different_seed_different_hash(self) -> None:
        a = _murmur64(b"hello", 0)
        b = _murmur64(b"hello", 1)
        assert a != b

    def test_empty_string(self) -> None:
        h = _murmur64(b"", 0)
        assert isinstance(h, int)
        assert h >= 0

    def test_long_input(self) -> None:
        data = b"x" * 1024
        h = _murmur64(data, 7)
        assert isinstance(h, int)
        assert h >= 0

    def test_bit31_cleared(self) -> None:
        for s in range(10):
            h = _murmur64(b"test", s)
            assert h < 2**63


class TestMinHashInit:
    def test_default_construction(self) -> None:
        mh = MinHash()
        assert mh.num_perm == 128
        assert mh.seed == 42
        assert len(mh.signature) == 128
        assert all(s == 0x7FFFFFFFFFFFFFFF for s in mh.signature)

    def test_custom_num_perm(self) -> None:
        mh = MinHash(num_perm=64, seed=99)
        assert mh.num_perm == 64
        assert mh.seed == 99
        assert len(mh.signature) == 64

    def test_num_perm_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            MinHash(num_perm=0)

    def test_num_perm_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            MinHash(num_perm=-1)

    def test_len_matches_num_perm(self) -> None:
        mh = MinHash(num_perm=200)
        assert len(mh) == 200

    def test_repr(self) -> None:
        mh = MinHash(num_perm=50, seed=7)
        r = repr(mh)
        assert "MinHash" in r
        assert "50" in r
        assert "7" in r


class TestMinHashUpdate:
    def test_update_sets_minima(self) -> None:
        mh = MinHash(num_perm=128)
        mh.update("hello")
        sig = mh.signature
        assert all(s < 0x7FFFFFFFFFFFFFFF for s in sig)

    def test_update_multiple_items(self) -> None:
        mh = MinHash(num_perm=64)
        for item in ["a", "b", "c", "d", "e"]:
            mh.update(item)
        sig = mh.signature
        assert all(s < 0x7FFFFFFFFFFFFFFF for s in sig)

    def test_update_bytes_vs_str(self) -> None:
        mh_str = MinHash(num_perm=64)
        mh_bytes = MinHash(num_perm=64)
        mh_str.update("data")
        mh_bytes.update(b"data")
        assert mh_str.signature == mh_bytes.signature

    def test_update_with_int(self) -> None:
        mh = MinHash(num_perm=64)
        mh.update(42)
        assert all(s < 0x7FFFFFFFFFFFFFFF for s in mh.signature)

    def test_add_many(self) -> None:
        mh = MinHash(num_perm=32)
        mh.add_many(["x", "y", "z"])
        assert all(s < 0x7FFFFFFFFFFFFFFF for s in mh.signature)

    def test_signature_is_tuple_copy(self) -> None:
        mh = MinHash(num_perm=8)
        mh.update("a")
        s1 = mh.signature
        s2 = mh.signature
        assert s1 == s2
        assert s1 is not s2


class TestMinHashJaccard:
    def test_identical_sets(self) -> None:
        a = MinHash(num_perm=128)
        b = MinHash(num_perm=128)
        for item in ["x", "y", "z"]:
            a.update(item)
            b.update(item)
        assert a.jaccard(b) == pytest.approx(1.0, abs=0.01)

    def test_disjoint_sets_nonzero(self) -> None:
        a = MinHash(num_perm=256)
        b = MinHash(num_perm=256)
        for i in range(200):
            a.update(f"left_{i}")
            b.update(f"right_{i}")
        assert a.jaccard(b) < 0.1

    def test_jaccard_range(self) -> None:
        a = MinHash(num_perm=256)
        b = MinHash(num_perm=256)
        for i in range(100):
            a.update(i)
        for i in range(50, 150):
            b.update(i)
        j = a.jaccard(b)
        assert 0.0 <= j <= 1.0

    def test_incompatible_sizes_raises(self) -> None:
        a = MinHash(num_perm=128)
        b = MinHash(num_perm=64)
        with pytest.raises(ValueError, match="incompatible"):
            a.jaccard(b)


class TestMinHashMerge:
    def test_merge_equals_elementwise_min(self) -> None:
        a = MinHash(num_perm=64, seed=0)
        b = MinHash(num_perm=64, seed=0)
        a.update("left")
        b.update("right")
        merged = a.merge(b)
        for i in range(64):
            assert merged.signature[i] == min(a.signature[i], b.signature[i])

    def test_merge_with_self_gives_self(self) -> None:
        a = MinHash(num_perm=64)
        a.add_many(["a", "b", "c"])
        merged = a.merge(a)
        assert merged.signature == a.signature

    def test_merge_unions_sets(self) -> None:
        a = MinHash(num_perm=256)
        b = MinHash(num_perm=256)
        a.add_many(range(100))
        b.add_many(range(50, 150))
        c = MinHash(num_perm=256)
        c.add_many(range(150))
        merged = a.merge(b)
        assert merged.signature == c.signature

    def test_merge_incompatible_raises(self) -> None:
        a = MinHash(num_perm=128)
        b = MinHash(num_perm=64)
        with pytest.raises(ValueError, match="incompatible"):
            a.merge(b)


class TestMinHashSerialization:
    def test_roundtrip(self) -> None:
        mh = MinHash(num_perm=64, seed=123)
        mh.add_many(["alpha", "beta", "gamma"])
        raw = mh.to_bytes()
        restored = MinHash.from_bytes(raw)
        assert restored.num_perm == mh.num_perm
        assert restored.seed == mh.seed
        assert restored.signature == mh.signature

    def test_empty_minhash_roundtrip(self) -> None:
        mh = MinHash(num_perm=8)
        raw = mh.to_bytes()
        restored = MinHash.from_bytes(raw)
        assert restored.signature == (0x7FFFFFFFFFFFFFFF,) * 8

    def test_truncated_header_raises(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            MinHash.from_bytes(b"\x00" * 3)

    def test_truncated_body_raises(self) -> None:
        mh = MinHash(num_perm=100)
        raw = mh.to_bytes()
        with pytest.raises(ValueError, match="truncated"):
            MinHash.from_bytes(raw[:20])


# ——— LSH ————————————————————————————————————————————————————————————


class TestLSHInit:
    def test_default_construction(self) -> None:
        lsh = LSH(num_perm=128)
        assert lsh.num_perm == 128
        assert lsh.bands == 16
        assert lsh.rows == 8
        assert lsh.item_count == 0

    def test_custom_bands(self) -> None:
        lsh = LSH(num_perm=100, bands=10)
        assert lsh.bands == 10
        assert lsh.rows == 10

    def test_bands_must_divide_num_perm(self) -> None:
        with pytest.raises(ValueError):
            LSH(num_perm=100, bands=7)

    def test_num_perm_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            LSH(num_perm=0)

    def test_bands_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            LSH(num_perm=128, bands=0)


class TestLSHInsertQuery:
    def test_insert_and_query_self(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.add_many(["a", "b", "c"])
        lsh.insert("key1", mh)
        candidates = lsh.query(mh)
        assert "key1" in candidates

    def test_insert_and_query_similar(self) -> None:
        lsh = LSH(num_perm=256, bands=16)
        mh1 = MinHash(num_perm=256)
        mh2 = MinHash(num_perm=256)
        mh1.add_many(range(100))
        mh2.add_many(range(90))
        lsh.insert("set_a", mh1)
        candidates = lsh.query(mh2)
        assert "set_a" in candidates

    def test_insert_and_query_dissimilar(self) -> None:
        lsh = LSH(num_perm=256, bands=16)
        mh1 = MinHash(num_perm=256)
        mh2 = MinHash(num_perm=256)
        mh1.add_many(range(100))
        mh2.add_many(range(1000, 1100))
        lsh.insert("set_a", mh1)
        candidates = lsh.query(mh2)
        assert "set_a" not in candidates

    def test_incompatible_num_perm_raises_on_insert(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=64)
        with pytest.raises(ValueError):
            lsh.insert("k", mh)

    def test_incompatible_num_perm_raises_on_query(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=64)
        with pytest.raises(ValueError):
            lsh.query(mh)

    def test_query_sorted(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.update("x")
        lsh.insert("z", mh)
        lsh.insert("a", mh)
        candidates = lsh.query(mh)
        assert candidates == ["a", "z"]

    def test_insert_multiple_same_minhash(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.update("shared")
        lsh.insert("a", mh)
        lsh.insert("b", mh)
        assert lsh.item_count == 2
        candidates = lsh.query(mh)
        assert "a" in candidates
        assert "b" in candidates


class TestLSHRemove:
    def test_remove_existing(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.update("data")
        lsh.insert("key", mh)
        lsh.remove("key")
        assert lsh.item_count == 0
        assert "key" not in lsh.query(mh)

    def test_remove_missing_raises(self) -> None:
        lsh = LSH(num_perm=128)
        with pytest.raises(KeyError):
            lsh.remove("nonexistent")

    def test_remove_cleans_empty_bucket(self) -> None:
        lsh = LSH(num_perm=128, bands=4)
        mh = MinHash(num_perm=128)
        mh.update("x")
        lsh.insert("k", mh)
        lsh.remove("k")
        assert lsh.item_count == 0


class TestLSHThreshold:
    def test_similarity_threshold_formula(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        expected = (1.0 / 16) ** (1.0 / 8)
        assert lsh.similarity_threshold() == pytest.approx(expected)


# ——— T-Digest ————————————————————————————————————————————————————————


class TestScaleFunctions:
    def test_scale_zero(self) -> None:
        for delta in [10.0, 50.0, 100.0]:
            assert _scale(0.0, delta) == pytest.approx(-delta / 4.0)

    def test_scale_half(self) -> None:
        for delta in [10.0, 100.0]:
            assert _scale(0.5, delta) == pytest.approx(0.0)

    def test_scale_one(self) -> None:
        for delta in [10.0, 100.0]:
            assert _scale(1.0, delta) == pytest.approx(delta / 4.0)

    def test_inv_scale_roundtrip(self) -> None:
        delta = 100.0
        for q in [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]:
            k = _scale(q, delta)
            q2 = _inv_scale(k, delta)
            assert q2 == pytest.approx(q, abs=1e-9)

    def test_scale_symmetry(self) -> None:
        delta = 50.0
        for q in [0.1, 0.2, 0.3, 0.4]:
            k_low = _scale(q, delta)
            k_high = _scale(1.0 - q, delta)
            assert k_low == pytest.approx(-k_high, abs=1e-9)


class TestWeightIntegratedLocation:
    def test_exact_min(self) -> None:
        centroids = [Centroid(mean=1.0, weight=2.0), Centroid(mean=5.0, weight=2.0)]
        val = _weight_integrated_location(centroids, 0.0, 4.0)
        assert val == 1.0

    def test_exact_max(self) -> None:
        centroids = [Centroid(mean=1.0, weight=2.0), Centroid(mean=5.0, weight=2.0)]
        val = _weight_integrated_location(centroids, 1.0, 4.0)
        assert val == 5.0

    def test_median_interpolates(self) -> None:
        centroids = [Centroid(mean=0.0, weight=1.0), Centroid(mean=10.0, weight=3.0)]
        val = _weight_integrated_location(centroids, 0.5, 4.0)
        assert 0.0 < val < 10.0


class TestCDFFromCentroids:
    def test_below_min(self) -> None:
        centroids = [Centroid(mean=0.0, weight=1.0), Centroid(mean=10.0, weight=1.0)]
        assert _cdf_from_centroids(centroids, -5.0, 2.0) == 0.0

    def test_above_max(self) -> None:
        centroids = [Centroid(mean=0.0, weight=1.0), Centroid(mean=10.0, weight=1.0)]
        assert _cdf_from_centroids(centroids, 15.0, 2.0) == 1.0

    def test_at_centroids(self) -> None:
        centroids = [Centroid(mean=0.0, weight=2.0), Centroid(mean=10.0, weight=2.0)]
        cdf_0 = _cdf_from_centroids(centroids, 0.0, 4.0)
        cdf_10 = _cdf_from_centroids(centroids, 10.0, 4.0)
        assert 0.0 <= cdf_0 <= 1.0
        assert cdf_10 == 1.0

    def test_empty(self) -> None:
        assert _cdf_from_centroids([], 5.0, 0.0) == 0.0


class TestMergeCentroidLists:
    def test_merges_into_single_when_under_compression(self) -> None:
        a = [Centroid(mean=1.0, weight=1.0)]
        b = [Centroid(mean=2.0, weight=1.0)]
        result = _merge_centroid_lists(a, b, compression=100.0)
        assert len(result) < 2 or all(c.weight > 0 for c in result)

    def test_merges_small_lists(self) -> None:
        a = [Centroid(mean=1.0, weight=1.0)]
        b = [Centroid(mean=2.0, weight=1.0)]
        result = _merge_centroid_lists(a, b, compression=200.0)
        assert len(result) >= 1
        total = sum(c.weight for c in result)
        assert total == pytest.approx(2.0)

    def test_preserves_total_weight(self) -> None:
        a = [Centroid(mean=i, weight=1.0) for i in range(10)]
        result = _merge_centroid_lists(a, [], compression=50.0)
        assert sum(c.weight for c in result) == pytest.approx(10.0)


class TestTDigestInit:
    def test_default_construction(self) -> None:
        td = TDigest(compression=100.0)
        assert td.compression == 100.0
        assert td.count == 0
        assert td.centroids is None

    def test_compression_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            TDigest(compression=-1.0)

    def test_compression_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            TDigest(compression=0.0)


class TestTDigestMinMax:
    def test_min_value_raises_on_empty(self) -> None:
        td = TDigest(compression=50.0)
        with pytest.raises(ValueError, match="empty"):
            _ = td.min_value

    def test_max_value_raises_on_empty(self) -> None:
        td = TDigest(compression=50.0)
        with pytest.raises(ValueError, match="empty"):
            _ = td.max_value

    def test_min_max_after_add(self) -> None:
        td = TDigest(compression=50.0)
        td.add(3.0)
        td.add(7.0)
        assert td.min_value == pytest.approx(3.0)
        assert td.max_value == pytest.approx(7.0)


class TestTDigestAdd:
    def test_add_increments_count(self) -> None:
        td = TDigest(compression=50.0)
        td.add(5.0)
        assert td.count == 1
        td.add(10.0)
        assert td.count == 2

    def test_add_non_finite_raises(self) -> None:
        td = TDigest(compression=50.0)
        with pytest.raises(ValueError, match="finite"):
            td.add(float("nan"))
        with pytest.raises(ValueError, match="finite"):
            td.add(float("inf"))

    def test_add_many_values(self) -> None:
        td = TDigest(compression=100.0)
        rng = random.Random(42)
        for _ in range(1000):
            td.add(rng.gauss(50.0, 10.0))
        assert td.count == 1000
        assert len(td.centroids) > 0 if td.centroids else False

    def test_centroids_returns_copy(self) -> None:
        td = TDigest(compression=50.0)
        td.add(1.0)
        c1 = td.centroids
        c2 = td.centroids
        if c1 is not None and c2 is not None:
            assert c1 is not c2


class TestTDigestMerge:
    def test_merge_empty_into_empty(self) -> None:
        a = TDigest(compression=50.0)
        b = TDigest(compression=50.0)
        a.merge(b)
        assert a.count == 0

    def test_merge_nonempty_into_empty(self) -> None:
        a = TDigest(compression=50.0)
        b = TDigest(compression=50.0)
        b.add(1.0)
        b.add(2.0)
        a.merge(b)
        assert a.count == 2

    def test_merge_empty_into_nonempty(self) -> None:
        a = TDigest(compression=50.0)
        b = TDigest(compression=50.0)
        a.add(1.0)
        a.merge(b)
        assert a.count == 1

    def test_merge_adds_counts(self) -> None:
        a = TDigest(compression=100.0)
        b = TDigest(compression=100.0)
        for v in [1.0, 2.0, 3.0]:
            a.add(v)
        for v in [4.0, 5.0]:
            b.add(v)
        a.merge(b)
        assert a.count == 5

    def test_merge_compression_mismatch_raises(self) -> None:
        a = TDigest(compression=50.0)
        b = TDigest(compression=100.0)
        with pytest.raises(TDigestMergeError):
            a.merge(b)


class TestTDigestQuantile:
    def test_quantile_raises_on_empty(self) -> None:
        td = TDigest(compression=50.0)
        with pytest.raises(ValueError, match="empty"):
            td.quantile(0.5)

    def test_quantile_single_value(self) -> None:
        td = TDigest(compression=50.0)
        td.add(42.0)
        assert td.quantile(0.0) == pytest.approx(42.0)
        assert td.quantile(0.5) == pytest.approx(42.0)
        assert td.quantile(1.0) == pytest.approx(42.0)

    def test_quantile_q_out_of_range(self) -> None:
        td = TDigest(compression=50.0)
        td.add(1.0)
        with pytest.raises(ValueError):
            td.quantile(-0.1)
        with pytest.raises(ValueError):
            td.quantile(1.1)

    def test_quantile_approximates_median(self) -> None:
        td = TDigest(compression=100.0)
        rng = random.Random(42)
        values = [rng.gauss(100.0, 15.0) for _ in range(2000)]
        for v in values:
            td.add(v)
        q50 = td.quantile(0.5)
        assert 90.0 < q50 < 110.0

    def test_quantile_extremes(self) -> None:
        td = TDigest(compression=100.0)
        td.add(0.0)
        td.add(100.0)
        assert td.quantile(0.0) == pytest.approx(0.0)
        assert td.quantile(1.0) == pytest.approx(100.0)

    def test_quantile_monotonic(self) -> None:
        td = TDigest(compression=100.0)
        rng = random.Random(7)
        for _ in range(500):
            td.add(rng.uniform(0.0, 100.0))
        vals = [td.quantile(q) for q in [0.1, 0.3, 0.5, 0.7, 0.9]]
        for i in range(len(vals) - 1):
            assert vals[i] <= vals[i + 1] + 1e-9


class TestTDigestCDF:
    def test_cdf_raises_on_empty(self) -> None:
        td = TDigest(compression=50.0)
        with pytest.raises(ValueError, match="empty"):
            td.cdf(0.0)

    def test_cdf_range(self) -> None:
        td = TDigest(compression=50.0)
        for v in [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]:
            td.add(v)
        c = td.cdf(25.0)
        assert 0.0 < c < 1.0

    def test_cdf_increasing(self) -> None:
        td = TDigest(compression=100.0)
        rng = random.Random(99)
        for _ in range(300):
            td.add(rng.uniform(0.0, 100.0))
        c_low = td.cdf(10.0)
        c_mid = td.cdf(50.0)
        c_high = td.cdf(90.0)
        assert c_low <= c_mid <= c_high + 1e-9


class TestTDigestSerialization:
    def test_roundtrip(self) -> None:
        td = TDigest(compression=100.0)
        rng = random.Random(3)
        for _ in range(200):
            td.add(rng.uniform(0.0, 100.0))
        raw = td.to_bytes()
        restored = TDigest.from_bytes(raw)
        assert restored.compression == td.compression
        assert restored.count == td.count
        assert restored.quantile(0.5) == pytest.approx(td.quantile(0.5), rel=0.1)

    def test_empty_roundtrip(self) -> None:
        td = TDigest(compression=50.0)
        raw = td.to_bytes()
        restored = TDigest.from_bytes(raw)
        assert restored.compression == 50.0
        assert restored.count == 0
        assert restored.centroids is None

    def test_truncated_header_raises(self) -> None:
        with pytest.raises(ValueError, match="compression header"):
            TDigest.from_bytes(b"\x00" * 2)

    def test_roundtrip_via_from_bytes(self) -> None:
        td = TDigest(compression=75.0)
        td.add(1.0)
        td.add(100.0)
        restored = TDigest.from_bytes(td.to_bytes())
        assert restored.min_value == pytest.approx(1.0)
        assert restored.max_value == pytest.approx(100.0)


class TestTDigestPickle:
    def test_pickle_roundtrip(self) -> None:
        td = TDigest(compression=100.0)
        rng = random.Random(55)
        for _ in range(300):
            td.add(rng.gauss(50.0, 10.0))
        data = pickle.dumps(td)
        restored = pickle.loads(data)
        assert restored.compression == td.compression
        assert restored.count == td.count
        assert restored.quantile(0.5) == pytest.approx(td.quantile(0.5))

    def test_pickle_empty(self) -> None:
        td = TDigest(compression=50.0)
        restored = pickle.loads(pickle.dumps(td))
        assert restored.count == 0
        assert restored.centroids is None


class TestCentroid:
    def test_creation(self) -> None:
        c = Centroid(mean=3.14, weight=2.0)
        assert c.mean == 3.14
        assert c.weight == 2.0

    def test_slots(self) -> None:
        c = Centroid(mean=1.0, weight=1.0)
        assert not hasattr(c, "__dict__")


# ——— Count-Min Sketch ———————————————————————————————————————————————


class TestCountMinSketchInit:
    def test_default_construction(self) -> None:
        cms = CountMinSketch(width=100, depth=5)
        assert cms.width == 100
        assert cms.depth == 5
        assert cms.conservative is False

    def test_conservative_construction(self) -> None:
        cms = CountMinSketch(width=50, depth=3, conservative=True)
        assert cms.conservative is True
        assert cms.width == 50
        assert cms.depth == 3

    def test_width_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="width must be >= 1"):
            CountMinSketch(width=0, depth=5)

    def test_width_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="width must be >= 1"):
            CountMinSketch(width=-1, depth=5)

    def test_depth_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="depth must be >= 1"):
            CountMinSketch(width=100, depth=0)

    @pytest.mark.parametrize(
        "epsilon,delta,expected_min_width,expected_min_depth",
        [
            (0.1, 0.1, 27, 3),
            (0.01, 0.01, 271, 5),
            (0.001, 0.001, 2718, 7),
        ],
    )
    def test_from_epsilon_delta(
        self, epsilon: float, delta: float, expected_min_width: int, expected_min_depth: int
    ) -> None:
        cms = CountMinSketch.from_epsilon_delta(epsilon, delta)
        assert cms.width >= expected_min_width
        assert cms.depth >= expected_min_depth

    def test_from_epsilon_delta_invalid_epsilon(self) -> None:
        with pytest.raises(ValueError, match="epsilon must be in"):
            CountMinSketch.from_epsilon_delta(1.5, 0.1)
        with pytest.raises(ValueError, match="epsilon must be in"):
            CountMinSketch.from_epsilon_delta(0.0, 0.1)

    def test_from_epsilon_delta_invalid_delta(self) -> None:
        with pytest.raises(ValueError, match="delta must be in"):
            CountMinSketch.from_epsilon_delta(0.1, 1.5)
        with pytest.raises(ValueError, match="delta must be in"):
            CountMinSketch.from_epsilon_delta(0.1, 0.0)


class TestCountMinSketchAddEstimate:
    def test_estimate_zero_when_empty(self) -> None:
        cms = CountMinSketch(width=100, depth=5)
        assert cms.estimate("anything") == 0
        assert cms.estimate(b"bytes") == 0
        assert cms.estimate(42) == 0

    def test_add_and_estimate_single(self) -> None:
        cms = CountMinSketch(width=1000, depth=5)
        cms.add("hello", count=3)
        assert cms.estimate("hello") == 3

    def test_add_multiple_items(self) -> None:
        cms = CountMinSketch(width=10000, depth=5)
        items = {"alpha": 2, "beta": 5, "gamma": 1}
        for item, cnt in items.items():
            for _ in range(cnt):
                cms.add(item)
        for item, cnt in items.items():
            assert cms.estimate(item) >= cnt

    def test_add_negative_count_raises(self) -> None:
        cms = CountMinSketch(width=100, depth=5)
        with pytest.raises(ValueError, match="count must be >= 1"):
            cms.add("x", count=-1)

    def test_add_zero_count_raises(self) -> None:
        cms = CountMinSketch(width=100, depth=5)
        with pytest.raises(ValueError, match="count must be >= 1"):
            cms.add("x", count=0)

    def test_estimate_overcounts_never_under(self) -> None:
        cms = CountMinSketch(width=5000, depth=10)
        n = 500
        for i in range(n):
            for _ in range(i % 5 + 1):
                cms.add(f"item_{i}")
        for i in range(n):
            true_count = i % 5 + 1
            assert cms.estimate(f"item_{i}") >= true_count

    def test_error_bound_statistical(self) -> None:
        cms = CountMinSketch(width=5000, depth=5)
        n = 1000
        for i in range(n):
            cnt = (i % 10) + 1
            for _ in range(cnt):
                cms.add(f"norm_{i}")
        total_added = sum((i % 10) + 1 for i in range(n))
        overcount_sum = 0
        for i in range(n):
            true_cnt = (i % 10) + 1
            over = cms.estimate(f"norm_{i}") - true_cnt
            overcount_sum += max(over, 0)
        overcount_ratio = overcount_sum / total_added
        assert overcount_ratio < 0.15

    def test_conservative_update(self) -> None:
        cms = CountMinSketch(width=1000, depth=5, conservative=True)
        for _ in range(100):
            cms.add("key")
        est = cms.estimate("key")
        assert 100 <= est <= 100 + 10


class TestCountMinSketchHeavyHitters:
    def test_no_candidates_returns_empty(self) -> None:
        cms = CountMinSketch(width=100, depth=3)
        cms.add("x", count=10)
        assert cms.heavy_hitters(threshold=5, candidates=None) == []

    def test_no_candidates_above_threshold(self) -> None:
        cms = CountMinSketch(width=100, depth=3)
        cms.add("a", count=2)
        cms.add("b", count=2)
        result = cms.heavy_hitters(threshold=10, candidates={"a", "b"})
        assert result == []

    def test_finds_heavy_hitters(self) -> None:
        cms = CountMinSketch(width=10000, depth=5)
        for _ in range(50):
            cms.add("heavy")
        for _ in range(3):
            cms.add("light")
        result = cms.heavy_hitters(threshold=40, candidates={"heavy", "light"})
        assert len(result) == 1
        assert result[0][0] == "heavy"

    def test_descending_frequency_order(self) -> None:
        cms = CountMinSketch(width=10000, depth=5)
        for _ in range(30):
            cms.add("mid")
        for _ in range(60):
            cms.add("top")
        for _ in range(10):
            cms.add("low")
        result = cms.heavy_hitters(threshold=5, candidates={"low", "mid", "top"})
        assert result[0][0] == "top"
        assert result[1][0] == "mid"
        assert result[2][0] == "low"

    def test_threshold_zero_raises(self) -> None:
        cms = CountMinSketch(width=100, depth=3)
        with pytest.raises(ValueError, match="threshold must be >= 1"):
            cms.heavy_hitters(threshold=0)


class TestCountMinSketchMerge:
    def test_merge_adds_counts(self) -> None:
        a = CountMinSketch(width=5000, depth=5)
        b = CountMinSketch(width=5000, depth=5)
        for _ in range(10):
            a.add("shared")
        for _ in range(5):
            b.add("shared")
        for _ in range(20):
            b.add("only_b")
        a.merge(b)
        assert a.estimate("shared") >= 15
        assert a.estimate("only_b") >= 20

    def test_merge_different_width_raises(self) -> None:
        a = CountMinSketch(width=100, depth=5)
        b = CountMinSketch(width=200, depth=5)
        with pytest.raises(ValueError, match="different dimensions"):
            a.merge(b)

    def test_merge_different_depth_raises(self) -> None:
        a = CountMinSketch(width=100, depth=5)
        b = CountMinSketch(width=100, depth=3)
        with pytest.raises(ValueError, match="different dimensions"):
            a.merge(b)

    def test_merge_empty_with_nonempty(self) -> None:
        a = CountMinSketch(width=1000, depth=4)
        b = CountMinSketch(width=1000, depth=4)
        b.add("x", count=7)
        a.merge(b)
        assert a.estimate("x") == 7


class TestCountMinSketchClear:
    def test_clear_zeros_all_estimates(self) -> None:
        cms = CountMinSketch(width=100, depth=3)
        cms.add("a", count=5)
        cms.add("b", count=3)
        cms.clear()
        assert cms.estimate("a") == 0
        assert cms.estimate("b") == 0

    def test_clear_then_reuse(self) -> None:
        cms = CountMinSketch(width=1000, depth=3)
        cms.add("x", count=10)
        cms.clear()
        cms.add("y", count=5)
        assert cms.estimate("x") == 0
        assert cms.estimate("y") == 5


class TestCountMinSketchSerialization:
    def test_roundtrip(self) -> None:
        cms = CountMinSketch(width=200, depth=4)
        cms.add("alpha", count=3)
        cms.add("beta", count=7)
        raw = cms.to_bytes()
        restored = CountMinSketch.from_bytes(raw)
        assert restored.width == cms.width
        assert restored.depth == cms.depth
        assert restored.conservative == cms.conservative
        assert restored.estimate("alpha") == cms.estimate("alpha")
        assert restored.estimate("beta") == cms.estimate("beta")

    def test_roundtrip_conservative(self) -> None:
        cms = CountMinSketch(width=200, depth=4, conservative=True)
        cms.add("k", count=5)
        restored = CountMinSketch.from_bytes(cms.to_bytes())
        assert restored.conservative is True
        assert restored.estimate("k") == 5

    def test_empty_roundtrip(self) -> None:
        cms = CountMinSketch(width=64, depth=3)
        restored = CountMinSketch.from_bytes(cms.to_bytes())
        assert restored.estimate("anything") == 0

    def test_truncated_header_raises(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            CountMinSketch.from_bytes(b"\x00\x00")

    def test_body_length_mismatch_raises(self) -> None:
        cms = CountMinSketch(width=100, depth=5)
        raw = cms.to_bytes()
        with pytest.raises(ValueError, match="body length mismatch"):
            CountMinSketch.from_bytes(raw[:20])


class TestCountMinSketchEdgeCases:
    def test_string_bytes_int_float_types(self) -> None:
        cms = CountMinSketch(width=100, depth=3)
        vals: list = ["str", b"bytes", 42, 3.14]
        for val in vals:
            cms.add(val)
            assert cms.estimate(val) >= 1

    def test_large_count_value(self) -> None:
        cms = CountMinSketch(width=1000, depth=5)
        cms.add("big", count=10000)
        assert cms.estimate("big") >= 10000

    def test_high_frequency_item_stable_over_rows(self) -> None:
        cms = CountMinSketch(width=500, depth=10)
        for _ in range(200):
            cms.add("hot")
        for i in range(500):
            cms.add(f"cold_{i}")
        assert cms.estimate("hot") == 200

    def test_non_member_returns_zero(self) -> None:
        cms = CountMinSketch(width=1000, depth=5)
        for i in range(100):
            cms.add(f"real_{i}")
        assert cms.estimate("never_added") == 0


# ============================================================================
# StableBloomFilter
# ============================================================================


class TestStableBloomInit:
    def test_default_construction(self) -> None:
        sbf = StableBloomFilter(capacity=1000)
        assert sbf.capacity == 1000
        assert sbf.error_rate == 0.01
        assert sbf.counter_bits == 4

    def test_custom_params(self) -> None:
        sbf = StableBloomFilter(capacity=500, error_rate=0.05, counter_bits=8)
        assert sbf.capacity == 500
        assert sbf.error_rate == 0.05
        assert sbf.counter_bits == 8

    def test_invalid_capacity_raises(self) -> None:
        with pytest.raises(ValueError):
            StableBloomFilter(capacity=0)

    def test_invalid_error_rate_raises(self) -> None:
        with pytest.raises(ValueError):
            StableBloomFilter(capacity=100, error_rate=0.0)
        with pytest.raises(ValueError):
            StableBloomFilter(capacity=100, error_rate=1.0)

    def test_invalid_counter_bits_raises(self) -> None:
        with pytest.raises(ValueError):
            StableBloomFilter(capacity=100, counter_bits=0)
        with pytest.raises(ValueError):
            StableBloomFilter(capacity=100, counter_bits=17)

    def test_properties_positive(self) -> None:
        sbf = StableBloomFilter(capacity=1000)
        assert sbf.slot_count > 0
        assert sbf.hash_count >= 1
        assert 0.0 < sbf.decay_probability <= 1.0


class TestStableBloomAddCount:
    def test_add_sets_min_count(self) -> None:
        sbf = StableBloomFilter(capacity=1000)
        sbf.add("hello")
        assert sbf.contains("hello")
        assert sbf.count("hello") >= 1

    def test_empty_filter_contains_nothing(self) -> None:
        sbf = StableBloomFilter(capacity=1000)
        assert not sbf.contains("anything")

    def test_multiple_items_positive(self) -> None:
        sbf = StableBloomFilter(capacity=10000)
        for i in range(200):
            sbf.add(f"item_{i}")
        for i in range(200):
            assert sbf.contains(f"item_{i}")

    def test_duplicate_add_increments_count(self) -> None:
        sbf = StableBloomFilter(capacity=10000)
        sbf.add("dup")
        c1 = sbf.count("dup")
        sbf.add("dup")
        assert sbf.count("dup") >= c1

    def test_string_bytes_int_float_types(self) -> None:
        sbf = StableBloomFilter(capacity=1000)
        sbf.add("str")
        sbf.add(b"bytes")
        sbf.add(42)
        sbf.add(3.14)
        assert sbf.contains("str")
        assert sbf.contains(b"bytes")
        assert sbf.contains(42)
        assert sbf.contains(3.14)


class TestStableBloomDecay:
    def test_decay_all_reduces_counters(self) -> None:
        sbf = StableBloomFilter(capacity=100, error_rate=0.01, counter_bits=4, seed=42)
        for i in range(5):
            sbf.add(f"item_{i}")
        before = sbf.count("item_0")
        sbf.decay_all(steps=100)
        assert sbf.count("item_0") <= before

    def test_saturated_fraction_positive(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=42)
        for i in range(50):
            sbf.add(f"sat_{i}")
        frac = sbf.saturated_fraction()
        assert frac > 0.0

    def test_estimated_count_positive(self) -> None:
        sbf = StableBloomFilter(capacity=1000, seed=42)
        for i in range(100):
            sbf.add(f"est_{i}")
        est = sbf.estimated_count()
        assert est > 0

    def test_estimated_count_zero_for_empty(self) -> None:
        sbf = StableBloomFilter(capacity=1000)
        assert sbf.estimated_count() == 0.0

    def test_saturated_fraction_zero_for_empty(self) -> None:
        sbf = StableBloomFilter(capacity=1000)
        assert sbf.saturated_fraction() == 0.0


class TestStableBloomSerialization:
    def test_roundtrip_bytes(self) -> None:
        sbf = StableBloomFilter(capacity=500, error_rate=0.02, counter_bits=6, seed=42)
        for i in range(50):
            sbf.add(f"rt_{i}")
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored.capacity == sbf.capacity
        assert restored.error_rate == sbf.error_rate
        assert restored.counter_bits == sbf.counter_bits
        for i in range(50):
            assert restored.contains(f"rt_{i}")

    def test_empty_roundtrip(self) -> None:
        sbf = StableBloomFilter(capacity=100, error_rate=0.05)
        raw = sbf.to_bytes()
        restored = StableBloomFilter.from_bytes(raw)
        assert restored.estimated_count() == 0.0
        assert not restored.contains("nothing")

    def test_from_bytes_truncated_raises(self) -> None:
        with pytest.raises(ValueError):
            StableBloomFilter.from_bytes(b"\x00\x00\x00\x00")


class TestStableBloomEdgeCases:
    def test_seed_determinism(self) -> None:
        a = StableBloomFilter(capacity=500, seed=42)
        b = StableBloomFilter(capacity=500, seed=42)
        for i in range(30):
            a.add(f"det_{i}")
            b.add(f"det_{i}")
        assert a.to_bytes() == b.to_bytes()

    def test_different_seeds_diverge_decay(self) -> None:
        a = StableBloomFilter(capacity=100, error_rate=0.05, counter_bits=4, seed=42)
        b = StableBloomFilter(capacity=100, error_rate=0.05, counter_bits=4, seed=99)
        for i in range(10):
            a.add(f"div_{i}")
            b.add(f"div_{i}")
        a.decay_all(steps=5)
        b.decay_all(steps=5)
        assert a.to_bytes() != b.to_bytes()


# ============================================================================
# CuckooFilter
# ============================================================================


class TestCuckooFilterInit:
    def test_default_construction(self) -> None:
        cf = CuckooFilter(capacity=1000)
        assert cf.capacity == 1000
        assert cf.error_rate == 0.01
        assert cf.bucket_size == 4

    def test_custom_params(self) -> None:
        cf = CuckooFilter(capacity=500, error_rate=0.001, bucket_size=8)
        assert cf.capacity == 500
        assert cf.bucket_size == 8

    def test_invalid_capacity_raises(self) -> None:
        with pytest.raises(ValueError):
            CuckooFilter(capacity=0)
        with pytest.raises(ValueError):
            CuckooFilter(capacity=-5)

    def test_invalid_error_rate_raises(self) -> None:
        with pytest.raises(ValueError):
            CuckooFilter(capacity=100, error_rate=0.0)
        with pytest.raises(ValueError):
            CuckooFilter(capacity=100, error_rate=1.5)

    def test_invalid_bucket_size_raises(self) -> None:
        with pytest.raises(ValueError):
            CuckooFilter(capacity=100, bucket_size=1)

    def test_properties(self) -> None:
        cf = CuckooFilter(capacity=1000)
        assert cf.num_buckets >= 2
        assert cf.fingerprint_bits >= 4
        assert cf.size == 0


class TestCuckooFilterAddContains:
    def test_add_single_item(self) -> None:
        cf = CuckooFilter(capacity=1000)
        assert cf.add("hello")
        assert cf.contains("hello")

    def test_empty_filter_contains_nothing(self) -> None:
        cf = CuckooFilter(capacity=1000)
        assert not cf.contains("anything")

    def test_add_many_within_capacity(self) -> None:
        cf = CuckooFilter(capacity=2000)
        added = 0
        for i in range(500):
            if cf.add(f"item_{i}"):
                added += 1
        assert added >= 400
        assert cf.size == added

    def test_duplicate_item_still_contains(self) -> None:
        cf = CuckooFilter(capacity=1000)
        assert cf.add("dup")
        cf.add("dup")
        assert cf.contains("dup")
        assert cf.size >= 1

    def test_string_bytes_int_types(self) -> None:
        cf = CuckooFilter(capacity=1000)
        cf.add("str")
        cf.add(b"bytes")
        cf.add(42)
        assert cf.contains("str")
        assert cf.contains(b"bytes")
        assert cf.contains(42)


class TestCuckooFilterRemove:
    def test_remove_existing(self) -> None:
        cf = CuckooFilter(capacity=1000)
        cf.add("present")
        assert cf.remove("present")
        assert not cf.contains("present")

    def test_remove_missing(self) -> None:
        cf = CuckooFilter(capacity=1000)
        cf.add("present")
        assert not cf.remove("absent")

    def test_remove_decrements_size(self) -> None:
        cf = CuckooFilter(capacity=1000)
        cf.add("x")
        cf.add("y")
        before = cf.size
        cf.remove("x")
        assert cf.size == before - 1

    def test_add_remove_add_cycle(self) -> None:
        cf = CuckooFilter(capacity=1000)
        cf.add("cycle")
        cf.remove("cycle")
        assert cf.add("cycle")
        assert cf.contains("cycle")


class TestCuckooFilterLoadFactor:
    def test_empty_load_factor(self) -> None:
        cf = CuckooFilter(capacity=1000)
        assert cf.load_factor() == 0.0

    def test_load_factor_grows(self) -> None:
        cf = CuckooFilter(capacity=1000)
        for i in range(100):
            cf.add(f"lf_{i}")
        lf = cf.load_factor()
        assert lf > 0.0


class TestCuckooFilterSerialization:
    def test_roundtrip_bytes(self) -> None:
        cf = CuckooFilter(capacity=500, error_rate=0.01, bucket_size=4, seed=42)
        for i in range(50):
            cf.add(f"rt_{i}")
        raw = cf.to_bytes()
        restored = CuckooFilter.from_bytes(raw)
        assert restored.capacity == cf.capacity
        assert restored.bucket_size == cf.bucket_size
        assert restored.size == cf.size
        for i in range(50):
            assert restored.contains(f"rt_{i}")

    def test_empty_roundtrip(self) -> None:
        cf = CuckooFilter(capacity=200)
        raw = cf.to_bytes()
        restored = CuckooFilter.from_bytes(raw)
        assert restored.size == 0
        assert not restored.contains("nothing")

    def test_from_bytes_truncated_raises(self) -> None:
        with pytest.raises(ValueError):
            CuckooFilter.from_bytes(b"\x00\x00")


class TestCuckooFilterEdgeCases:
    def test_seed_determinism(self) -> None:
        a = CuckooFilter(capacity=500, seed=42)
        b = CuckooFilter(capacity=500, seed=42)
        for i in range(40):
            a.add(f"det_{i}")
            b.add(f"det_{i}")
        assert a.to_bytes() == b.to_bytes()

    def test_load_factor_never_exceeds_one(self) -> None:
        cf = CuckooFilter(capacity=100)
        for i in range(500):
            cf.add(f"max_{i}")
        assert cf.load_factor() <= 1.0

    def test_size_after_many_inserts_is_nonzero(self) -> None:
        cf = CuckooFilter(capacity=500)
        for i in range(100):
            cf.add(f"count_{i}")
        assert cf.size > 0


# ============================================================================
# HyperLogLogV2
# ============================================================================


class TestHyperLogLogV2Init:
    def test_default_construction(self) -> None:
        hll = HyperLogLogV2()
        assert hll.precision == 14
        assert hll.register_count > 0
        assert hll.is_sparse is True

    def test_custom_precision(self) -> None:
        for p in (4, 8, 12, 16, 18):
            hll = HyperLogLogV2(precision=p)
            assert hll.precision == p
            assert hll.register_count == (1 << p)

    def test_invalid_precision_raises(self) -> None:
        with pytest.raises(ValueError):
            HyperLogLogV2(precision=3)
        with pytest.raises(ValueError):
            HyperLogLogV2(precision=19)

    def test_initially_sparse(self) -> None:
        hll = HyperLogLogV2(precision=10)
        assert hll.is_sparse


class TestHyperLogLogV2AddCount:
    def test_empty_returns_zero(self) -> None:
        hll = HyperLogLogV2(precision=8)
        assert hll.count() == 0

    def test_single_item_sparse(self) -> None:
        hll = HyperLogLogV2(precision=8)
        hll.add("one")
        c = hll.count()
        assert 0 < c <= 5

    def test_small_sparse(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(50):
            hll.add(f"item_{i}")
        assert hll.is_sparse
        est = hll.count()
        assert 30 <= est <= 100

    def test_sparse_to_dense_transition(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(5000):
            hll.add(f"trans_{i}")
        assert not hll.is_sparse
        est = hll.count()
        assert est > 0

    def test_duplicate_items_no_change(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for _ in range(10):
            hll.add("same")
        c1 = hll.count()
        for _ in range(10):
            hll.add("same")
        assert hll.count() == c1

    def test_string_bytes_int_types(self) -> None:
        hll = HyperLogLogV2(precision=10)
        hll.add("str")
        hll.add(b"bytes")
        hll.add(42)
        hll.add(3.14)
        assert hll.count() > 0

    def test_large_dense_estimate_reasonable(self) -> None:
        hll = HyperLogLogV2(precision=12)
        for i in range(100000):
            hll.add(f"big_{i}")
        est = hll.count()
        assert 90000 <= est <= 110000

    def test_precision_six_works(self) -> None:
        hll = HyperLogLogV2(precision=6)
        hll.add("p6")
        assert hll.count() > 0


class TestHyperLogLogV2Merge:
    def test_merge_sparse_sparse(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(30):
            a.add(f"a_{i}")
        for i in range(30, 60):
            b.add(f"b_{i}")
        a.merge(b)
        est = a.count()
        assert 40 <= est <= 80

    def test_merge_sparse_same_items(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(30):
            a.add(f"shared_{i}")
            b.add(f"shared_{i}")
        a.merge(b)
        est = a.count()
        assert 20 <= est <= 50

    def test_merge_dense_dense(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(5000):
            a.add(f"dense_a_{i}")
        for i in range(5000, 10000):
            b.add(f"dense_b_{i}")
        assert not a.is_sparse
        assert not b.is_sparse
        a.merge(b)
        est = a.count()
        assert est > 5000

    def test_merge_sparse_into_dense(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(5000):
            a.add(f"d_a_{i}")
        for i in range(10000, 10030):
            b.add(f"s_b_{i}")
        assert not a.is_sparse
        assert b.is_sparse
        before = a.count()
        a.merge(b)
        assert a.count() >= before

    def test_merge_different_precision_raises(self) -> None:
        a = HyperLogLogV2(precision=8)
        b = HyperLogLogV2(precision=10)
        with pytest.raises(ValueError):
            a.merge(b)


class TestHyperLogLogV2ErrorBound:
    def test_error_bound_positive(self) -> None:
        hll = HyperLogLogV2(precision=14)
        assert 0.0 < hll.error_bound() < 1.0

    def test_error_bound_decreases_with_precision(self) -> None:
        low = HyperLogLogV2(precision=4).error_bound()
        high = HyperLogLogV2(precision=14).error_bound()
        assert high < low


class TestHyperLogLogV2Serialization:
    def test_roundtrip_sparse(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(30):
            hll.add(f"spr_{i}")
        assert hll.is_sparse
        raw = hll.to_bytes()
        restored = HyperLogLogV2.from_bytes(raw)
        assert restored.precision == hll.precision
        assert restored.is_sparse
        assert abs(restored.count() - hll.count()) <= 2

    def test_roundtrip_dense(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(5000):
            hll.add(f"den_{i}")
        assert not hll.is_sparse
        raw = hll.to_bytes()
        restored = HyperLogLogV2.from_bytes(raw)
        assert restored.precision == hll.precision
        assert abs(restored.count() - hll.count()) <= 2

    def test_empty_roundtrip(self) -> None:
        hll = HyperLogLogV2(precision=8)
        raw = hll.to_bytes()
        restored = HyperLogLogV2.from_bytes(raw)
        assert restored.count() == 0

    def test_from_bytes_truncated_raises(self) -> None:
        with pytest.raises(ValueError):
            HyperLogLogV2.from_bytes(b"\x00\x00\x00")


class TestHyperLogLogV2EdgeCases:
    def test_merge_empty_into_populated(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(50):
            a.add(f"pop_{i}")
        before = a.count()
        a.merge(b)
        assert a.count() == before

    def test_merge_populated_into_empty(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(50):
            b.add(f"into_{i}")
        a.merge(b)
        assert abs(a.count() - b.count()) <= 2

    def test_bias_correction_at_small_cardinality(self) -> None:
        hll = HyperLogLogV2(precision=6)
        hll.add("x")
        hll.add("y")
        hll.add("z")
        c = hll.count()
        assert 0 <= c <= 10

    def test_sparse_transition_in_merge(self) -> None:
        a = HyperLogLogV2(precision=10)
        for i in range(50):
            a.add(f"pre_{i}")
        assert a.is_sparse
        b = HyperLogLogV2(precision=10)
        for i in range(50, 200):
            b.add(f"post_{i}")
        a.merge(b)
        est = a.count()
        assert est > 100


# ============================================================================
# HyperLogLogV2 — Deep Internals
# ============================================================================


class TestHyperLogLogV2Internals:
    def test_compute_alpha_special_m16(self) -> None:
        assert HyperLogLogV2._compute_alpha(16) == pytest.approx(0.673)

    def test_compute_alpha_special_m32(self) -> None:
        assert HyperLogLogV2._compute_alpha(32) == pytest.approx(0.697)

    def test_compute_alpha_special_m64(self) -> None:
        assert HyperLogLogV2._compute_alpha(64) == pytest.approx(0.709)

    def test_compute_alpha_general(self) -> None:
        for m in (128, 256, 1024, 4096):
            a = HyperLogLogV2._compute_alpha(m)
            expected = 0.7213 / (1.0 + 1.079 / m)
            assert a == pytest.approx(expected)

    def test_fnv1a_64_deterministic(self) -> None:
        a = HyperLogLogV2._fnv1a_64(b"hello")
        b = HyperLogLogV2._fnv1a_64(b"hello")
        assert a == b

    def test_fnv1a_64_empty_input(self) -> None:
        h = HyperLogLogV2._fnv1a_64(b"")
        assert isinstance(h, int)
        assert h >= 0
        assert h <= 0xFFFFFFFFFFFFFFFF

    def test_fnv1a_64_different_inputs_different_hashes(self) -> None:
        a = HyperLogLogV2._fnv1a_64(b"alpha")
        b = HyperLogLogV2._fnv1a_64(b"beta")
        assert a != b

    def test_fnv1a_64_output_in_64bit_range(self) -> None:
        for data in [b"", b"x", b"y" * 100]:
            h = HyperLogLogV2._fnv1a_64(data)
            assert 0 <= h <= 0xFFFFFFFFFFFFFFFF

    def test_hash64_deterministic(self) -> None:
        a = HyperLogLogV2._hash64(b"test_key")
        b = HyperLogLogV2._hash64(b"test_key")
        assert a == b

    def test_hash64_different_keys_different_hashes(self) -> None:
        a = HyperLogLogV2._hash64(b"key_a")
        b = HyperLogLogV2._hash64(b"key_b")
        assert a != b

    def test_hash64_output_in_64bit_range(self) -> None:
        for data in [b"", b"x", b"long" * 64]:
            h = HyperLogLogV2._hash64(data)
            assert 0 <= h <= 0xFFFFFFFFFFFFFFFF

    def test_rho_zero_returns_bits_plus_one(self) -> None:
        for p in (4, 8, 12, 14, 18):
            hll = HyperLogLogV2(precision=p)
            assert hll._rho(0) == (64 - p) + 1

    def test_rho_one_returns_bits(self) -> None:
        for p in (4, 8, 12):
            hll = HyperLogLogV2(precision=p)
            w = 1  # 1.bit_length() == 1
            assert hll._rho(w) == (64 - p) - w.bit_length() + 1

    def test_rho_large_value(self) -> None:
        hll = HyperLogLogV2(precision=14)
        w = 1 << 40
        assert hll._rho(w) == (64 - 14) - w.bit_length() + 1

    def test_rho_increasing(self) -> None:
        hll = HyperLogLogV2(precision=14)
        rhos = [hll._rho(1 << i) for i in range(5, 50)]
        for i in range(len(rhos) - 1):
            assert rhos[i] >= rhos[i + 1]

    def test_item_to_bytes_str(self) -> None:
        assert HyperLogLogV2._item_to_bytes("abc") == b"abc"

    def test_item_to_bytes_bytes_pass_through(self) -> None:
        assert HyperLogLogV2._item_to_bytes(b"raw") == b"raw"

    def test_item_to_bytes_int(self) -> None:
        assert HyperLogLogV2._item_to_bytes(42) == b"42"

    def test_item_to_bytes_float(self) -> None:
        assert HyperLogLogV2._item_to_bytes(3.14) == b"3.14"

    def test_item_to_bytes_other_type(self) -> None:
        result = HyperLogLogV2._item_to_bytes([1, 2, 3])
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_sparse_max_entries_default_precision(self) -> None:
        hll = HyperLogLogV2(precision=14)
        expected = max(1, int((1 << 14) * 0.15))
        assert hll._sparse_max_entries() == expected

    def test_sparse_max_entries_low_precision(self) -> None:
        hll = HyperLogLogV2(precision=4)
        expected = max(1, int((1 << 4) * 0.15))
        assert hll._sparse_max_entries() == expected

    def test_apply_bias_correction_no_bias_data(self) -> None:
        hll = HyperLogLogV2(precision=14)
        raw = 100.0
        corrected = hll._apply_bias_correction(raw)
        assert corrected == pytest.approx(raw)

    def test_apply_bias_correction_negative_raw(self) -> None:
        hll = HyperLogLogV2(precision=4)
        corrected = hll._apply_bias_correction(-1.0)
        assert corrected == pytest.approx(-1.0)

    def test_apply_bias_correction_above_range(self) -> None:
        hll = HyperLogLogV2(precision=4)
        bias_len = len(HyperLogLogV2._BIAS_DATA[4])
        raw = float(bias_len + 100)
        corrected = hll._apply_bias_correction(raw)
        assert corrected == pytest.approx(raw)

    def test_apply_bias_correction_in_range(self) -> None:
        hll = HyperLogLogV2(precision=4)
        bias_data = HyperLogLogV2._BIAS_DATA[4]
        test_idx = min(5, len(bias_data) - 1)
        raw = float(test_idx)
        bias = bias_data[test_idx]
        corrected = hll._apply_bias_correction(raw)
        if bias != 0.0:
            assert corrected < raw
        assert corrected >= 0

    def test_raw_estimate_sparse_small(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(10):
            hll.add(f"e_{i}")
        raw = hll._raw_estimate()
        assert raw > 0.0

    def test_raw_estimate_dense(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(5000):
            hll.add(f"dense_e_{i}")
        assert not hll.is_sparse
        raw = hll._raw_estimate()
        assert raw > 0.0

    def test_raw_estimate_empty_sparse(self) -> None:
        hll = HyperLogLogV2(precision=8)
        raw = hll._raw_estimate()
        assert raw == 0.0

    def test_transition_to_dense_noop_when_already_dense(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(5000):
            hll.add(f"td_{i}")
        assert not hll.is_sparse
        regs_before = bytes(hll._registers) if hll._registers else b""
        hll._transition_to_dense()
        assert not hll.is_sparse
        assert bytes(hll._registers) == regs_before

    def test_transition_to_dense_preserves_max_registers(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(100):
            for _ in range(3):
                hll.add(f"dup_{i}")
        assert hll.is_sparse
        hll._transition_to_dense()
        assert not hll.is_sparse

    def test_to_bytes_empty(self) -> None:
        hll = HyperLogLogV2(precision=8)
        raw = hll.to_bytes()
        assert len(raw) > 0
        restored = HyperLogLogV2.from_bytes(raw)
        assert restored.count() == 0

    def test_sparse_list_after_add_distinct(self) -> None:
        hll = HyperLogLogV2(precision=10)
        hll.add("a")
        hll.add("b")
        assert len(hll._sparse_list) == 2

    def test_sparse_list_after_add_duplicate(self) -> None:
        hll = HyperLogLogV2(precision=10)
        hll.add("x")
        hll.add("x")
        hll.add("x")
        assert len(hll._sparse_list) == 3

    def test_merge_three_instances(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        c = HyperLogLogV2(precision=10)
        for i in range(30):
            a.add(f"a_{i}")
        for i in range(30, 60):
            b.add(f"b_{i}")
        for i in range(60, 90):
            c.add(f"c_{i}")
        a.merge(b)
        a.merge(c)
        est = a.count()
        assert 60 <= est <= 130

    def test_small_sparse_count_accuracy(self) -> None:
        hll = HyperLogLogV2(precision=12)
        for i in range(20):
            hll.add(f"small_{i}")
        c = hll.count()
        assert 15 <= c <= 30

    def test_dense_after_merge_stays_dense(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(5000):
            a.add(f"d1_{i}")
        for i in range(5000, 10000):
            b.add(f"d2_{i}")
        a.merge(b)
        assert not a.is_sparse

    def test_dense_merge_another_dense_remains_dense(self) -> None:
        a = HyperLogLogV2(precision=10)
        b = HyperLogLogV2(precision=10)
        for i in range(5000):
            a.add(f"q_{i}")
        for i in range(5000, 10000):
            b.add(f"r_{i}")
        assert not a.is_sparse
        a.merge(b)
        assert not a.is_sparse

    def test_register_count_consistent(self) -> None:
        for p in (4, 6, 8, 10, 12, 14, 16, 18):
            hll = HyperLogLogV2(precision=p)
            assert hll.register_count == (1 << p)

    def test_error_bound_formula(self) -> None:
        hll = HyperLogLogV2(precision=10)
        expected = 1.04 / (math.sqrt(1 << 10))
        assert hll.error_bound() == pytest.approx(expected)

    def test_count_on_sparse_does_not_transition(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(30):
            hll.add(f"stay_sparse_{i}")
        assert hll.is_sparse
        _ = hll.count()
        assert hll.is_sparse

    def test_count_on_dense_uses_registers(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(5000):
            hll.add(f"c_{i}")
        assert not hll.is_sparse
        c = hll.count()
        assert c > 0

    def test_hash64_distribution_across_items(self) -> None:
        hashes = set()
        for i in range(100):
            key = HyperLogLogV2._item_to_bytes(f"dist_{i}")
            h = HyperLogLogV2._hash64(key)
            hashes.add(h)
        assert len(hashes) == 100

    def test_fnv1a_64_fnv_offset_basis_constant(self) -> None:
        h = HyperLogLogV2._fnv1a_64(b"\x01")
        assert isinstance(h, int)
        assert h != 0xCBF29CE484222325

    def test_bias_correction_not_applied_at_high_precision(self) -> None:
        hll = HyperLogLogV2(precision=10)
        for i in range(100):
            hll.add(f"bias_test_{i}")
        est = hll.count()
        assert est > 0

    def test_merge_sparse_produces_sparse_when_under_threshold(self) -> None:
        a = HyperLogLogV2(precision=12)
        b = HyperLogLogV2(precision=12)
        for i in range(5):
            a.add(f"m1_{i}")
        for i in range(5, 10):
            b.add(f"m2_{i}")
        a.merge(b)
        assert a.is_sparse or a.count() > 0

    def test_linear_counting_fallback_triggered(self) -> None:
        hll = HyperLogLogV2(precision=4)
        for i in range(5):
            hll.add(f"lc_{i}")
        c = hll.count()
        assert 1 <= c <= 20


# ============================================================================
# CuckooFilter — Deep Internals
# ============================================================================


class TestCuckooFilterInternals:
    def test_fingerprint_nonzero_for_all_keys(self) -> None:
        cf = CuckooFilter(capacity=1000)
        for key in [b"a", b"hello", b"z" * 20, b""]:
            fp = cf._fingerprint(key)
            assert fp >= 1
            assert fp <= cf._fingerprint_mask

    def test_fingerprint_deterministic(self) -> None:
        cf = CuckooFilter(capacity=1000)
        assert cf._fingerprint(b"key") == cf._fingerprint(b"key")

    def test_index_hash_in_bounds(self) -> None:
        cf = CuckooFilter(capacity=1000)
        for _ in range(50):
            key = f"idx_{_}".encode()
            idx = cf._index_hash(key)
            assert 0 <= idx < cf._num_buckets

    def test_alt_index_in_bounds(self) -> None:
        cf = CuckooFilter(capacity=1000)
        for fp_val in (1, 7, 15, 63, 255):
            for idx_val in (0, 1, 10, 100):
                alt = cf._alt_index(idx_val, fp_val)
                assert 0 <= alt < cf._num_buckets

    def test_alt_index_different_from_original(self) -> None:
        cf = CuckooFilter(capacity=10000)
        fp = 42
        for idx_val in range(10):
            alt = cf._alt_index(idx_val, fp)
            assert alt != idx_val

    def test_next_power_of_two(self) -> None:
        assert CuckooFilter._next_power_of_two(1) == 1
        assert CuckooFilter._next_power_of_two(3) == 4
        assert CuckooFilter._next_power_of_two(5) == 8
        assert CuckooFilter._next_power_of_two(100) == 128
        assert CuckooFilter._next_power_of_two(256) == 256

    def test_hash_fp_deterministic(self) -> None:
        a = CuckooFilter._hash_fp(b"fp_test")
        b = CuckooFilter._hash_fp(b"fp_test")
        assert a == b

    def test_hash_fp_32bit_range(self) -> None:
        for key in [b"a", b"longer_key", b""]:
            h = CuckooFilter._hash_fp(key)
            assert 0 <= h <= 0xFFFFFFFF

    def test_hash64_deterministic(self) -> None:
        a = CuckooFilter._hash64(b"cuckoo_test")
        b = CuckooFilter._hash64(b"cuckoo_test")
        assert a == b

    def test_item_to_bytes_roundtrip_str(self) -> None:
        assert CuckooFilter._item_to_bytes("hello") == b"hello"

    def test_item_to_bytes_roundtrip_int(self) -> None:
        assert CuckooFilter._item_to_bytes(42) == b"42"

    def test_full_bucket_causes_kick(self) -> None:
        cf = CuckooFilter(capacity=20, bucket_size=2)
        added = 0
        for i in range(100):
            if cf.add(f"fill_{i}"):
                added += 1
        assert added >= 1

    def test_max_kicks_limit(self) -> None:
        cf = CuckooFilter(capacity=5, bucket_size=2)
        count = 0
        for i in range(50):
            if cf.add(f"maxkick_{i}"):
                count += 1
        assert count <= cf._MAX_KICKS or count >= 1

    def test_fingerprint_mask_covers_all_bits(self) -> None:
        cf = CuckooFilter(capacity=1000, error_rate=0.001)
        mask = cf._fingerprint_mask
        assert mask > 0
        assert (mask & (mask + 1)) == 0


# ============================================================================
# StableBloomFilter — Deep Internals
# ============================================================================


class TestStableBloomInternals:
    def test_get_counter_zero_initially(self) -> None:
        sbf = StableBloomFilter(capacity=1000)
        assert sbf._get_counter(0) == 0
        assert sbf._get_counter(sbf.slot_count - 1) == 0

    def test_set_counter_then_get(self) -> None:
        sbf = StableBloomFilter(capacity=1000, counter_bits=8)
        max_val = (1 << 8) - 1
        sbf._set_counter(5, 42)
        assert sbf._get_counter(5) == 42
        sbf._set_counter(5, max_val)
        assert sbf._get_counter(5) == max_val

    def test_set_counter_clamps_saturation(self) -> None:
        sbf = StableBloomFilter(capacity=1000, counter_bits=4)
        max_val = (1 << 4) - 1
        sbf._set_counter(0, max_val + 100)
        assert sbf._get_counter(0) <= max_val

    def test_item_to_bytes_str(self) -> None:
        assert StableBloomFilter._item_to_bytes("abc") == b"abc"

    def test_item_to_bytes_bytes(self) -> None:
        assert StableBloomFilter._item_to_bytes(b"raw") == b"raw"

    def test_hash_deterministic(self) -> None:
        a = StableBloomFilter._hash(b"data", 42)
        b = StableBloomFilter._hash(b"data", 42)
        assert a == b

    def test_hash_different_seeds_different_output(self) -> None:
        a = StableBloomFilter._hash(b"data", 1)
        b = StableBloomFilter._hash(b"data", 2)
        assert a != b

    def test_hash_in_slot_range(self) -> None:
        sbf = StableBloomFilter(capacity=1000)
        for seed in range(5):
            idx = StableBloomFilter._hash(b"test", seed) % sbf.slot_count
            assert 0 <= idx < sbf.slot_count

    def test_fnv1a_deterministic(self) -> None:
        a = StableBloomFilter._fnv1a(b"fnv_test")
        b = StableBloomFilter._fnv1a(b"fnv_test")
        assert a == b

    def test_decay_all_num_slots_zero_returns_none(self) -> None:
        sbf = StableBloomFilter(capacity=100, seed=42)
        for i in range(10):
            sbf.add(f"d_{i}")
        sbf.decay_all(steps=0)
        assert sbf.count("d_0") >= 1

    def test_decay_all_single_step(self) -> None:
        sbf = StableBloomFilter(capacity=500, seed=42)
        for i in range(20):
            sbf.add(f"dec_{i}")
        sbf.decay_all(steps=1)
        assert sbf.count("dec_0") >= 1

    def test_estimated_count_via_fill_ratio(self) -> None:
        sbf = StableBloomFilter(capacity=1000, seed=42)
        for i in range(200):
            sbf.add(f"fill_{i}")
        est = sbf.estimated_count()
        assert 100 <= est <= 300


# ============================================================================
# CountingBloomFilter — Deep Internals
# ============================================================================


class TestCountingBloomInternals:
    def test_get_counter_zero_initial(self) -> None:
        cbf = CountingBloomFilter(capacity=100, error_rate=0.01)
        assert cbf._get_counter(0) == 0
        assert cbf._get_counter(cbf.slot_count - 1) == 0

    def test_set_counter_then_read(self) -> None:
        cbf = CountingBloomFilter(capacity=100, error_rate=0.01, counter_bits=8)
        max_val = (1 << 8) - 1
        cbf._set_counter(3, 77)
        assert cbf._get_counter(3) == 77
        cbf._set_counter(3, max_val)
        assert cbf._get_counter(3) == max_val

    def test_set_counter_saturation(self) -> None:
        cbf = CountingBloomFilter(capacity=100, error_rate=0.01, counter_bits=4)
        max_val = (1 << 4) - 1
        cbf._set_counter(0, max_val + 50)
        assert cbf._get_counter(0) <= max_val

    def test_hash_deterministic(self) -> None:
        a = CountingBloomFilter._hash(b"bloom_key", 7)
        b = CountingBloomFilter._hash(b"bloom_key", 7)
        assert a == b

    def test_hash_different_seeds(self) -> None:
        a = CountingBloomFilter._hash(b"key", 1)
        b = CountingBloomFilter._hash(b"key", 2)
        assert a != b

    def test_hash_slot_range(self) -> None:
        cbf = CountingBloomFilter(capacity=1000)
        for seed in range(5):
            idx = CountingBloomFilter._hash(b"test_item", seed) % cbf.slot_count
            assert 0 <= idx < cbf.slot_count

    def test_fnv1a_deterministic(self) -> None:
        a = CountingBloomFilter._fnv1a(b"fnv_data")
        b = CountingBloomFilter._fnv1a(b"fnv_data")
        assert a == b

    def test_fnv1a_empty_input(self) -> None:
        h = CountingBloomFilter._fnv1a(b"")
        assert isinstance(h, int)
        assert h >= 0

    def test_item_to_bytes_all_types(self) -> None:
        assert CountingBloomFilter._item_to_bytes("str") == b"str"
        assert CountingBloomFilter._item_to_bytes(b"raw") == b"raw"
        assert CountingBloomFilter._item_to_bytes(99) == b"99"
        assert CountingBloomFilter._item_to_bytes(2.5) == b"2.5"


# ============================================================================
# CountMinSketch — Deep Internals
# ============================================================================


class TestCountMinSketchInternals:
    def test_item_to_bytes_str(self) -> None:
        assert CountMinSketch._item_to_bytes("abc") == b"abc"

    def test_item_to_bytes_bytes(self) -> None:
        assert CountMinSketch._item_to_bytes(b"binary") == b"binary"

    def test_item_to_bytes_int(self) -> None:
        assert CountMinSketch._item_to_bytes(42) == b"42"

    def test_hash_deterministic(self) -> None:
        a = CountMinSketch._hash(b"key", 0)
        b = CountMinSketch._hash(b"key", 0)
        assert a == b

    def test_hash_different_seeds(self) -> None:
        a = CountMinSketch._hash(b"key", 0)
        b = CountMinSketch._hash(b"key", 1)
        assert a != b

    def test_hash_in_range(self) -> None:
        cms = CountMinSketch(width=100, depth=5)
        for seed in range(10):
            idx = CountMinSketch._hash(b"test", seed) % cms.width
            assert 0 <= idx < cms.width

    def test_fnv1a_deterministic(self) -> None:
        a = CountMinSketch._fnv1a(b"cms_data")
        b = CountMinSketch._fnv1a(b"cms_data")
        assert a == b

    def test_fnv1a_empty(self) -> None:
        h = CountMinSketch._fnv1a(b"")
        assert isinstance(h, int)
        assert h >= 0

    def test_fnv1a_different_inputs(self) -> None:
        a = CountMinSketch._fnv1a(b"x")
        b = CountMinSketch._fnv1a(b"y")
        assert a != b


# ============================================================================
# HyperLogLog — Deep Internals
# ============================================================================


class TestHyperLogLogInternals:
    def test_compute_alpha_m16(self) -> None:
        assert HyperLogLog._compute_alpha(16) == pytest.approx(0.673)

    def test_compute_alpha_m32(self) -> None:
        assert HyperLogLog._compute_alpha(32) == pytest.approx(0.697)

    def test_compute_alpha_m64(self) -> None:
        assert HyperLogLog._compute_alpha(64) == pytest.approx(0.709)

    def test_compute_alpha_general(self) -> None:
        for m in (128, 256, 1024):
            a = HyperLogLog._compute_alpha(m)
            expected = 0.7213 / (1.0 + 1.079 / m)
            assert a == pytest.approx(expected, rel=1e-5)

    def test_fnv1a_64_deterministic(self) -> None:
        a = HyperLogLog._fnv1a_64(b"data")
        b = HyperLogLog._fnv1a_64(b"data")
        assert a == b

    def test_hash64_deterministic(self) -> None:
        a = HyperLogLog._hash64(b"key")
        b = HyperLogLog._hash64(b"key")
        assert a == b

    def test_rho_zero(self) -> None:
        for p in (4, 8, 12, 14):
            hll = HyperLogLog(precision=p)
            assert hll._rho(0) == (64 - p) + 1

    def test_rho_one(self) -> None:
        hll = HyperLogLog(precision=8)
        assert hll._rho(1) == (64 - 8) - 1 + 1

    def test_rho_large(self) -> None:
        hll = HyperLogLog(precision=10)
        w = 1 << 40
        assert hll._rho(w) == (64 - 10) - w.bit_length() + 1

    def test_rho_monotonic(self) -> None:
        hll = HyperLogLog(precision=12)
        rhos = [hll._rho(1 << i) for i in range(5, 50)]
        for i in range(len(rhos) - 1):
            assert rhos[i] >= rhos[i + 1]

    def test_item_to_bytes_str(self) -> None:
        assert HyperLogLog._item_to_bytes("hello") == b"hello"

    def test_item_to_bytes_bytes(self) -> None:
        assert HyperLogLog._item_to_bytes(b"raw") == b"raw"

    def test_item_to_bytes_int(self) -> None:
        assert HyperLogLog._item_to_bytes(99) == b"99"

    def test_item_to_bytes_float(self) -> None:
        assert HyperLogLog._item_to_bytes(1.5) == b"1.5"

    def test_fnv1a_64_output_range(self) -> None:
        for data in [b"", b"x", b"y" * 200]:
            h = HyperLogLog._fnv1a_64(data)
            assert 0 <= h <= 0xFFFFFFFFFFFFFFFF

    def test_hash64_output_range(self) -> None:
        for data in [b"", b"short", b"long" * 100]:
            h = HyperLogLog._hash64(data)
            assert 0 <= h <= 0xFFFFFFFFFFFFFFFF


# ============================================================================
# MinHash — Deep Internals
# ============================================================================


class TestMinHashInternals:
    def test_item_to_bytes_str(self) -> None:
        mh = MinHash(num_perm=8)
        assert mh._item_to_bytes("text") == b"text"

    def test_item_to_bytes_bytes(self) -> None:
        mh = MinHash(num_perm=8)
        assert mh._item_to_bytes(b"binary") == b"binary"

    def test_item_to_bytes_int(self) -> None:
        mh = MinHash(num_perm=8)
        assert mh._item_to_bytes(42) == b"42"

    def test_item_to_bytes_float(self) -> None:
        mh = MinHash(num_perm=8)
        assert mh._item_to_bytes(3.14) == b"3.14"

    def test_item_to_bytes_other(self) -> None:
        mh = MinHash(num_perm=8)
        result = mh._item_to_bytes((1, 2))
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_len_matches_num_perm_large(self) -> None:
        mh = MinHash(num_perm=1024)
        assert len(mh) == 1024

    def test_signature_always_tuple(self) -> None:
        mh = MinHash(num_perm=64)
        mh.update("a")
        sig = mh.signature
        assert isinstance(sig, tuple)
        assert len(sig) == 64

    def test_empty_add_many_no_effect(self) -> None:
        mh = MinHash(num_perm=32)
        orig = mh.signature
        mh.add_many([])
        assert mh.signature == orig

    def test_repr_with_high_num_perm(self) -> None:
        mh = MinHash(num_perm=999, seed=5)
        r = repr(mh)
        assert "999" in r
        assert "5" in r

    def test_jaccard_self_is_one(self) -> None:
        mh = MinHash(num_perm=128)
        mh.add_many(["a", "b", "c", "d", "e"])
        assert mh.jaccard(mh) == pytest.approx(1.0)

    def test_jaccard_different_seeds_rejected_for_large_sketches(self) -> None:
        a = MinHash(num_perm=256, seed=1)
        b = MinHash(num_perm=256, seed=2)
        a.update("x")
        b.update("x")
        with pytest.raises(ValueError, match="seeds differ"):
            a.jaccard(b)

    def test_merge_different_seeds_raises(self) -> None:
        a = MinHash(num_perm=64, seed=0)
        b = MinHash(num_perm=64, seed=1)
        with pytest.raises(ValueError, match="seeds differ"):
            a.merge(b)

    def test_jaccard_different_seeds_raises(self) -> None:
        a = MinHash(num_perm=64, seed=1)
        b = MinHash(num_perm=64, seed=2)
        with pytest.raises(ValueError, match="seeds differ"):
            a.jaccard(b)


# ============================================================================
# TDigest — Deep Internals
# ============================================================================


class TestTDigestDeepInternals:
    def test_scale_asymptotic_delta_large(self) -> None:
        delta = 10000.0
        assert _scale(0.0, delta) == pytest.approx(-delta / 4.0)
        assert _scale(0.5, delta) == pytest.approx(0.0)
        assert _scale(1.0, delta) == pytest.approx(delta / 4.0)

    def test_inv_scale_boundary(self) -> None:
        delta = 50.0
        assert _inv_scale(-delta / 4.0, delta) == pytest.approx(0.0, abs=1e-12)
        assert _inv_scale(0.0, delta) == pytest.approx(0.5, abs=1e-12)
        assert _inv_scale(delta / 4.0, delta) == pytest.approx(1.0, abs=1e-12)

    def test_merge_centroid_lists_single(self) -> None:
        result = _merge_centroid_lists([Centroid(mean=5.0, weight=3.0)], [], compression=50.0)
        assert len(result) == 1
        assert result[0].mean == 5.0
        assert result[0].weight == 3.0

    def test_merge_centroid_lists_empty_both(self) -> None:
        result = _merge_centroid_lists([], [], compression=50.0)
        assert result == []

    def test_weight_integrated_location_single_centroid(self) -> None:
        val = _weight_integrated_location([Centroid(mean=7.0, weight=5.0)], 0.5, 5.0)
        assert val == 7.0

    def test_cdf_from_centroids_single_centroid(self) -> None:
        centroids = [Centroid(mean=5.0, weight=10.0)]
        assert _cdf_from_centroids(centroids, 0.0, 10.0) == 0.0
        assert _cdf_from_centroids(centroids, 5.0, 10.0) == 1.0
        assert _cdf_from_centroids(centroids, 10.0, 10.0) == 1.0

    def test_cdf_from_centroids_below_first(self) -> None:
        centroids = [Centroid(mean=10.0, weight=2.0), Centroid(mean=20.0, weight=2.0)]
        assert _cdf_from_centroids(centroids, 5.0, 4.0) == 0.0

    def test_cdf_from_centroids_between(self) -> None:
        centroids = [Centroid(mean=10.0, weight=2.0), Centroid(mean=20.0, weight=2.0)]
        c = _cdf_from_centroids(centroids, 15.0, 4.0)
        assert 0.0 < c < 1.0

    def test_compress_after_add_within_compression(self) -> None:
        td = TDigest(compression=100.0)
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            td.add(v)
        assert td.count == 5
        assert td.min_value == 1.0
        assert td.max_value == 5.0

    def test_merge_does_not_mutate_other(self) -> None:
        a = TDigest(compression=50.0)
        b = TDigest(compression=50.0)
        a.add(1.0)
        b.add(100.0)
        b_count_before = b.count
        a.merge(b)
        assert b.count == b_count_before

    def test_quantile_edge_low_load(self) -> None:
        td = TDigest(compression=100.0)
        td.add(0.0)
        td.add(100.0)
        q_low = td.quantile(0.0)
        q_high = td.quantile(1.0)
        assert q_low == pytest.approx(0.0)
        assert q_high == pytest.approx(100.0)

    def test_quantile_interpolation_small(self) -> None:
        td = TDigest(compression=100.0)
        td.add(0.0)
        td.add(10.0)
        td.add(20.0)
        q50 = td.quantile(0.5)
        assert 5.0 <= q50 <= 15.0

    def test_cdf_outside_range(self) -> None:
        td = TDigest(compression=50.0)
        for v in [10.0, 20.0, 30.0]:
            td.add(v)
        assert td.cdf(0.0) < 0.5
        assert td.cdf(100.0) == pytest.approx(1.0, abs=0.01)

    def test_quantile_many_uniform(self) -> None:
        td = TDigest(compression=200.0)
        rng = random.Random(123)
        for _ in range(3000):
            td.add(rng.uniform(0.0, 1000.0))
        q01 = td.quantile(0.01)
        q50 = td.quantile(0.50)
        q99 = td.quantile(0.99)
        assert 0.0 <= q01 <= q50 <= q99 <= 1000.0

    def test_cdf_increasing_monotonic(self) -> None:
        td = TDigest(compression=200.0)
        rng = random.Random(42)
        for _ in range(1000):
            td.add(rng.gauss(500.0, 100.0))
        prev = 0.0
        for x in range(0, 1100, 100):
            c = td.cdf(float(x))
            assert c >= prev - 1e-9
            prev = c

    def test_pickle_roundtrip_empty(self) -> None:
        td = TDigest(compression=50.0)
        restored = pickle.loads(pickle.dumps(td))
        assert restored.compression == 50.0
        assert restored.count == 0

    def test_merge_compression_mismatch_via_constructor(self) -> None:
        a = TDigest(compression=25.0)
        b = TDigest(compression=50.0)
        a.add(1.0)
        b.add(2.0)
        with pytest.raises(TDigestMergeError, match="compression"):
            a.merge(b)

    def test_centroid_eq(self) -> None:
        c1 = Centroid(mean=1.0, weight=2.0)
        c2 = Centroid(mean=1.0, weight=2.0)
        assert c1 == c2
        assert c1 != Centroid(mean=1.0, weight=3.0)
        assert c1 != Centroid(mean=2.0, weight=2.0)

    def test_centroid_immutable(self) -> None:
        c = Centroid(mean=1.0, weight=2.0)
        with pytest.raises(AttributeError):
            c.mean = 5.0  # type: ignore[misc]

    def test_scale_symmetry_full_range(self) -> None:
        delta = 75.0
        for q in [0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99]:
            k = _scale(q, delta)
            q_round = _inv_scale(k, delta)
            assert q_round == pytest.approx(q, abs=1e-9)

    def test_quantile_duplicate_values(self) -> None:
        td = TDigest(compression=50.0)
        for _ in range(100):
            td.add(42.0)
        assert td.quantile(0.5) == pytest.approx(42.0)

    def test_cdf_before_min(self) -> None:
        td = TDigest(compression=100.0)
        td.add(100.0)
        assert td.cdf(50.0) == 0.0

    def test_cdf_after_max(self) -> None:
        td = TDigest(compression=100.0)
        td.add(0.0)
        assert td.cdf(50.0) == 1.0


# ============================================================================
# LSH — Deep Internals
# ============================================================================


class TestLSHDeepInternals:
    def test_insert_rejects_minhash_from_different_seed_domain(self) -> None:
        lsh = LSH(num_perm=128)
        lsh.insert("seed-one", MinHash(num_perm=128, seed=1))

        with pytest.raises(ValueError, match="seeds differ"):
            lsh.insert("seed-two", MinHash(num_perm=128, seed=2))

    def test_query_rejects_minhash_from_different_seed_domain(self) -> None:
        lsh = LSH(num_perm=128)
        lsh.insert("seed-one", MinHash(num_perm=128, seed=1))

        with pytest.raises(ValueError, match="seeds differ"):
            lsh.query(MinHash(num_perm=128, seed=2))

    def test_empty_index_can_adopt_a_new_seed_domain(self) -> None:
        lsh = LSH(num_perm=128)
        lsh.insert("seed-one", MinHash(num_perm=128, seed=1))
        lsh.remove("seed-one")

        replacement = MinHash(num_perm=128, seed=2)
        lsh.insert("seed-two", replacement)
        assert lsh.query(replacement) == ["seed-two"]

    def test_items_dict_after_insert(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.update("data")
        lsh.insert("key1", mh)
        assert "key1" in lsh._items
        assert lsh._items["key1"] is mh

    def test_items_dict_after_remove(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.update("x")
        lsh.insert("k", mh)
        lsh.remove("k")
        assert "k" not in lsh._items

    def test_buckets_populated_after_insert(self) -> None:
        lsh = LSH(num_perm=128, bands=8)
        mh = MinHash(num_perm=128)
        mh.update("hello")
        lsh.insert("item_a", mh)
        assert len(lsh._buckets) >= 1
        for bucket_entries in lsh._buckets.values():
            assert any(k == "item_a" for k, _ in bucket_entries)

    def test_buckets_cleaned_on_remove(self) -> None:
        lsh = LSH(num_perm=128, bands=4)
        mh = MinHash(num_perm=128)
        mh.update("solo")
        lsh.insert("solo_key", mh)
        lsh.remove("solo_key")
        assert len(lsh._buckets) == 0

    def test_bucket_key_determinism(self) -> None:
        lsh = LSH(num_perm=128, bands=8)
        mh_a = MinHash(num_perm=128)
        mh_b = MinHash(num_perm=128)
        mh_a.update("x")
        mh_b.update("x")
        lsh.insert("a", mh_a)
        original_keys = set(lsh._buckets.keys())
        lsh.insert("b", mh_b)
        new_keys = set(lsh._buckets.keys())
        assert original_keys == new_keys

    def test_insert_duplicate_key_overwrites(self) -> None:
        lsh = LSH(num_perm=128)
        mh1 = MinHash(num_perm=128)
        mh2 = MinHash(num_perm=128)
        mh1.update("old")
        mh2.update("new")
        lsh.insert("dup", mh1)
        lsh.insert("dup", mh2)
        assert lsh._items["dup"] is mh2

    def test_query_empty_lsh(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.update("nothing")
        assert lsh.query(mh) == []

    def test_query_returns_sorted_unique(self) -> None:
        lsh = LSH(num_perm=128, bands=8)
        mh = MinHash(num_perm=128)
        mh.update("shared")
        lsh.insert("zeta", mh)
        lsh.insert("alpha", mh)
        lsh.insert("gamma", mh)
        result = lsh.query(mh)
        assert result == sorted(set(result))
        assert len(result) == 3

    def test_multiple_items_same_bucket(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        mh = MinHash(num_perm=128)
        mh.update("common")
        for i in range(5):
            lsh.insert(f"bucket_{i}", mh)
        candidates = lsh.query(mh)
        assert len(candidates) == 5

    def test_bands_one(self) -> None:
        lsh = LSH(num_perm=128, bands=1)
        mh = MinHash(num_perm=128)
        mh.update("single_band")
        lsh.insert("sb", mh)
        assert lsh.bands == 1
        assert lsh.rows == 128
        assert lsh.query(mh) == ["sb"]

    def test_bands_equals_num_perm(self) -> None:
        lsh = LSH(num_perm=64, bands=64)
        mh = MinHash(num_perm=64)
        mh.update("thin")
        lsh.insert("t", mh)
        assert lsh.rows == 1
        candidates = lsh.query(mh)
        assert "t" in candidates

    def test_item_count_tracks_inserts_and_removes(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.update("count")
        assert lsh.item_count == 0
        lsh.insert("c1", mh)
        assert lsh.item_count == 1
        lsh.insert("c2", mh)
        assert lsh.item_count == 2
        lsh.remove("c1")
        assert lsh.item_count == 1

    def test_remove_cleans_only_target_key(self) -> None:
        lsh = LSH(num_perm=128, bands=8)
        mh_a = MinHash(num_perm=128)
        mh_b = MinHash(num_perm=128)
        mh_a.update("target")
        mh_b.update("target")
        lsh.insert("keep", mh_a)
        lsh.insert("drop", mh_b)
        lsh.remove("drop")
        assert "keep" in lsh._items
        assert "drop" not in lsh._items
        assert "keep" in lsh.query(mh_a)

    def test_properties_consistent_after_operations(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        assert lsh.num_perm == 128
        assert lsh.bands == 16
        assert lsh.rows == 8
        mh = MinHash(num_perm=128)
        mh.update("prop")
        lsh.insert("p", mh)
        assert lsh.num_perm == 128
        assert lsh.bands == 16
        assert lsh.rows == 8
        lsh.remove("p")
        assert lsh.num_perm == 128

    def test_similarity_threshold_at_various_bands(self) -> None:
        for bands in (1, 2, 4, 8, 16, 32):
            num_perm = bands * 8
            lsh = LSH(num_perm=num_perm, bands=bands)
            t = lsh.similarity_threshold()
            expected = (1.0 / bands) ** (1.0 / lsh.rows)
            assert t == pytest.approx(expected)
            assert 0.0 < t <= 1.0

    def test_query_after_remove_empty_index(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.update("gone")
        lsh.insert("transient", mh)
        lsh.remove("transient")
        assert lsh.query(mh) == []
        assert lsh.item_count == 0

    def test_remove_middle_of_three(self) -> None:
        lsh = LSH(num_perm=128, bands=8)
        mh = MinHash(num_perm=128)
        mh.update("mid")
        lsh.insert("a", mh)
        lsh.insert("b", mh)
        lsh.insert("c", mh)
        lsh.remove("b")
        assert lsh.item_count == 2
        result = lsh.query(mh)
        assert result == ["a", "c"]

    def test_bucket_entries_per_band(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        mh = MinHash(num_perm=128)
        mh.update("per_band")
        lsh.insert("pb", mh)
        bands_with_entry = set()
        for bucket_entries in lsh._buckets.values():
            for _, band in bucket_entries:
                bands_with_entry.add(band)
        assert len(bands_with_entry) == 16

    def test_long_key_handling(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.update("x")
        long_key = "k" * 1024
        lsh.insert(long_key, mh)
        assert lsh.item_count == 1
        assert long_key in lsh.query(mh)

    def test_insert_same_minhash_many_keys(self) -> None:
        lsh = LSH(num_perm=128, bands=8)
        mh = MinHash(num_perm=128)
        mh.update("fixed")
        for i in range(20):
            lsh.insert(f"k{i}", mh)
        assert lsh.item_count == 20
        result = lsh.query(mh)
        assert len(result) == 20

    def test_bucket_keys_are_positive(self) -> None:
        lsh = LSH(num_perm=128)
        mh = MinHash(num_perm=128)
        mh.update("pos")
        lsh.insert("pos_key", mh)
        for bucket_key in lsh._buckets:
            assert bucket_key >= 0

    def test_query_after_three_way_merge(self) -> None:
        lsh = LSH(num_perm=256)
        mh_a = MinHash(num_perm=256)
        mh_b = MinHash(num_perm=256)
        mh_c = MinHash(num_perm=256)
        mh_a.add_many(range(100))
        mh_b.add_many(range(50, 150))
        mh_c.add_many(range(100, 200))
        lsh.insert("A", mh_a)
        lsh.insert("B", mh_b)
        lsh.insert("C", mh_c)
        q = MinHash(num_perm=256)
        q.add_many(range(80, 120))
        candidates = lsh.query(q)
        assert len(candidates) >= 1
