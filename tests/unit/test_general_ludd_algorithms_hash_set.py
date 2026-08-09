from __future__ import annotations

import pytest

from general_ludd.algorithms.hash_set import HashSet


class TestHashSet:
    def test_init_empty(self) -> None:
        s = HashSet[int]()
        assert len(s) == 0

    def test_init_from_iterable(self) -> None:
        s = HashSet[int]([1, 2, 3])
        assert len(s) == 3

    def test_add(self) -> None:
        s = HashSet[int]()
        s.add(1)
        s.add(2)
        assert len(s) == 2

    def test_add_duplicate(self) -> None:
        s = HashSet[int]()
        s.add(1)
        s.add(1)
        s.add(1)
        assert len(s) == 1

    def test_contains(self) -> None:
        s = HashSet[str]()
        s.add("hello")
        assert "hello" in s
        assert "world" not in s

    def test_remove(self) -> None:
        s = HashSet[int]()
        s.add(42)
        s.remove(42)
        assert len(s) == 0
        assert 42 not in s

    def test_remove_missing_raises(self) -> None:
        s = HashSet[int]()
        with pytest.raises(KeyError):
            s.remove(99)

    def test_discard(self) -> None:
        s = HashSet[int]()
        s.add(1)
        s.discard(1)
        assert len(s) == 0
        s.discard(999)

    def test_clear(self) -> None:
        s = HashSet[int]([1, 2, 3])
        s.clear()
        assert len(s) == 0

    def test_iter(self) -> None:
        s = HashSet[int]([3, 1, 2])
        assert sorted(s) == [1, 2, 3]

    def test_eq(self) -> None:
        s1 = HashSet[int]([1, 2, 3])
        s2 = HashSet[int]([3, 2, 1])
        s3 = HashSet[int]([1, 2])
        assert s1 == s2
        assert s1 != s3
        assert s1 != "not a set"

    def test_union(self) -> None:
        s1 = HashSet[int]([1, 2])
        s2 = HashSet[int]([2, 3])
        result = s1.union(s2)
        assert sorted(result) == [1, 2, 3]
        assert sorted(s1 | s2) == [1, 2, 3]

    def test_intersection(self) -> None:
        s1 = HashSet[int]([1, 2, 3])
        s2 = HashSet[int]([2, 3, 4])
        result = s1.intersection(s2)
        assert sorted(result) == [2, 3]
        assert sorted(s1 & s2) == [2, 3]

    def test_difference(self) -> None:
        s1 = HashSet[int]([1, 2, 3])
        s2 = HashSet[int]([2, 3, 4])
        result = s1.difference(s2)
        assert sorted(result) == [1]
        assert sorted(s1 - s2) == [1]

    def test_symmetric_difference(self) -> None:
        s1 = HashSet[int]([1, 2, 3])
        s2 = HashSet[int]([2, 3, 4])
        result = s1.symmetric_difference(s2)
        assert sorted(result) == [1, 4]

    def test_issubset(self) -> None:
        s1 = HashSet[int]([1, 2])
        s2 = HashSet[int]([1, 2, 3])
        assert s1.issubset(s2)
        assert s1 <= s2
        assert s1 < s2
        assert not s2.issubset(s1)

    def test_issuperset(self) -> None:
        s1 = HashSet[int]([1, 2, 3])
        s2 = HashSet[int]([1, 2])
        assert s1.issuperset(s2)
        assert s1 >= s2
        assert s1 > s2

    def test_isdisjoint(self) -> None:
        s1 = HashSet[int]([1, 2])
        s2 = HashSet[int]([3, 4])
        s3 = HashSet[int]([2, 3])
        assert s1.isdisjoint(s2)
        assert not s1.isdisjoint(s3)

    def test_update(self) -> None:
        s = HashSet[int]([1, 2])
        other = HashSet[int]([2, 3])
        s.update(other)
        assert sorted(s) == [1, 2, 3]

    def test_intersection_update(self) -> None:
        s = HashSet[int]([1, 2, 3])
        other = HashSet[int]([2, 3, 4])
        s.intersection_update(other)
        assert sorted(s) == [2, 3]

    def test_difference_update(self) -> None:
        s = HashSet[int]([1, 2, 3])
        other = HashSet[int]([2, 3, 4])
        s.difference_update(other)
        assert sorted(s) == [1]

    def test_copy(self) -> None:
        s = HashSet[int]([1, 2, 3])
        c = s.copy()
        assert s == c
        c.add(4)
        assert len(s) == 3
        assert len(c) == 4

    def test_capacity_and_size(self) -> None:
        s = HashSet[int]()
        assert s.capacity >= 8
        assert s.size == 0
        s.add(1)
        assert s.size == 1

    def test_resize_on_many_adds(self) -> None:
        s = HashSet[int]()
        for i in range(100):
            s.add(i)
        assert len(s) == 100
        for i in range(100):
            assert i in s

    def test_repr(self) -> None:
        s = HashSet[int]([1, 2])
        r = repr(s)
        assert "HashSet" in r
