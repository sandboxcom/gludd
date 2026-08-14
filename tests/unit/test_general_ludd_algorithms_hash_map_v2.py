from __future__ import annotations

import pytest

from general_ludd.algorithms.hash_map_v2 import (
    LinearProbingHashMap,
    QuadraticProbingHashMap,
    RobinHoodHashMap,
    SwissHashMap,
)


class TestRobinHoodHashMap:
    def test_init_empty(self) -> None:
        m = RobinHoodHashMap[str, int]()
        assert len(m) == 0

    def test_setitem_and_len(self) -> None:
        m = RobinHoodHashMap[str, int]()
        m["a"] = 1
        m["b"] = 2
        assert len(m) == 2

    def test_getitem(self) -> None:
        m = RobinHoodHashMap[str, int]()
        m["x"] = 42
        assert m["x"] == 42

    def test_getitem_missing_raises(self) -> None:
        m = RobinHoodHashMap[str, int]()
        with pytest.raises(KeyError):
            _ = m["missing"]

    def test_contains(self) -> None:
        m = RobinHoodHashMap[str, int]()
        m["k"] = 100
        assert "k" in m
        assert "nope" not in m

    def test_get_default(self) -> None:
        m = RobinHoodHashMap[str, int]()
        assert m.get("missing") is None
        assert m.get("missing", 99) == 99
        m["a"] = 1
        assert m.get("a") == 1

    def test_delitem(self) -> None:
        m = RobinHoodHashMap[str, int]()
        m["a"] = 1
        del m["a"]
        assert len(m) == 0
        assert "a" not in m

    def test_delitem_missing_raises(self) -> None:
        m = RobinHoodHashMap[str, int]()
        with pytest.raises(KeyError):
            del m["nope"]

    def test_items(self) -> None:
        m = RobinHoodHashMap[str, int]()
        m["x"] = 1
        m["y"] = 2
        items = sorted(m.items())
        assert items == [("x", 1), ("y", 2)]

    def test_keys(self) -> None:
        m = RobinHoodHashMap[str, int]()
        m["a"] = 1
        m["b"] = 2
        assert sorted(m.keys()) == ["a", "b"]

    def test_values(self) -> None:
        m = RobinHoodHashMap[str, int]()
        m["a"] = 10
        m["b"] = 20
        assert sorted(m.values()) == [10, 20]

    def test_update_existing(self) -> None:
        m = RobinHoodHashMap[str, int]()
        m["a"] = 1
        m["a"] = 99
        assert m["a"] == 99
        assert len(m) == 1

    def test_resize_triggers(self) -> None:
        m = RobinHoodHashMap[int, int](capacity=4)
        for i in range(20):
            m[i] = i
        assert len(m) == 20
        for i in range(20):
            assert m[i] == i

    def test_repr(self) -> None:
        m = RobinHoodHashMap[str, int]()
        m["x"] = 1
        r = repr(m)
        assert "RobinHoodHashMap" in r
        assert "x" in r

    def test_iter(self) -> None:
        m = RobinHoodHashMap[str, int]()
        m["a"] = 1
        m["b"] = 2
        assert sorted(m) == ["a", "b"]


class TestSwissHashMap:
    def test_init_empty(self) -> None:
        m = SwissHashMap[str, int]()
        assert len(m) == 0

    def test_setitem_and_getitem(self) -> None:
        m = SwissHashMap[str, int]()
        m["hello"] = 42
        assert m["hello"] == 42

    def test_contains(self) -> None:
        m = SwissHashMap[str, int]()
        m["k"] = 1
        assert "k" in m
        assert "z" not in m

    def test_get(self) -> None:
        m = SwissHashMap[str, int]()
        assert m.get("x") is None
        assert m.get("x", 5) == 5
        m["x"] = 10
        assert m.get("x") == 10

    def test_delitem(self) -> None:
        m = SwissHashMap[str, int]()
        m["a"] = 1
        m["b"] = 2
        del m["b"]
        assert "b" not in m
        assert len(m) == 1

    def test_delitem_missing_raises(self) -> None:
        m = SwissHashMap[str, int]()
        with pytest.raises(KeyError):
            del m["nope"]

    def test_items(self) -> None:
        m = SwissHashMap[str, int]()
        m["k1"] = 10
        m["k2"] = 20
        items = sorted(m.items())
        assert items == [("k1", 10), ("k2", 20)]

    def test_keys_and_values(self) -> None:
        m = SwissHashMap[str, int]()
        m["a"] = 1
        m["b"] = 2
        assert sorted(m.keys()) == ["a", "b"]
        assert sorted(m.values()) == [1, 2]

    def test_update_existing(self) -> None:
        m = SwissHashMap[str, int]()
        m["a"] = 1
        m["a"] = 100
        assert m["a"] == 100
        assert len(m) == 1

    def test_resize(self) -> None:
        m = SwissHashMap[int, int](capacity=4)
        for i in range(20):
            m[i] = i
        assert len(m) == 20
        for i in range(20):
            assert m[i] == i

    def test_control_sentinel_hashes_remain_occupied(self) -> None:
        m = SwissHashMap[int, str](capacity=4)

        # int hashes 0 and 126 yield the metadata bytes reserved for empty and
        # tombstone slots when the high-bit fingerprint encoding is applied.
        m[0] = "empty-byte hash"
        m[126] = "tombstone-byte hash"

        assert len(m) == 2
        assert m[0] == "empty-byte hash"
        assert m[126] == "tombstone-byte hash"

        m[1] = "one"
        m[2] = "two"

        assert len(m) == 4
        assert m[0] == "empty-byte hash"
        assert m[126] == "tombstone-byte hash"
        assert sorted(m.items()) == [
            (0, "empty-byte hash"),
            (1, "one"),
            (2, "two"),
            (126, "tombstone-byte hash"),
        ]

    def test_repr(self) -> None:
        m = SwissHashMap[str, int]()
        m["a"] = 1
        r = repr(m)
        assert "SwissHashMap" in r

    def test_iter(self) -> None:
        m = SwissHashMap[str, int]()
        m["a"] = 1
        m["b"] = 2
        assert sorted(m) == ["a", "b"]


class TestLinearProbingHashMap:
    def test_init_empty(self) -> None:
        m = LinearProbingHashMap[str, int]()
        assert len(m) == 0

    def test_basic_ops(self) -> None:
        m = LinearProbingHashMap[str, int]()
        m["a"] = 1
        assert m["a"] == 1
        assert "a" in m
        del m["a"]
        assert "a" not in m

    def test_get_default(self) -> None:
        m = LinearProbingHashMap[str, int]()
        assert m.get("x", -1) == -1

    def test_items(self) -> None:
        m = LinearProbingHashMap[str, int]()
        m["k"] = 100
        assert sorted(m.items()) == [("k", 100)]

    def test_resize(self) -> None:
        m = LinearProbingHashMap[int, int](capacity=4)
        for i in range(10):
            m[i] = i
        assert len(m) == 10
        assert m[5] == 5

    def test_repr(self) -> None:
        m = LinearProbingHashMap[str, int]()
        m["a"] = 1
        assert "LinearProbingHashMap" in repr(m)

    def test_update_value(self) -> None:
        m = LinearProbingHashMap[str, int]()
        m["a"] = 1
        m["a"] = 2
        assert m["a"] == 2
        assert len(m) == 1


class TestQuadraticProbingHashMap:
    def test_init_empty(self) -> None:
        m = QuadraticProbingHashMap[str, int]()
        assert len(m) == 0

    def test_basic_ops(self) -> None:
        m = QuadraticProbingHashMap[str, int]()
        m["x"] = 42
        assert m["x"] == 42
        assert "x" in m

    def test_getitem_missing(self) -> None:
        m = QuadraticProbingHashMap[str, int]()
        with pytest.raises(KeyError):
            _ = m["missing"]

    def test_get_default(self) -> None:
        m = QuadraticProbingHashMap[str, int]()
        assert m.get("z", 7) == 7
        m["z"] = 3
        assert m.get("z") == 3

    def test_delitem(self) -> None:
        m = QuadraticProbingHashMap[str, int]()
        m["a"] = 1
        del m["a"]
        assert len(m) == 0
        assert "a" not in m

    def test_delitem_missing(self) -> None:
        m = QuadraticProbingHashMap[str, int]()
        with pytest.raises(KeyError):
            del m["x"]

    def test_keys_and_values(self) -> None:
        m = QuadraticProbingHashMap[str, int]()
        m["a"] = 10
        m["b"] = 20
        assert sorted(m.keys()) == ["a", "b"]
        assert sorted(m.values()) == [10, 20]

    def test_resize(self) -> None:
        m = QuadraticProbingHashMap[int, int](capacity=4)
        for i in range(15):
            m[i] = i * 2
        assert len(m) == 15
        assert m[7] == 14

    def test_repr(self) -> None:
        m = QuadraticProbingHashMap[str, int]()
        m["a"] = 1
        assert "QuadraticProbingHashMap" in repr(m)

    def test_iter(self) -> None:
        m = QuadraticProbingHashMap[str, int]()
        m["x"] = 1
        assert list(m) == ["x"]
