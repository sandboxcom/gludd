"""Deep bitmap index tests: set/get, AND/OR/XOR/NOT, cardinality, serialization."""

from __future__ import annotations

import pytest

from general_ludd.storage.bitmap_index import BitmapIndex


class TestSetGet:
    def test_add_and_contains_single(self) -> None:
        bm = BitmapIndex()
        bm.add(42)
        assert bm.contains(42)
        assert 42 in bm

    def test_add_and_contains_many(self) -> None:
        bm = BitmapIndex()
        for v in (0, 1, 100, 65535, 65536, 100000):
            bm.add(v)
        for v in (0, 1, 100, 65535, 65536, 100000):
            assert v in bm

    def test_contains_missing(self) -> None:
        bm = BitmapIndex()
        assert bm.contains(999) is False
        assert 999 not in bm

    def test_add_negative_raises(self) -> None:
        bm = BitmapIndex()
        with pytest.raises(ValueError):
            bm.add(-1)

    def test_contains_negative_returns_false(self) -> None:
        bm = BitmapIndex()
        assert bm.contains(-5) is False

    def test_duplicate_add_no_double_count(self) -> None:
        bm = BitmapIndex()
        bm.add(7)
        bm.add(7)
        bm.add(7)
        assert bm.cardinality() == 1

    def test_remove_existing(self) -> None:
        bm = BitmapIndex()
        bm.add(42)
        bm.add(99)
        bm.remove(42)
        assert 42 not in bm
        assert 99 in bm

    def test_remove_last_element_clears_container(self) -> None:
        bm = BitmapIndex()
        bm.add(65536)
        bm.remove(65536)
        assert bm.is_empty()
        assert bm.container_count() == 0

    def test_remove_nonexistent_noop(self) -> None:
        bm = BitmapIndex()
        bm.add(10)
        bm.remove(999)
        assert 10 in bm
        assert len(bm) == 1

    def test_bulk_add(self) -> None:
        bm = BitmapIndex()
        bm.bulk_add([1, 2, 3, 100, 200, 300])
        for v in (1, 2, 3, 100, 200, 300):
            assert v in bm
        assert len(bm) == 6

    def test_from_iterable(self) -> None:
        bm = BitmapIndex.from_iterable([5, 10, 15, 20])
        assert len(bm) == 4
        assert 5 in bm and 10 in bm and 15 in bm and 20 in bm


class TestCardinality:
    def test_empty_cardinality_zero(self) -> None:
        assert len(BitmapIndex()) == 0

    def test_cardinality_after_adds(self) -> None:
        bm = BitmapIndex()
        for v in range(100):
            bm.add(v)
        assert bm.cardinality() == 100
        assert len(bm) == 100

    def test_cardinality_after_remove(self) -> None:
        bm = BitmapIndex()
        for v in range(50):
            bm.add(v)
        for v in range(25):
            bm.remove(v)
        assert bm.cardinality() == 25


class TestSetConversion:
    def test_to_set_empty(self) -> None:
        assert BitmapIndex().to_set() == set()

    def test_to_set_sparse(self) -> None:
        bm = BitmapIndex()
        bm.bulk_add([0, 100, 65536, 200000])
        assert bm.to_set() == {0, 100, 65536, 200000}

    def test_iter_yields_all(self) -> None:
        bm = BitmapIndex()
        bm.bulk_add([7, 77, 777])
        assert set(bm) == {7, 77, 777}


class TestLogicalOps:
    def test_and_intersection(self) -> None:
        a = BitmapIndex.from_iterable([1, 2, 3, 100])
        b = BitmapIndex.from_iterable([2, 3, 200])
        result = a & b
        assert result.to_set() == {2, 3}
        assert result.cardinality() == 2

    def test_and_empty_result(self) -> None:
        a = BitmapIndex.from_iterable([1, 2, 3])
        b = BitmapIndex.from_iterable([4, 5, 6])
        result = a & b
        assert result.is_empty()
        assert len(result) == 0

    def test_or_union(self) -> None:
        a = BitmapIndex.from_iterable([1, 2, 3])
        b = BitmapIndex.from_iterable([3, 4, 5])
        result = a | b
        assert result.to_set() == {1, 2, 3, 4, 5}
        assert result.cardinality() == 5

    def test_or_empty_operand(self) -> None:
        a = BitmapIndex.from_iterable([10, 20, 30])
        b = BitmapIndex()
        assert (a | b) == a
        assert (b | a) == a

    def test_xor_symmetric_difference(self) -> None:
        a = BitmapIndex.from_iterable([1, 2, 3, 4])
        b = BitmapIndex.from_iterable([3, 4, 5, 6])
        result = a ^ b
        assert result.to_set() == {1, 2, 5, 6}

    def test_xor_identical_yields_empty(self) -> None:
        a = BitmapIndex.from_iterable([10, 20, 30])
        assert (a ^ a).is_empty()

    def test_subtract_difference(self) -> None:
        a = BitmapIndex.from_iterable([1, 2, 3, 4, 5])
        b = BitmapIndex.from_iterable([2, 4])
        result = a - b
        assert result.to_set() == {1, 3, 5}
        assert result.cardinality() == 3

    def test_subtract_non_overlapping(self) -> None:
        a = BitmapIndex.from_iterable([1, 2, 3])
        b = BitmapIndex.from_iterable([4, 5, 6])
        assert (a - b) == a

    def test_not_invert_within_containers(self) -> None:
        a = BitmapIndex()
        a.add(0)
        a.add(1)
        inverted = ~a
        assert 0 not in inverted
        assert 1 not in inverted
        assert 2 in inverted

    def test_not_on_empty(self) -> None:
        bm = BitmapIndex()
        assert (~bm).is_empty()


class TestEquality:
    def test_equal_same_elements(self) -> None:
        a = BitmapIndex.from_iterable([1, 2, 3])
        b = BitmapIndex.from_iterable([1, 2, 3])
        assert a == b

    def test_not_equal_different_cardinality(self) -> None:
        a = BitmapIndex.from_iterable([1, 2])
        b = BitmapIndex.from_iterable([1, 2, 3])
        assert a != b

    def test_not_equal_same_cardinality_different_elements(self) -> None:
        a = BitmapIndex.from_iterable([1, 2, 3])
        b = BitmapIndex.from_iterable([4, 5, 6])
        assert a != b

    def test_not_equal_different_type(self) -> None:
        bm = BitmapIndex()
        assert bm != "not a bitmap"


class TestSerialization:
    def test_roundtrip_empty(self) -> None:
        bm = BitmapIndex()
        data = bm.to_bytes()
        restored = BitmapIndex.from_bytes(data)
        assert restored.is_empty()
        assert restored == bm

    def test_roundtrip_single_container(self) -> None:
        bm = BitmapIndex.from_iterable([1, 2, 3, 100, 200])
        data = bm.to_bytes()
        restored = BitmapIndex.from_bytes(data)
        assert restored == bm
        assert restored.cardinality() == bm.cardinality()

    def test_roundtrip_multi_container(self) -> None:
        bm = BitmapIndex()
        bm.bulk_add([1, 100, 65536, 65537, 131072, 200000])
        data = bm.to_bytes()
        restored = BitmapIndex.from_bytes(data)
        assert restored == bm
        assert restored.to_set() == bm.to_set()

    def test_roundtrip_large_sparse(self) -> None:
        bm = BitmapIndex()
        for v in range(0, 500000, 5000):
            bm.add(v)
        data = bm.to_bytes()
        restored = BitmapIndex.from_bytes(data)
        assert restored == bm

    def test_from_bytes_too_short_raises(self) -> None:
        with pytest.raises(ValueError):
            BitmapIndex.from_bytes(b"\x00\x00")

    def test_from_bytes_truncated_entries_raises(self) -> None:
        data = b"\x00\x00\x00\x02\x00\x01\x00"  # claims 2 containers, data for 0
        with pytest.raises(ValueError):
            BitmapIndex.from_bytes(data)


class TestUtility:
    def test_copy_independent(self) -> None:
        a = BitmapIndex.from_iterable([10, 20, 30])
        b = a.copy()
        b.add(40)
        assert 40 in b
        assert 40 not in a

    def test_clear(self) -> None:
        bm = BitmapIndex.from_iterable([1, 2, 3, 100])
        bm.clear()
        assert bm.is_empty()
        assert len(bm) == 0

    def test_is_empty(self) -> None:
        assert BitmapIndex().is_empty()
        bm = BitmapIndex()
        bm.add(1)
        assert not bm.is_empty()

    def test_container_count(self) -> None:
        bm = BitmapIndex()
        assert bm.container_count() == 0
        bm.add(0)
        assert bm.container_count() == 1
        bm.add(65536)
        assert bm.container_count() == 2


class TestCrossContainerOps:
    def test_and_across_containers(self) -> None:
        a = BitmapIndex.from_iterable([0, 65536, 131072])
        b = BitmapIndex.from_iterable([0, 65536, 99999])
        result = a & b
        assert result.to_set() == {0, 65536}

    def test_or_across_containers(self) -> None:
        a = BitmapIndex.from_iterable([0, 1])
        b = BitmapIndex.from_iterable([65536, 65537])
        result = a | b
        assert result.to_set() == {0, 1, 65536, 65537}
        assert result.container_count() == 2

    def test_subtract_across_containers(self) -> None:
        a = BitmapIndex.from_iterable([0, 65536, 131072])
        b = BitmapIndex.from_iterable([65536])
        result = a - b
        assert result.to_set() == {0, 131072}
