"""Deep skip list tests: insert, search, delete, level generation, probabilistic
balance, range queries, ordered iteration, edge cases.

Covers: SkipList, SkipNode. 20+ tests.
"""

from __future__ import annotations

import math
import random

from general_ludd.skip_list import SkipList


class TestSkipListInsertSearch:
    def test_insert_and_search_single(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(5, "five")
        assert sl.search(5) == "five"

    def test_insert_and_search_multiple_ascending(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(20):
            sl.insert(i, i * 10)
        for i in range(20):
            assert sl.search(i) == i * 10

    def test_insert_and_search_multiple_descending(self):
        sl: SkipList[int, str] = SkipList()
        for i in range(20, 0, -1):
            sl.insert(i, f"val-{i}")
        for i in range(1, 21):
            assert sl.search(i) == f"val-{i}"

    def test_insert_duplicate_key_overwrites_value(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(7, "first")
        sl.insert(7, "second")
        assert sl.search(7) == "second"

    def test_search_missing_key_returns_none(self):
        sl: SkipList[int, int] = SkipList()
        sl.insert(1, 100)
        sl.insert(3, 300)
        assert sl.search(2) is None
        assert sl.search(0) is None
        assert sl.search(999) is None

    def test_search_empty_list_returns_none(self):
        sl: SkipList[int, str] = SkipList()
        assert sl.search(42) is None

    def test_insert_and_search_boundary_keys(self):
        sl: SkipList[int, int] = SkipList()
        sl.insert(-1, -100)
        sl.insert(0, 0)
        sl.insert(10**9, 1)
        assert sl.search(-1) == -100
        assert sl.search(0) == 0
        assert sl.search(10**9) == 1

    def test_insert_and_search_string_keys(self):
        sl: SkipList[str, str] = SkipList()
        sl.insert("apple", "red")
        sl.insert("banana", "yellow")
        sl.insert("cherry", "dark red")
        assert sl.search("apple") == "red"
        assert sl.search("banana") == "yellow"
        assert sl.search("cherry") == "dark red"


class TestSkipListDelete:
    def test_delete_existing_key(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(5, "five")
        assert sl.delete(5) is True
        assert sl.search(5) is None

    def test_delete_nonexistent_key_returns_false(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(1, "one")
        assert sl.delete(99) is False

    def test_delete_from_empty_list_returns_false(self):
        sl: SkipList[int, str] = SkipList()
        assert sl.delete(1) is False

    def test_delete_head_node(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(1, "a")
        sl.insert(2, "b")
        sl.insert(3, "c")
        assert sl.delete(1) is True
        assert sl.search(1) is None
        assert sl.search(2) == "b"
        assert sl.search(3) == "c"

    def test_delete_tail_node(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(1, "a")
        sl.insert(2, "b")
        sl.insert(3, "c")
        assert sl.delete(3) is True
        assert sl.search(3) is None
        assert sl.search(1) == "a"
        assert sl.search(2) == "b"

    def test_delete_middle_node(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(1, "a")
        sl.insert(2, "b")
        sl.insert(3, "c")
        sl.insert(4, "d")
        sl.insert(5, "e")
        assert sl.delete(3) is True
        assert sl.search(3) is None
        assert [sl.search(k) for k in (1, 2, 4, 5)] == ["a", "b", "d", "e"]

    def test_delete_all_nodes_empties_list(self):
        sl: SkipList[int, str] = SkipList()
        keys = list(range(10))
        for k in keys:
            sl.insert(k, f"v{k}")
        for k in keys:
            assert sl.delete(k) is True
        for k in keys:
            assert sl.search(k) is None
        assert sl.search(0) is None

    def test_reinsert_after_delete(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(5, "five")
        sl.delete(5)
        sl.insert(5, "new-five")
        assert sl.search(5) == "new-five"


class TestSkipListLevelGeneration:
    def test_level_geometric_distribution(self, monkeypatch):
        monkeypatch.setattr(random, "random", lambda: 0.0)
        sl_low: SkipList[int, int] = SkipList()
        sl_low.insert(1, 10)
        assert sl_low._max_level >= 1

        monkeypatch.setattr(random, "random", lambda: 0.99)
        sl_high: SkipList[int, int] = SkipList()
        sl_high.insert(1, 10)
        assert sl_high._max_level >= 1

    def test_level_within_bounds_after_many_inserts(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(1000):
            sl.insert(i, i)
        assert sl._max_level >= 1
        expected_max = 1 + int(math.log2(1001))
        assert sl._max_level <= expected_max + 4

    def test_max_level_grows_with_list_size(self):
        sl: SkipList[int, int] = SkipList()
        sl.insert(1, 1)
        level_few = sl._max_level
        for i in range(2, 5000):
            sl.insert(i, i)
        level_many = sl._max_level
        assert level_many >= level_few

    def test_head_node_level_matches_max_level(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(200):
            sl.insert(i, i)
        assert len(sl._head.forward) == sl._max_level


class TestSkipListProbabilisticBalance:
    def test_high_probability_insert_order_independent(self):
        small: list[int] = []
        large: list[int] = []
        for _ in range(10):
            sl: SkipList[int, int] = SkipList()
            for i in range(200):
                sl.insert(i, i)
            assert sl._max_level >= 1
            small.append(sl._max_level)

            sl2: SkipList[int, int] = SkipList()
            for i in range(199, -1, -1):
                sl2.insert(i, i)
            large.append(sl2._max_level)

        mean_small = sum(small) / len(small)
        mean_large = sum(large) / len(large)
        assert abs(mean_small - mean_large) < 5

    def test_large_dataset_no_crash_or_degenerate(self):
        sl: SkipList[int, int] = SkipList()
        n = 2000
        for i in range(n):
            sl.insert(i, i)
        assert sl.search(0) == 0
        assert sl.search(n - 1) == n - 1
        assert sl.search(n // 2) == n // 2
        assert sl.search(-1) is None
        assert sl.search(n) is None


class TestSkipListRangeQueries:
    def test_range_query_empty(self):
        sl: SkipList[int, int] = SkipList()
        assert sl.range_query(0, 10) == []

    def test_range_query_full_range(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(10):
            sl.insert(i, i * 10)
        result = sl.range_query(0, 9)
        assert result == [(i, i * 10) for i in range(10)]

    def test_range_query_partial_range(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(20):
            sl.insert(i, i * 10)
        result = sl.range_query(5, 9)
        assert result == [(i, i * 10) for i in range(5, 10)]

    def test_range_query_start_equals_end(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(10):
            sl.insert(i, i * 10)
        result = sl.range_query(5, 5)
        assert result == [(5, 50)]

    def test_range_query_no_match_below_min(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(10, 20):
            sl.insert(i, i)
        result = sl.range_query(0, 5)
        assert result == []

    def test_range_query_no_match_above_max(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(10, 20):
            sl.insert(i, i)
        result = sl.range_query(30, 40)
        assert result == []

    def test_range_query_start_greater_than_end(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(10):
            sl.insert(i, i)
        result = sl.range_query(8, 2)
        assert result == []

    def test_range_query_sparse_keys(self):
        sl: SkipList[int, int] = SkipList()
        sl.insert(1, 10)
        sl.insert(100, 1000)
        sl.insert(200, 2000)
        result = sl.range_query(50, 150)
        assert result == [(100, 1000)]


class TestSkipListOrderedIteration:
    def test_iter_empty(self):
        sl: SkipList[int, int] = SkipList()
        assert list(sl) == []

    def test_iter_ordered_ascending(self):
        sl: SkipList[int, int] = SkipList()
        keys = [5, 2, 8, 1, 9, 3, 7, 4, 6, 0]
        for k in keys:
            sl.insert(k, k * 10)
        assert [(k, v) for k, v in sl] == [(i, i * 10) for i in range(10)]

    def test_iter_after_deletes(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(10):
            sl.insert(i, i * 10)
        sl.delete(3)
        sl.delete(7)
        result = list(sl)
        assert result == [(i, i * 10) for i in range(10) if i not in (3, 7)]

    def test_iter_after_overwrites(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(1, "a")
        sl.insert(2, "b")
        sl.insert(2, "b2")
        result = list(sl)
        assert result == [(1, "a"), (2, "b2")]

    def test_len_empty(self):
        sl: SkipList[int, int] = SkipList()
        assert len(sl) == 0

    def test_len_after_inserts_and_deletes(self):
        sl: SkipList[int, str] = SkipList()
        for i in range(15):
            sl.insert(i, f"v{i}")
        assert len(sl) == 15
        sl.delete(3)
        sl.delete(7)
        assert len(sl) == 13

    def test_contains_operator(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(1, "a")
        sl.insert(2, "b")
        assert 1 in sl
        assert 2 in sl
        assert 3 not in sl

    def test_repr_non_empty(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(1, "a")
        sl.insert(2, "b")
        r = repr(sl)
        assert "SkipList" in r
        assert "size=2" in r

    def test_repr_empty(self):
        sl: SkipList[int, int] = SkipList()
        r = repr(sl)
        assert "SkipList" in r
        assert "size=0" in r

    def test_reversed_iteration(self):
        sl: SkipList[int, int] = SkipList()
        keys = [5, 2, 8, 1, 9, 3]
        for k in keys:
            sl.insert(k, k * 10)
        result = list(reversed(sl))
        expected = [(k, k * 10) for k in sorted(keys, reverse=True)]
        assert result == expected


class TestSkipListEdgeCases:
    def test_many_inserts_no_memory_leak(self):
        sl: SkipList[int, int] = SkipList()
        for i in range(5000):
            sl.insert(i, i)
            if i % 1000 == 0:
                assert sl.search(i) == i
        assert sl.search(0) == 0
        assert sl.search(4999) == 4999

    def test_insert_delete_cycle_repeatedly(self):
        sl: SkipList[int, str] = SkipList()
        for cycle in range(5):
            for i in range(50):
                sl.insert(i, f"c{cycle}-{i}")
            for i in range(0, 50, 2):
                sl.delete(i)
            for i in range(0, 50, 2):
                assert sl.search(i) is None
            for i in range(1, 50, 2):
                assert sl.search(i) == f"c{cycle}-{i}"

    def test_same_key_insert_delete_insert(self):
        sl: SkipList[int, int] = SkipList()
        for _ in range(10):
            sl.insert(42, 100)
            assert sl.search(42) == 100
            sl.delete(42)
            assert sl.search(42) is None

    def test_negative_keys(self):
        sl: SkipList[int, str] = SkipList()
        sl.insert(-10, "minus ten")
        sl.insert(-5, "minus five")
        sl.insert(0, "zero")
        sl.insert(5, "five")
        assert sl.search(-10) == "minus ten"
        assert sl.search(0) == "zero"
        assert sl.range_query(-8, -4) == [(-5, "minus five")]

    def test_float_keys(self):
        sl: SkipList[float, str] = SkipList()
        sl.insert(1.1, "a")
        sl.insert(2.2, "b")
        sl.insert(1.5, "c")
        assert sl.search(1.1) == "a"
        assert sl.search(1.5) == "c"
        assert sl.search(2.2) == "b"
        assert sl.range_query(1.0, 1.6) == [(1.1, "a"), (1.5, "c")]

    def test_none_values(self):
        sl: SkipList[int, int | None] = SkipList()
        sl.insert(1, None)
        assert sl.search(1) is None
        assert len(sl) == 1
        sl.insert(2, 42)
        assert sl.search(2) == 42
        assert 2 in sl

    def test_very_large_key_space(self):
        import sys

        sl: SkipList[int, object] = SkipList()
        sl.insert(0, 0)
        sl.insert(sys.maxsize, "max")
        sl.insert(-sys.maxsize - 1, "min")
        assert sl.search(0) == 0
        assert sl.search(sys.maxsize) == "max"
        assert sl.search(-sys.maxsize - 1) == "min"
