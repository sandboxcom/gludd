"""Unit tests for HyperLogLogV2 — HLL++ with sparse representation and bias correction."""

from __future__ import annotations

import struct

import pytest

from general_ludd.probabilistic.hyperloglog_v2 import HyperLogLogV2


class TestInit:
    def test_default_precision(self) -> None:
        hll = HyperLogLogV2()
        assert hll.precision == 14
        assert hll.register_count == 1 << 14
        assert hll.is_sparse

    def test_min_precision(self) -> None:
        hll = HyperLogLogV2(precision=4)
        assert hll.precision == 4
        assert hll.register_count == 16
        assert hll.is_sparse

    def test_max_precision(self) -> None:
        hll = HyperLogLogV2(precision=18)
        assert hll.precision == 18
        assert hll.register_count == 1 << 18
        assert hll.is_sparse

    def test_below_min_precision_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLogV2(precision=3)

    def test_above_max_precision_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLogV2(precision=19)

    def test_precision_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLogV2(precision=0)

    def test_negative_precision_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLogV2(precision=-1)


class TestProperties:
    def test_is_sparse_initially_true(self) -> None:
        hll = HyperLogLogV2(precision=8)
        assert hll.is_sparse

    def test_error_bound_positive(self) -> None:
        hll = HyperLogLogV2(precision=10)
        assert hll.error_bound() > 0.0

    def test_error_bound_decreases_with_precision(self) -> None:
        e4 = HyperLogLogV2(precision=4).error_bound()
        e14 = HyperLogLogV2(precision=14).error_bound()
        assert e4 > e14


class TestAddSparse:
    def test_empty_count(self) -> None:
        hll = HyperLogLogV2(precision=8)
        assert hll.count() == 0

    def test_single_item_count(self) -> None:
        hll = HyperLogLogV2(precision=8)
        hll.add("foo")
        assert hll.count() >= 1

    def test_stays_sparse_below_threshold(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for i in range(10):
            hll.add(f"item_{i}")
        assert hll.is_sparse

    def test_cardinality_grows_with_items(self) -> None:
        hll = HyperLogLogV2(precision=8)
        counts = []
        for i in range(100):
            hll.add(f"item_{i}")
            counts.append(hll.count())
        assert counts[-1] >= counts[0]
        assert counts[-1] > 0

    def test_string_items(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for i in range(50):
            hll.add(f"str_{i}")
        assert 10 < hll.count() < 200

    def test_int_items(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for i in range(50):
            hll.add(i)
        assert 10 < hll.count() < 200

    def test_float_items(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for i in range(50):
            hll.add(float(i) + 0.5)
        assert 10 < hll.count() < 200

    def test_bytes_items(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for i in range(50):
            hll.add(f"bytes_{i}".encode())
        assert 10 < hll.count() < 200

    def test_mixed_types(self) -> None:
        hll = HyperLogLogV2(precision=8)
        hll.add("str")
        hll.add(42)
        hll.add(3.14)
        hll.add(b"bytes")
        assert hll.count() >= 1

    def test_duplicates_dont_inflate_count(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for _ in range(100):
            hll.add("same_item")
        assert hll.count() < 10


class TestSparseToDenseTransition:
    def test_transitions_to_dense_at_threshold(self) -> None:
        hll = HyperLogLogV2(precision=4)
        m = hll.register_count
        threshold = max(1, int(m * 0.15))
        for i in range(threshold + 5):
            hll.add(f"item_{i}")
        assert not hll.is_sparse

    def test_add_still_works_after_transition(self) -> None:
        hll = HyperLogLogV2(precision=4)
        threshold = max(1, int(16 * 0.15)) + 5
        for i in range(threshold):
            hll.add(f"item_{i}")
        assert not hll.is_sparse
        prev = hll.count()
        for i in range(threshold, threshold + 50):
            hll.add(f"more_{i}")
        assert hll.count() >= prev

    def test_count_consistent_across_transition(self) -> None:
        hll = HyperLogLogV2(precision=8)
        m = hll.register_count
        threshold = max(1, int(m * 0.15))
        for i in range(threshold):
            hll.add(f"pre_{i}")
        count_before = hll.count()
        assert count_before > 0
        for i in range(threshold, threshold + 10):
            hll.add(f"trigger_{i}")
        count_after = hll.count()
        assert count_after >= count_before


class TestCount:
    def test_count_hundred_items(self) -> None:
        hll = HyperLogLogV2(precision=10)
        n = 100
        for i in range(n):
            hll.add(f"item_{i}")
        c = hll.count()
        assert abs(c - n) / n < 2.0

    def test_count_thousand_items(self) -> None:
        hll = HyperLogLogV2(precision=10)
        n = 1000
        for i in range(n):
            hll.add(f"item_{i}")
        c = hll.count()
        assert abs(c - n) / n < 1.0

    def test_count_monotonic(self) -> None:
        hll = HyperLogLogV2(precision=8)
        prev = 0
        for i in range(200):
            hll.add(f"item_{i}")
            curr = hll.count()
            assert curr >= prev
            prev = curr

    def test_zero_count_empty(self) -> None:
        hll = HyperLogLogV2(precision=8)
        assert hll.count() == 0

    def test_count_is_int(self) -> None:
        hll = HyperLogLogV2(precision=8)
        hll.add("a")
        hll.add("b")
        hll.add("c")
        result = hll.count()
        assert isinstance(result, int)


class TestMerge:
    def test_merge_two_sparse(self) -> None:
        a = HyperLogLogV2(precision=8)
        b = HyperLogLogV2(precision=8)
        for i in range(30):
            a.add(f"a_{i}")
        for i in range(20, 50):
            b.add(f"b_{i}")
        a.merge(b)
        assert 35 < a.count() < 120

    def test_merge_sparse_into_sparse_below_threshold(self) -> None:
        a = HyperLogLogV2(precision=8)
        b = HyperLogLogV2(precision=8)
        for i in range(3):
            a.add(f"a_{i}")
        for i in range(2):
            b.add(f"b_{i}")
        assert a.is_sparse
        assert b.is_sparse
        a.merge(b)
        assert a.count() >= 3

    def test_merge_different_precision_raises(self) -> None:
        a = HyperLogLogV2(precision=8)
        b = HyperLogLogV2(precision=10)
        with pytest.raises(ValueError, match="cannot merge"):
            a.merge(b)

    def test_merge_sparse_into_dense(self) -> None:
        a = HyperLogLogV2(precision=8)
        b = HyperLogLogV2(precision=8)
        m = a.register_count
        threshold = max(1, int(m * 0.15)) + 10
        for i in range(threshold):
            a.add(f"a_{i}")
        assert not a.is_sparse
        for i in range(500, 510):
            b.add(f"b_{i}")
        assert b.is_sparse
        prev = a.count()
        a.merge(b)
        assert a.count() >= prev

    def test_merge_dense_into_sparse_transitions(self) -> None:
        a = HyperLogLogV2(precision=8)
        b = HyperLogLogV2(precision=8)
        for i in range(10):
            a.add(f"a_{i}")
        assert a.is_sparse
        m = b.register_count
        threshold = max(1, int(m * 0.15)) + 10
        for i in range(threshold):
            b.add(f"b_{i}")
        assert not b.is_sparse
        a.merge(b)
        assert not a.is_sparse

    def test_merge_dense_with_dense(self) -> None:
        a = HyperLogLogV2(precision=8)
        b = HyperLogLogV2(precision=8)
        m = a.register_count
        threshold = max(1, int(m * 0.15)) + 10
        for i in range(threshold):
            a.add(f"a_{i}")
        for i in range(500, threshold + 550):
            b.add(f"b_{i}")
        assert not a.is_sparse
        assert not b.is_sparse
        prev_a = a.count()
        prev_b = b.count()
        a.merge(b)
        assert a.count() >= max(prev_a, prev_b)

    def test_merge_idempotent(self) -> None:
        a = HyperLogLogV2(precision=8)
        for i in range(50):
            a.add(f"item_{i}")
        c1 = a.count()
        b = HyperLogLogV2(precision=8)
        for i in range(50):
            b.add(f"item_{i}")
        a.merge(b)
        c2 = a.count()
        assert abs(c2 - c1) / max(c1, 1) < 1.0


class TestSerialization:
    def test_roundtrip_sparse(self) -> None:
        hll = HyperLogLogV2(precision=8)
        for i in range(20):
            hll.add(f"item_{i}")
        assert hll.is_sparse
        raw = hll.to_bytes()
        restored = HyperLogLogV2.from_bytes(raw)
        assert restored.precision == hll.precision
        assert restored.is_sparse
        assert abs(restored.count() - hll.count()) <= 1

    def test_roundtrip_dense(self) -> None:
        hll = HyperLogLogV2(precision=4)
        threshold = max(1, int(16 * 0.15)) + 10
        for i in range(threshold):
            hll.add(f"item_{i}")
        assert not hll.is_sparse
        raw = hll.to_bytes()
        restored = HyperLogLogV2.from_bytes(raw)
        assert restored.precision == hll.precision
        assert not restored.is_sparse
        assert restored.count() == hll.count()

    def test_roundtrip_empty(self) -> None:
        hll = HyperLogLogV2(precision=8)
        raw = hll.to_bytes()
        restored = HyperLogLogV2.from_bytes(raw)
        assert restored.precision == 8
        assert restored.count() == 0

    def test_from_bytes_truncated_raises(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            HyperLogLogV2.from_bytes(b"short")

    def test_from_bytes_dense_mismatch_raises(self) -> None:
        hll = HyperLogLogV2(precision=4)
        threshold = max(1, int(16 * 0.15)) + 10
        for i in range(threshold):
            hll.add(f"item_{i}")
        raw = hll.to_bytes()
        bad_raw = raw[:-1]
        with pytest.raises(ValueError, match="register array length mismatch"):
            HyperLogLogV2.from_bytes(bad_raw)

    def test_to_bytes_sparse_structure(self) -> None:
        hll = HyperLogLogV2(precision=8)
        hll.add("a")
        hll.add("b")
        raw = hll.to_bytes()
        header_size = struct.calcsize("!4sBIIB")
        magic, version, precision, _m, is_sparse_flag = struct.unpack(
            "!4sBIIB", raw[:header_size]
        )
        assert magic == b"HLL2"
        assert version == hll.hash_domain_version == 2
        assert precision == hll.precision
        assert is_sparse_flag == 1
        count = struct.unpack("!I", raw[header_size : header_size + 4])[0]
        assert count == 2

    def test_legacy_payload_stays_in_legacy_hash_domain(self) -> None:
        precision = 8
        register_count = 1 << precision
        raw = struct.pack("!IIBI", precision, register_count, 1, 0)
        legacy = HyperLogLogV2.from_bytes(raw)
        current = HyperLogLogV2(precision=precision)
        assert legacy.hash_domain_version == 1
        assert HyperLogLogV2.from_bytes(legacy.to_bytes()).hash_domain_version == 1
        with pytest.raises(ValueError, match="hash domains"):
            current.merge(legacy)

    def test_unknown_hash_domain_is_rejected(self) -> None:
        raw = bytearray(HyperLogLogV2(precision=8).to_bytes())
        raw[4] = 99
        with pytest.raises(ValueError, match="hash domain"):
            HyperLogLogV2.from_bytes(bytes(raw))

    def test_single_item_roundtrip_count(self) -> None:
        hll = HyperLogLogV2(precision=8)
        hll.add("only_one")
        c1 = hll.count()
        raw = hll.to_bytes()
        restored = HyperLogLogV2.from_bytes(raw)
        assert abs(restored.count() - c1) <= 1


class TestInternalHelpers:
    def test_hash64_deterministic(self) -> None:
        h1 = HyperLogLogV2._hash64(b"hello")
        h2 = HyperLogLogV2._hash64(b"hello")
        assert h1 == h2

    def test_hash64_different_inputs(self) -> None:
        h1 = HyperLogLogV2._hash64(b"a")
        h2 = HyperLogLogV2._hash64(b"b")
        assert h1 != h2

    def test_fnv1a_64_deterministic(self) -> None:
        h1 = HyperLogLogV2._fnv1a_64(b"data")
        h2 = HyperLogLogV2._fnv1a_64(b"data")
        assert h1 == h2

    def test_compute_alpha_m16(self) -> None:
        assert HyperLogLogV2._compute_alpha(16) == 0.673

    def test_compute_alpha_m32(self) -> None:
        assert HyperLogLogV2._compute_alpha(32) == 0.697

    def test_compute_alpha_m64(self) -> None:
        assert HyperLogLogV2._compute_alpha(64) == 0.709

    def test_compute_alpha_general(self) -> None:
        a = HyperLogLogV2._compute_alpha(256)
        assert 0.6 < a < 0.8

    def test_rho_nonzero(self) -> None:
        hll = HyperLogLogV2(precision=14)
        r = hll._rho(0)
        assert r > 0

    def test_item_to_bytes_str(self) -> None:
        assert HyperLogLogV2._item_to_bytes("abc") == b"abc"

    def test_item_to_bytes_bytes(self) -> None:
        assert HyperLogLogV2._item_to_bytes(b"abc") == b"abc"

    def test_item_to_bytes_int(self) -> None:
        assert HyperLogLogV2._item_to_bytes(42) == b"42"

    def test_item_to_bytes_float(self) -> None:
        result = HyperLogLogV2._item_to_bytes(3.14)
        assert b"3.14" in result

    def test_sparse_max_entries(self) -> None:
        hll = HyperLogLogV2(precision=8)
        expected = max(1, int(256 * 0.15))
        assert hll._sparse_max_entries() == expected

    def test_transition_to_dense_already_dense_noop(self) -> None:
        hll = HyperLogLogV2(precision=4)
        threshold = max(1, int(16 * 0.15)) + 5
        for i in range(threshold):
            hll.add(f"item_{i}")
        assert not hll.is_sparse
        hll._transition_to_dense()
        assert not hll.is_sparse


class TestBiasCorrection:
    def test_bias_applied_for_known_precision(self) -> None:
        hll = HyperLogLogV2(precision=4)
        for i in range(5):
            hll.add(f"item_{i}")
        hll._transition_to_dense()
        raw = hll._raw_estimate()
        corrected = hll._apply_bias_correction(raw)
        assert corrected <= raw

    def test_bias_not_applied_for_unknown_precision(self) -> None:
        hll = HyperLogLogV2(precision=8)
        hll.add("a")
        raw = hll._raw_estimate()
        corrected = hll._apply_bias_correction(raw)
        assert corrected == raw
