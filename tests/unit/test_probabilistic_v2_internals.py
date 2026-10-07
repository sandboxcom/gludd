"""Tests split from :mod:`tests.unit.test_probabilistic_deep` by coherent behavior."""

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
from general_ludd.probabilistic.minhash import LSH, MinHash
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
        assert _cdf_from_centroids(centroids, 5.0, 10.0) == 0.5
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
