"""Unit tests for HashSet open-addressing set."""

import pytest

from general_ludd.algorithms.hash_set import HashSet


class TestHashSetInit:
    def test_empty(self):
        s = HashSet()
        assert len(s) == 0
        assert list(s) == []

    def test_from_iterable(self):
        s = HashSet([1, 2, 3, 2])
        assert len(s) == 3
        assert sorted(s) == [1, 2, 3]


class TestHashSetCrud:
    def test_add_and_contains(self):
        s = HashSet[int]()
        s.add(1)
        s.add(2)
        assert 1 in s
        assert 2 in s
        assert 3 not in s

    def test_add_duplicate(self):
        s = HashSet[int]()
        s.add(1)
        s.add(1)
        assert len(s) == 1

    def test_remove_and_discard(self):
        s = HashSet[int]()
        s.add(1)
        s.remove(1)
        assert 1 not in s
        s.discard(2)
        with pytest.raises(KeyError):
            s.remove(2)

    def test_clear(self):
        s = HashSet([1, 2, 3])
        s.clear()
        assert len(s) == 0
        assert 1 not in s

    def test_resize(self):
        s = HashSet[int]()
        for i in range(50):
            s.add(i)
        assert len(s) == 50
        for i in range(50):
            assert i in s


class TestHashSetOperations:
    def test_union(self):
        a = HashSet([1, 2, 3])
        b = HashSet([3, 4, 5])
        assert sorted(a.union(b)) == [1, 2, 3, 4, 5]
        assert sorted(a | b) == [1, 2, 3, 4, 5]

    def test_intersection(self):
        a = HashSet([1, 2, 3])
        b = HashSet([3, 4, 5])
        assert sorted(a.intersection(b)) == [3]
        assert sorted(a & b) == [3]

    def test_difference(self):
        a = HashSet([1, 2, 3])
        b = HashSet([3, 4, 5])
        assert sorted(a.difference(b)) == [1, 2]
        assert sorted(a - b) == [1, 2]

    def test_symmetric_difference(self):
        a = HashSet([1, 2, 3])
        b = HashSet([3, 4, 5])
        assert sorted(a.symmetric_difference(b)) == [1, 2, 4, 5]

    def test_subset_superset(self):
        a = HashSet([1, 2])
        b = HashSet([1, 2, 3])
        assert a.issubset(b)
        assert a <= b
        assert a < b
        assert b.issuperset(a)
        assert b >= a
        assert b > a

    def test_isdisjoint(self):
        a = HashSet([1, 2])
        b = HashSet([3, 4])
        assert a.isdisjoint(b)
        assert not a.isdisjoint(HashSet([2, 3]))

    def test_update(self):
        a = HashSet([1, 2])
        a.update(HashSet([2, 3]))
        assert sorted(a) == [1, 2, 3]

    def test_intersection_update(self):
        a = HashSet([1, 2, 3])
        a.intersection_update(HashSet([2, 3, 4]))
        assert sorted(a) == [2, 3]

    def test_difference_update(self):
        a = HashSet([1, 2, 3])
        a.difference_update(HashSet([2, 3, 4]))
        assert sorted(a) == [1]

    def test_copy(self):
        a = HashSet([1, 2, 3])
        b = a.copy()
        assert a == b
        b.add(4)
        assert a != b

    def test_eq_not_hashset(self):
        assert HashSet([1]) != [1]


class TestHashSetProperties:
    def test_capacity_and_size(self):
        s = HashSet([1, 2, 3])
        assert s.size == 3
        assert s.capacity >= 8

    def test_repr(self):
        assert "HashSet" in repr(HashSet([1, 2]))
