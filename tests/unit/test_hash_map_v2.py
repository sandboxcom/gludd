"""Unit tests for hash_map_v2 open-addressing hash maps."""

import pytest

from general_ludd.algorithms.hash_map_v2 import (
    LinearProbingHashMap,
    QuadraticProbingHashMap,
    RobinHoodHashMap,
    SwissHashMap,
)


class TestRobinHoodHashMap:
    def test_basic_crud(self):
        m: RobinHoodHashMap[str, int] = RobinHoodHashMap()
        assert len(m) == 0
        m["a"] = 1
        m["b"] = 2
        assert len(m) == 2
        assert m["a"] == 1
        assert m["b"] == 2
        assert "a" in m
        assert "z" not in m
        del m["a"]
        assert "a" not in m
        assert len(m) == 1
        with pytest.raises(KeyError):
            del m["a"]
        with pytest.raises(KeyError):
            m["a"]

    def test_update_existing_key(self):
        m: RobinHoodHashMap[str, int] = RobinHoodHashMap()
        m["a"] = 1
        m["a"] = 10
        assert m["a"] == 10
        assert len(m) == 1

    def test_get_default(self):
        m: RobinHoodHashMap[str, int] = RobinHoodHashMap()
        assert m.get("missing") is None
        assert m.get("missing", 42) == 42
        m["x"] = 5
        assert m.get("x") == 5

    def test_resize_triggers(self):
        m: RobinHoodHashMap[int, int] = RobinHoodHashMap(capacity=4)
        for i in range(20):
            m[i] = i * 2
        assert len(m) == 20
        for i in range(20):
            assert m[i] == i * 2

    def test_iteration_and_views(self):
        m: RobinHoodHashMap[str, int] = RobinHoodHashMap()
        m["a"] = 1
        m["b"] = 2
        assert sorted(m.keys()) == ["a", "b"]
        assert sorted(m.values()) == [1, 2]
        assert sorted(m.items()) == [("a", 1), ("b", 2)]
        assert sorted(iter(m)) == ["a", "b"]

    def test_repr(self):
        m: RobinHoodHashMap[str, int] = RobinHoodHashMap()
        m["a"] = 1
        assert "RobinHoodHashMap" in repr(m)


class TestSwissHashMap:
    def test_basic_crud(self):
        m: SwissHashMap[str, int] = SwissHashMap()
        m["a"] = 1
        m["b"] = 2
        assert len(m) == 2
        assert m["a"] == 1
        del m["a"]
        assert "a" not in m
        with pytest.raises(KeyError):
            m["a"]

    def test_tombstone_reuse(self):
        m: SwissHashMap[str, int] = SwissHashMap(capacity=4)
        m["a"] = 1
        m["b"] = 2
        del m["a"]
        m["c"] = 3
        assert len(m) == 2
        assert m["c"] == 3

    def test_resize(self):
        m: SwissHashMap[int, int] = SwissHashMap(capacity=4)
        for i in range(30):
            m[i] = i
        assert len(m) == 30

    def test_views(self):
        m: SwissHashMap[str, int] = SwissHashMap()
        m["x"] = 10
        m["y"] = 20
        assert sorted(m.keys()) == ["x", "y"]
        assert sorted(m.values()) == [10, 20]
        assert sorted(m.items()) == [("x", 10), ("y", 20)]

    def test_repr(self):
        m: SwissHashMap[str, int] = SwissHashMap()
        m["a"] = 1
        assert "SwissHashMap" in repr(m)


class TestLinearProbingHashMap:
    def test_basic_crud(self):
        m: LinearProbingHashMap[str, int] = LinearProbingHashMap()
        m["a"] = 1
        m["b"] = 2
        assert len(m) == 2
        assert m["a"] == 1
        del m["a"]
        assert "a" not in m
        with pytest.raises(KeyError):
            m["a"]

    def test_resize_and_collision(self):
        m: LinearProbingHashMap[int, int] = LinearProbingHashMap(capacity=4)
        for i in range(25):
            m[i] = i * 3
        assert len(m) == 25
        for i in range(25):
            assert m[i] == i * 3

    def test_views_and_iter(self):
        m: LinearProbingHashMap[str, int] = LinearProbingHashMap()
        m["x"] = 1
        m["y"] = 2
        assert sorted(m.keys()) == ["x", "y"]
        assert sorted(m.values()) == [1, 2]
        assert sorted(iter(m)) == ["x", "y"]

    def test_repr(self):
        m: LinearProbingHashMap[str, int] = LinearProbingHashMap()
        m["a"] = 1
        assert "LinearProbingHashMap" in repr(m)


class TestQuadraticProbingHashMap:
    def test_basic_crud(self):
        m: QuadraticProbingHashMap[str, int] = QuadraticProbingHashMap()
        m["a"] = 1
        m["b"] = 2
        assert len(m) == 2
        assert m["a"] == 1
        del m["a"]
        assert "a" not in m
        with pytest.raises(KeyError):
            m["a"]

    def test_resize(self):
        m: QuadraticProbingHashMap[int, int] = QuadraticProbingHashMap(capacity=4)
        for i in range(25):
            m[i] = i * 3
        assert len(m) == 25
        for i in range(25):
            assert m[i] == i * 3

    def test_views(self):
        m: QuadraticProbingHashMap[str, int] = QuadraticProbingHashMap()
        m["x"] = 1
        m["y"] = 2
        assert sorted(m.keys()) == ["x", "y"]
        assert sorted(m.values()) == [1, 2]

    def test_repr(self):
        m: QuadraticProbingHashMap[str, int] = QuadraticProbingHashMap()
        m["a"] = 1
        assert "QuadraticProbingHashMap" in repr(m)
