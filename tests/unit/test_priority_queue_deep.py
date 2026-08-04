"""Deep tests for generic PriorityQueue (min/max heap, stable ordering, custom comparator)."""

from __future__ import annotations

import pytest


class TestMinHeapBasic:
    def test_empty_queue_is_empty(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        assert pq.is_empty()
        assert len(pq) == 0

    def test_enqueue_single_pop_returns_same(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        pq.push("a", priority=5)
        assert not pq.is_empty()
        assert len(pq) == 1
        assert pq.peek() == "a"
        assert pq.pop() == "a"
        assert pq.is_empty()

    def test_enqueue_two_different_priorities_lower_first(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        pq.push("low", priority=10)
        pq.push("high", priority=1)
        assert pq.pop() == "high"
        assert pq.pop() == "low"

    def test_enqueue_same_priority_fifo_stable(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        pq.push("first", priority=5)
        pq.push("second", priority=5)
        pq.push("third", priority=5)
        assert pq.pop() == "first"
        assert pq.pop() == "second"
        assert pq.pop() == "third"

    def test_enqueue_many_out_of_order(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        priorities = [7, 3, 9, 1, 4, 6, 2, 8, 5, 0]
        for i, p in enumerate(priorities):
            pq.push(f"item-{i}", priority=p)
        result = [pq.pop() for _ in range(len(priorities))]
        assert result == [f"item-{i}" for i in [9, 3, 6, 1, 4, 8, 5, 0, 7, 2]]

    def test_peek_does_not_remove(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        pq.push("a", priority=3)
        pq.push("b", priority=1)
        assert pq.peek() == "b"
        assert len(pq) == 2
        assert pq.peek() == "b"

    def test_pop_empty_raises(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        with pytest.raises(IndexError):
            pq.pop()

    def test_peek_empty_raises(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        with pytest.raises(IndexError):
            pq.peek()

    def test_negative_priorities(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        pq.push("a", priority=-5)
        pq.push("b", priority=-10)
        pq.push("c", priority=0)
        assert pq.pop() == "b"
        assert pq.pop() == "a"
        assert pq.pop() == "c"

    def test_large_volume(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        for i in range(1000):
            pq.push(i, priority=1000 - i)
        for expected in range(999, -1, -1):
            assert pq.pop() == expected

    def test_duplicate_items_different_priorities(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        pq.push("dup", priority=3)
        pq.push("dup", priority=1)
        pq.push("unique", priority=2)
        assert pq.pop() == "dup"
        assert pq.pop() == "unique"
        assert pq.pop() == "dup"


class TestMaxHeap:
    def test_max_heap_pops_highest_first(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue(max_heap=True)
        pq.push("low", priority=2)
        pq.push("high", priority=9)
        pq.push("mid", priority=5)
        assert pq.pop() == "high"
        assert pq.pop() == "mid"
        assert pq.pop() == "low"

    def test_max_heap_same_priority_fifo(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue(max_heap=True)
        pq.push("a", priority=5)
        pq.push("b", priority=5)
        pq.push("c", priority=5)
        assert pq.pop() == "a"
        assert pq.pop() == "b"
        assert pq.pop() == "c"


class TestCustomComparator:
    def test_custom_key_function_on_item(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue(key=lambda item: len(item))
        pq.push("x")
        pq.push("hello")
        pq.push("ab")
        assert pq.pop() == "x"
        assert pq.pop() == "ab"
        assert pq.pop() == "hello"

    def test_custom_key_with_max_heap(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue(key=lambda item: len(item), max_heap=True)
        pq.push("x")
        pq.push("hello")
        pq.push("ab")
        assert pq.pop() == "hello"
        assert pq.pop() == "ab"
        assert pq.pop() == "x"

    def test_custom_key_string_length_reverse(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue(key=lambda s: -len(s))
        pq.push("a")
        pq.push("world")
        pq.push("xy")
        assert pq.pop() == "world"
        assert pq.pop() == "xy"
        assert pq.pop() == "a"


class TestPriorityInversion:
    def test_high_priority_inserted_after_low_still_pops_first(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        pq.push("medium-1", priority=5)
        pq.push("medium-2", priority=5)
        pq.push("urgent", priority=0)
        pq.push("medium-3", priority=5)
        assert pq.pop() == "urgent"
        assert pq.pop() == "medium-1"
        assert pq.pop() == "medium-2"
        assert pq.pop() == "medium-3"

    def test_min_then_max_priority_late_insert(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        for i in range(5):
            pq.push(f"mid-{i}", priority=50)
        pq.push("critical", priority=0)
        pq.push("lowest", priority=100)
        assert pq.pop() == "critical"
        result = [pq.pop() for _ in range(5)]
        assert all(r.startswith("mid-") for r in result)
        assert pq.pop() == "lowest"


class TestIterationAndStr:
    def test_bool_false_when_empty(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        assert not pq
        pq.push("a", priority=1)
        assert pq

    def test_repr_shows_length(self) -> None:
        from general_ludd.util.priority_queue import PriorityQueue

        pq = PriorityQueue()
        pq.push("a", priority=1)
        pq.push("b", priority=2)
        r = repr(pq)
        assert "2" in r
        assert "PriorityQueue" in r
