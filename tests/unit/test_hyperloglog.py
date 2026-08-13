"""Unit tests for HyperLogLog — covering gaps not in test_probabilistic_deep.py."""

from __future__ import annotations

import struct

import pytest

from general_ludd.probabilistic.hyperloglog import HyperLogLog


class TestInit:
    def test_default_precision(self) -> None:
        hll = HyperLogLog()
        assert hll.precision == 14
        assert hll.register_count == 1 << 14

    def test_min_precision(self) -> None:
        hll = HyperLogLog(precision=4)
        assert hll.precision == 4
        assert hll.register_count == 16

    def test_max_precision(self) -> None:
        hll = HyperLogLog(precision=18)
        assert hll.precision == 18
        assert hll.register_count == 1 << 18

    def test_below_min_precision_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLog(precision=3)

    def test_above_max_precision_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLog(precision=19)

    def test_precision_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLog(precision=0)

    def test_negative_precision_raises(self) -> None:
        with pytest.raises(ValueError, match="precision must be in"):
            HyperLogLog(precision=-1)


class TestProperties:
    def test_precision_returns_value(self) -> None:
        hll = HyperLogLog(precision=10)
        assert hll.precision == 10

    def test_register_count_matches_formula(self) -> None:
        for p in (4, 8, 10, 12, 14, 16, 18):
            hll = HyperLogLog(precision=p)
            assert hll.register_count == 1 << p

    def test_error_bound_decreases_with_precision(self) -> None:
        e4 = HyperLogLog(precision=4).error_bound()
        e14 = HyperLogLog(precision=14).error_bound()
        assert e4 > e14

    def test_error_bound_is_positive(self) -> None:
        hll = HyperLogLog(precision=10)
        assert hll.error_bound() > 0.0


class TestRho:
    def test_rho_zero(self) -> None:
        hll = HyperLogLog(precision=10)
        result = hll._rho(0)
        assert result == 64 - 10 + 1

    def test_rho_one(self) -> None:
        hll = HyperLogLog(precision=10)
        result = hll._rho(1)
        assert result == 54

    def test_rho_power_of_two(self) -> None:
        hll = HyperLogLog(precision=10)
        result = hll._rho(4)
        assert result == 52

    def test_rho_largest_remainder(self) -> None:
        hll = HyperLogLog(precision=14)
        result = hll._rho((1 << (64 - hll.precision)) - 1)
        assert result == 1


class TestComputeAlpha:
    def test_alpha_m16(self) -> None:
        assert HyperLogLog._compute_alpha(16) == 0.673

    def test_alpha_m32(self) -> None:
        assert HyperLogLog._compute_alpha(32) == 0.697

    def test_alpha_m64(self) -> None:
        assert HyperLogLog._compute_alpha(64) == 0.709

    def test_alpha_formula_large_m(self) -> None:
        a = HyperLogLog._compute_alpha(256)
        expected = 0.7213 / (1.0 + 1.079 / 256)
        assert a == pytest.approx(expected)

    def test_alpha_increases_with_m(self) -> None:
        a_small = HyperLogLog._compute_alpha(4)
        a_large = HyperLogLog._compute_alpha(1024)
        assert a_small < a_large


class TestFnv1a64:
    def test_known_empty(self) -> None:
        h = HyperLogLog._fnv1a_64(b"")
        assert h == 0xCBF29CE484222325

    def test_known_hello(self) -> None:
        h = HyperLogLog._fnv1a_64(b"hello")
        assert h == 0xA430D84680AABD0B

    def test_deterministic(self) -> None:
        a = HyperLogLog._fnv1a_64(b"test")
        b = HyperLogLog._fnv1a_64(b"test")
        assert a == b

    def test_different_inputs_different_hash(self) -> None:
        a = HyperLogLog._fnv1a_64(b"alpha")
        b = HyperLogLog._fnv1a_64(b"beta")
        assert a != b


class TestHash64:
    def test_hash64_deterministic(self) -> None:
        a = HyperLogLog._hash64(b"data")
        b = HyperLogLog._hash64(b"data")
        assert a == b

    def test_hash64_is_64bit(self) -> None:
        h = HyperLogLog._hash64(b"some_data")
        assert 0 <= h < (1 << 64)

    def test_hash64_different_for_different_keys(self) -> None:
        a = HyperLogLog._hash64(b"alpha")
        b = HyperLogLog._hash64(b"beta")
        assert a != b

    def test_hash64_empty_key(self) -> None:
        h = HyperLogLog._hash64(b"")
        assert 0 <= h < (1 << 64)


class TestItemToBytes:
    def test_string(self) -> None:
        assert HyperLogLog._item_to_bytes("hello") == b"hello"

    def test_bytes_passthrough(self) -> None:
        assert HyperLogLog._item_to_bytes(b"raw") == b"raw"

    def test_int(self) -> None:
        assert HyperLogLog._item_to_bytes(42) == b"42"

    def test_float(self) -> None:
        result = HyperLogLog._item_to_bytes(3.14)
        assert b"." in result

    def test_custom_object(self) -> None:
        class Custom:
            def __str__(self) -> str:
                return "custom_str"

        result = HyperLogLog._item_to_bytes(Custom())
        assert result == b"custom_str"

    def test_list_falls_through_to_str(self) -> None:
        result = HyperLogLog._item_to_bytes([1, 2, 3])
        assert isinstance(result, bytes)


class TestAddCount:
    def test_count_increases_with_items(self) -> None:
        hll = HyperLogLog(precision=10)
        hll.add("a")
        c1 = hll.count()
        hll.add("b")
        c2 = hll.count()
        assert c2 >= c1

    def test_count_monotonic(self) -> None:
        hll = HyperLogLog(precision=8)
        prev = hll.count()
        for i in range(100):
            hll.add(f"item_{i}")
            curr = hll.count()
            assert curr >= prev
            prev = curr

    def test_many_unique_items_approximate_count(self) -> None:
        hll = HyperLogLog(precision=14)
        n = 10000
        for i in range(n):
            hll.add(str(i))
        estimated = hll.count()
        error = abs(estimated - n) / n
        assert error < 0.10

    def test_add_none(self) -> None:
        hll = HyperLogLog(precision=8)
        hll.add(None)
        assert hll.count() == 1

    def test_add_boolean(self) -> None:
        hll = HyperLogLog(precision=8)
        hll.add(True)
        hll.add(False)
        assert hll.count() == 2


class TestMerge:
    def test_merge_self_no_change(self) -> None:
        hll = HyperLogLog(precision=10)
        for i in range(100):
            hll.add(f"item_{i}")
        c_before = hll.count()
        hll.merge(hll)
        assert hll.count() == c_before

    def test_merge_into_empty(self) -> None:
        a = HyperLogLog(precision=10)
        b = HyperLogLog(precision=10)
        for i in range(200):
            b.add(f"data_{i}")
        c_b = b.count()
        a.merge(b)
        assert a.count() >= c_b * 0.9

    def test_merge_empty_into_populated(self) -> None:
        a = HyperLogLog(precision=10)
        b = HyperLogLog(precision=10)
        for i in range(200):
            a.add(f"data_{i}")
        c_before = a.count()
        a.merge(b)
        assert a.count() == c_before

    def test_merge_idempotent(self) -> None:
        a = HyperLogLog(precision=10)
        b = HyperLogLog(precision=10)
        for i in range(100):
            a.add(f"xa_{i}")
        for i in range(150):
            b.add(f"xb_{i}")
        a.merge(b)
        c1 = a.count()
        for _i in range(5):
            a.merge(b)
        assert a.count() == c1

    def test_merge_different_precision_raises_alt_message(self) -> None:
        a = HyperLogLog(precision=8)
        b = HyperLogLog(precision=10)
        with pytest.raises(ValueError):
            a.merge(b)


class TestSerialization:
    def test_empty_roundtrip(self) -> None:
        hll = HyperLogLog(precision=8)
        raw = hll.to_bytes()
        restored = HyperLogLog.from_bytes(raw)
        assert restored.precision == 8
        assert restored.count() == 0

    def test_roundtrip_preserves_count(self) -> None:
        hll = HyperLogLog(precision=10)
        items = [f"data_{i}" for i in range(500)]
        for item in items:
            hll.add(item)
        raw = hll.to_bytes()
        restored = HyperLogLog.from_bytes(raw)
        assert restored.count() == hll.count()

    def test_to_bytes_starts_with_header(self) -> None:
        hll = HyperLogLog(precision=10)
        raw = hll.to_bytes()
        assert len(raw) >= 8

    def test_roundtrip_with_min_precision(self) -> None:
        hll = HyperLogLog(precision=4)
        for i in range(10):
            hll.add(f"x_{i}")
        raw = hll.to_bytes()
        restored = HyperLogLog.from_bytes(raw)
        assert restored.precision == 4
        assert restored.count() == hll.count()

    def test_roundtrip_with_max_precision(self) -> None:
        hll = HyperLogLog(precision=18)
        for i in range(100):
            hll.add(f"y_{i}")
        raw = hll.to_bytes()
        restored = HyperLogLog.from_bytes(raw)
        assert restored.precision == 18
        assert restored.count() == hll.count()

    def test_from_bytes_register_length_mismatch_raises(self) -> None:
        hll = HyperLogLog(precision=8)
        raw = hll.to_bytes()
        bad = raw[:-1]
        with pytest.raises(ValueError, match="register array length mismatch"):
            HyperLogLog.from_bytes(bad)

    def test_from_bytes_empty_data(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            HyperLogLog.from_bytes(b"")

    def test_from_bytes_partial_header(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            HyperLogLog.from_bytes(b"\x00\x00\x00\x04\x00\x00\x00")

    def test_serialized_payload_records_hash_domain(self) -> None:
        hll = HyperLogLog(precision=8)
        raw = hll.to_bytes()
        header_size = struct.calcsize("!4sBII")
        magic, version, precision, register_count = struct.unpack(
            "!4sBII", raw[:header_size]
        )
        assert magic == b"HLL1"
        assert version == hll.hash_domain_version == 2
        assert precision == 8
        assert register_count == 256

    def test_legacy_payload_stays_in_legacy_hash_domain(self) -> None:
        precision = 4
        register_count = 1 << precision
        raw = struct.pack("!II", precision, register_count) + bytes(register_count)
        legacy = HyperLogLog.from_bytes(raw)
        current = HyperLogLog(precision=precision)
        assert legacy.hash_domain_version == 1
        assert HyperLogLog.from_bytes(legacy.to_bytes()).hash_domain_version == 1
        with pytest.raises(ValueError, match="hash domains"):
            current.merge(legacy)

    def test_unknown_hash_domain_is_rejected(self) -> None:
        raw = bytearray(HyperLogLog(precision=4).to_bytes())
        raw[4] = 99
        with pytest.raises(ValueError, match="hash domain"):
            HyperLogLog.from_bytes(bytes(raw))


class TestSmallRangeCorrection:
    def test_small_count_triggers_correction(self) -> None:
        hll = HyperLogLog(precision=4)
        for i in range(5):
            hll.add(f"s_{i}")
        c = hll.count()
        assert c >= 1

    def test_empty_uses_correction_path(self) -> None:
        hll = HyperLogLog(precision=4)
        assert hll.count() == 0

    def test_single_item_precision4(self) -> None:
        hll = HyperLogLog(precision=4)
        hll.add("only")
        assert hll.count() == 1


class TestLargeRangeCorrection:
    def test_large_count_does_not_explode(self) -> None:
        hll = HyperLogLog(precision=14)
        for i in range(500_000):
            hll.add(str(i))
        c = hll.count()
        assert c > 0
        assert c < (1 << 32)


class TestLargerSetAccuracy:
    def test_500k_items_error_within_bound(self) -> None:
        hll = HyperLogLog(precision=14)
        n = 500_000
        for i in range(n):
            hll.add(f"big_{i}")
        estimated = hll.count()
        error = abs(estimated - n) / n
        bound = hll.error_bound()
        assert error < max(bound * 3.0, 0.03)

    def test_1k_items_low_precision(self) -> None:
        hll = HyperLogLog(precision=6)
        n = 1000
        for i in range(n):
            hll.add(str(i))
        estimated = hll.count()
        error = abs(estimated - n) / n
        assert error < 0.15
