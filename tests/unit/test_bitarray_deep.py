"""Deep bit array / bitfield tests.

Tests for general_ludd.bitarray.BitArray: set, get, clear, toggle, count,
bitwise ops, serialization, range operations.
"""

from __future__ import annotations

import copy
from typing import cast

import pytest

from general_ludd.bitarray import BitArray

# ——— Construction ————————————————————————————————————————————————————————


class TestConstruction:
    def test_default_empty(self) -> None:
        ba = BitArray()
        assert len(ba) == 0

    def test_from_size(self) -> None:
        ba = BitArray(64)
        assert len(ba) == 64
        assert ba.count() == 0

    def test_from_iterable(self) -> None:
        ba = BitArray([True, False, True])
        assert len(ba) == 3
        assert ba[0] is True
        assert ba[1] is False
        assert ba[2] is True

    def test_invalid_source_type_fails_closed(self) -> None:
        source = cast(int, "101")
        with pytest.raises(TypeError, match="Invalid source type"):
            BitArray(source)

    def test_from_int(self) -> None:
        ba = BitArray.from_int(0b101, 3)
        assert ba[0] is True
        assert ba[1] is False
        assert ba[2] is True

    def test_from_bytes(self) -> None:
        ba = BitArray.from_bytes(b"\x05", 5)
        assert ba[0] is True
        assert ba[1] is False
        assert ba[2] is True
        assert ba[3] is False
        assert ba[4] is False

    def test_copy(self) -> None:
        b1 = BitArray([True, False, True])
        b2 = copy.copy(b1)
        b2[0] = False
        assert b1[0] is True
        assert b2[0] is False

    def test_str_repr(self) -> None:
        ba = BitArray([True, False, True])
        s = str(ba)
        assert "1" in s
        assert "0" in s

    def test_equality(self) -> None:
        b1 = BitArray([True, False, True])
        b2 = BitArray([True, False, True])
        b3 = BitArray([True, False, False])
        assert b1 == b2
        assert b1 != b3


# ——— Set / Get / Clear ————————————————————————————————————————————————————


class TestSetGetClear:
    def test_set_bit(self) -> None:
        ba = BitArray(4)
        ba.set(0)
        assert ba[0] is True
        ba.set(3)
        assert ba[3] is True

    def test_clear_bit(self) -> None:
        ba = BitArray([True, True, True, True])
        ba.clear(0)
        assert ba[0] is False
        ba.clear(3)
        assert ba[3] is False

    def test_get_oob_raises(self) -> None:
        ba = BitArray(4)
        with pytest.raises(IndexError):
            _ = ba[4]
        with pytest.raises(IndexError):
            _ = ba[-1]

    def test_set_oob_raises(self) -> None:
        ba = BitArray(4)
        with pytest.raises(IndexError):
            ba.set(4)

    def test_clear_oob_raises(self) -> None:
        ba = BitArray(4)
        with pytest.raises(IndexError):
            ba.clear(4)

    def test_set_all(self) -> None:
        ba = BitArray(8)
        ba.set_all()
        assert ba.count() == 8
        assert ba.all_set() is True

    def test_clear_all(self) -> None:
        ba = BitArray([True] * 8)
        ba.clear_all()
        assert ba.count() == 0
        assert ba.all_set() is False


# ——— Toggle ————————————————————————————————————————————————————————————————


class TestToggle:
    def test_toggle_single(self) -> None:
        ba = BitArray([True, False, True])
        ba.toggle(0)
        assert ba[0] is False
        ba.toggle(1)
        assert ba[1] is True
        ba.toggle(2)
        assert ba[2] is False

    def test_toggle_range(self) -> None:
        ba = BitArray([True, True, True, True])
        ba.toggle_range(1, 3)
        assert ba[0] is True
        assert ba[1] is False
        assert ba[2] is False
        assert ba[3] is True

    def test_toggle_range_empty(self) -> None:
        ba = BitArray([True, False])
        ba.toggle_range(0, 0)
        assert ba[0] is True
        assert ba[1] is False


# ——— Count / Popcount ——————————————————————————————————————————————————————


class TestCount:
    def test_count_empty(self) -> None:
        assert BitArray().count() == 0

    def test_count_all_zeros(self) -> None:
        assert BitArray(100).count() == 0

    def test_count_all_ones(self) -> None:
        ba = BitArray([True] * 255)
        assert ba.count() == 255

    def test_count_mixed(self) -> None:
        ba = BitArray([True, False, True, True, False, True])
        assert ba.count() == 4

    def test_count_large(self) -> None:
        ba = BitArray(1024)
        for i in range(0, 1024, 3):
            ba.set(i)
        assert ba.count() == 342

    def test_count_after_toggle(self) -> None:
        ba = BitArray([True] * 10)
        for i in range(5):
            ba.toggle(i)
        assert ba.count() == 5

    def test_first_set_none(self) -> None:
        assert BitArray(8).first_set() is None

    def test_first_set_found(self) -> None:
        ba = BitArray(8)
        ba.set(5)
        assert ba.first_set() == 5


# ——— Bitwise Operations ————————————————————————————————————————————————————


class TestBitwise:
    def test_and_op(self) -> None:
        b1 = BitArray([True, True, False, False])
        b2 = BitArray([True, False, True, False])
        r = b1 & b2
        assert r == BitArray([True, False, False, False])

    def test_or_op(self) -> None:
        b1 = BitArray([True, True, False, False])
        b2 = BitArray([True, False, True, False])
        r = b1 | b2
        assert r == BitArray([True, True, True, False])

    def test_xor_op(self) -> None:
        b1 = BitArray([True, True, False, False])
        b2 = BitArray([True, False, True, False])
        r = b1 ^ b2
        assert r == BitArray([False, True, True, False])

    def test_not_op(self) -> None:
        ba = BitArray([True, False, True, False])
        r = ~ba
        assert r == BitArray([False, True, False, True])

    def test_bitwise_length_mismatch_raises(self) -> None:
        b1 = BitArray(4)
        b2 = BitArray(8)
        with pytest.raises(ValueError, match="length"):
            _ = b1 & b2

    def test_bitwise_returns_new(self) -> None:
        b1 = BitArray([True, False])
        b2 = BitArray([False, True])
        r = b1 | b2
        b2[0] = True
        assert r[0] is True


# ——— Serialization ——————————————————————————————————————————————————————————


class TestSerialization:
    def test_to_int(self) -> None:
        ba = BitArray([True, False, True, True])
        assert ba.to_int() == 0b1101

    def test_to_int_empty(self) -> None:
        assert BitArray().to_int() == 0

    def test_to_bytes(self) -> None:
        ba = BitArray([True] * 8)
        assert ba.to_bytes() == b"\xff"

    def test_to_bytes_partial(self) -> None:
        ba = BitArray([True, False, False, False, False, False, False, False, True])
        assert ba.to_bytes() == b"\x01\x01"

    def test_to_binary_string(self) -> None:
        ba = BitArray([True, False, True])
        assert ba.to_binary_string() == "101"

    def test_roundtrip_bytes(self) -> None:
        data = b"\xaa\x55\xf0"
        ba = BitArray.from_bytes(data, len(data) * 8)
        assert ba.to_bytes() == data

    def test_roundtrip_int(self) -> None:
        for n in [0, 1, 0xDEAD, 0b10101010, 0xFFFF_FFFF]:
            ba = BitArray.from_int(n, max(1, n.bit_length()))
            assert ba.to_int() == n

    def test_roundtrip_binary_string(self) -> None:
        for s in ["", "0", "1", "10101010", "1111111100000000"]:
            ba = BitArray.from_binary_string(s) if s else BitArray()
            assert ba.to_binary_string() == s


# ——— Range Operations ———————————————————————————————————————————————————————


class TestRange:
    def test_set_range(self) -> None:
        ba = BitArray(8)
        ba.set_range(2, 5)
        assert ba[0] is False
        assert ba[1] is False
        assert ba[2] is True
        assert ba[3] is True
        assert ba[4] is True
        assert ba[5] is False

    def test_clear_range(self) -> None:
        ba = BitArray([True] * 8)
        ba.clear_range(1, 4)
        assert ba[0] is True
        assert ba[1] is False
        assert ba[2] is False
        assert ba[3] is False
        assert ba[4] is True

    def test_set_range_full(self) -> None:
        ba = BitArray(8)
        ba.set_range(0, 8)
        assert ba.count() == 8

    def test_set_range_ooo_reversed(self) -> None:
        ba = BitArray(8)
        ba.set_range(5, 2)
        assert ba.count() == 0

    def test_clear_range_empty(self) -> None:
        ba = BitArray([True] * 4)
        ba.clear_range(0, 0)
        assert ba.count() == 4

    def test_set_range_oob_raises(self) -> None:
        ba = BitArray(4)
        with pytest.raises(IndexError):
            ba.set_range(2, 5)


# ——— Edge Cases —————————————————————————————————————————————————————————————


class TestEdgeCases:
    def test_single_bit(self) -> None:
        ba = BitArray(1)
        ba.set(0)
        assert ba.count() == 1
        ba.toggle(0)
        assert ba.count() == 0

    def test_large_sparse(self) -> None:
        ba = BitArray(100_000)
        ba.set(0)
        ba.set(99_999)
        assert ba.count() == 2

    def test_wide_not(self) -> None:
        ba = BitArray([True] * 1000)
        r = ~ba
        assert r.count() == 0

    def test_iteration(self) -> None:
        ba = BitArray([True, False, True, False])
        bits = list(ba)
        assert bits == [True, False, True, False]

    def test_any_none(self) -> None:
        ba = BitArray([False, False, False])
        assert ba.any() is False
        ba.set(1)
        assert ba.any() is True

    def test_none(self) -> None:
        assert BitArray([False] * 10).none() is True
        assert BitArray([True]).none() is False

    def test_resize_grow(self) -> None:
        ba = BitArray([True, False])
        ba.resize(5)
        assert len(ba) == 5
        assert ba[0] is True
        assert ba[1] is False
        assert ba[2] is False

    def test_resize_shrink(self) -> None:
        ba = BitArray([True, False, True, False, True])
        ba.resize(3)
        assert len(ba) == 3
        assert ba[0] is True
        assert ba[1] is False
        assert ba[2] is True

    def test_iand(self) -> None:
        b1 = BitArray([True, False, True])
        b1 &= BitArray([False, False, True])
        assert b1 == BitArray([False, False, True])

    def test_ior(self) -> None:
        b1 = BitArray([True, False])
        b1 |= BitArray([False, True])
        assert b1 == BitArray([True, True])

    def test_ixor(self) -> None:
        b1 = BitArray([True, False])
        b1 ^= BitArray([True, True])
        assert b1 == BitArray([False, True])

    def test_contains(self) -> None:
        ba = BitArray([False, True, False])
        assert True in ba
        assert 1 not in ba
