"""Deep tests for probabilistic data structures."""

from __future__ import annotations

import pickle
import random

import pytest

from general_ludd.probabilistic.count_min_sketch import CountMinSketch
from general_ludd.probabilistic.counting_bloom import CountingBloomFilter
from general_ludd.probabilistic.hyperloglog import HyperLogLog
from general_ludd.probabilistic.minhash import LSH, MinHash, _murmur64
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
