"""Deep binary heap tests: push/pop, heapify, min/max heap,
decrease-key, merge, heap sort verification. 18 tests.
"""

from __future__ import annotations

import heapq
import random
from typing import Any

import pytest

# ── Binary Heap Implementation ─────────────────────────────────────────────


class BinaryHeap:
    """Array-based binary heap supporting min and max modes."""

    def __init__(self, items: list[Any] | None = None, *, max_heap: bool = False) -> None:
        self._max_heap = max_heap
        self._heap: list[Any] = []
        if items:
            self._heap = list(items)
            if max_heap:
                self._heap = [_Negate(x) for x in self._heap]
            heapq.heapify(self._heap)

    def push(self, item: Any) -> None:
        if self._max_heap:
            heapq.heappush(self._heap, _Negate(item))
        else:
            heapq.heappush(self._heap, item)

    def pop(self) -> Any:
        if not self._heap:
            raise IndexError("pop from empty heap")
        val = heapq.heappop(self._heap)
        return val._value if self._max_heap and isinstance(val, _Negate) else val

    def peek(self) -> Any:
        if not self._heap:
            raise IndexError("peek on empty heap")
        val = self._heap[0]
        return val._value if self._max_heap and isinstance(val, _Negate) else val

    def decrease_key(self, idx: int, new_value: Any) -> None:
        if idx < 0 or idx >= len(self._heap):
            raise IndexError(f"index {idx} out of range [0, {len(self._heap)})")
        target: Any = _Negate(new_value) if self._max_heap else new_value
        if target >= self._heap[idx]:
            return
        self._heap[idx] = target
        self._sift_up(idx)

    def _sift_up(self, idx: int) -> None:
        while idx > 0:
            parent = (idx - 1) // 2
            if self._heap[idx] < self._heap[parent]:
                self._heap[idx], self._heap[parent] = self._heap[parent], self._heap[idx]
                idx = parent
            else:
                break

    def merge(self, other: BinaryHeap) -> None:
        if self._max_heap != other._max_heap:
            raise ValueError("cannot merge min-heap with max-heap")
        for item in other.to_list():
            self.push(item)

    def to_list(self) -> list[Any]:
        if self._max_heap:
            return sorted(x._value for x in self._heap if isinstance(x, _Negate))
        return sorted(self._heap)

    def delete(self, idx: int) -> Any:
        if idx < 0 or idx >= len(self._heap):
            raise IndexError(f"index {idx} out of range [0, {len(self._heap)})")
        if idx == len(self._heap) - 1:
            val = self._heap.pop()
            return val._value if self._max_heap and isinstance(val, _Negate) else val
        val = self._heap[idx]
        last = self._heap.pop()
        self._heap[idx] = last
        self._sift_down(idx)
        return val._value if self._max_heap and isinstance(val, _Negate) else val

    def _sift_down(self, idx: int) -> None:
        n = len(self._heap)
        while True:
            left = 2 * idx + 1
            right = 2 * idx + 2
            smallest = idx
            if left < n and self._heap[left] < self._heap[smallest]:
                smallest = left
            if right < n and self._heap[right] < self._heap[smallest]:
                smallest = right
            if smallest == idx:
                break
            self._heap[idx], self._heap[smallest] = self._heap[smallest], self._heap[idx]
            idx = smallest

    def __len__(self) -> int:
        return len(self._heap)

    def __bool__(self) -> bool:
        return len(self._heap) > 0

    def __repr__(self) -> str:
        return f"BinaryHeap({len(self._heap)} items, max_heap={self._max_heap})"


class _Negate:
    """Wrapper that negates comparison order for max-heap support."""

    __slots__ = ("_value",)

    def __init__(self, value: Any) -> None:
        self._value = value

    def __lt__(self, other: object) -> bool:
        if isinstance(other, _Negate):
            return self._value > other._value
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, _Negate):
            return self._value >= other._value
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, _Negate):
            return self._value < other._value
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        if isinstance(other, _Negate):
            return self._value <= other._value
        return NotImplemented

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _Negate):
            return self._value == other._value
        return NotImplemented

    def __repr__(self) -> str:
        return f"_Negate({self._value!r})"


# ── Min-Heap Tests ─────────────────────────────────────────────────────────


class TestMinHeapPushPop:
    def test_push_and_pop_single(self) -> None:
        h = BinaryHeap()
        h.push(10)
        assert len(h) == 1
        assert h.peek() == 10
        assert h.pop() == 10
        assert len(h) == 0

    def test_push_pop_multiple_min_order(self) -> None:
        h = BinaryHeap()
        for v in [5, 3, 8, 1, 9, 2]:
            h.push(v)
        result = [h.pop() for _ in range(6)]
        assert result == [1, 2, 3, 5, 8, 9]

    def test_push_pop_with_duplicates(self) -> None:
        h = BinaryHeap()
        for v in [4, 2, 4, 1, 2, 3]:
            h.push(v)
        result = [h.pop() for _ in range(6)]
        assert result == [1, 2, 2, 3, 4, 4]

    def test_pop_empty_raises(self) -> None:
        h = BinaryHeap()
        with pytest.raises(IndexError, match="empty"):
            h.pop()

    def test_peek_empty_raises(self) -> None:
        h = BinaryHeap()
        with pytest.raises(IndexError, match="empty"):
            h.peek()

    def test_peek_does_not_remove(self) -> None:
        h = BinaryHeap()
        h.push(7)
        h.push(3)
        assert h.peek() == 3
        assert len(h) == 2
        assert h.peek() == 3

    def test_push_pop_maintains_heap_invariant(self) -> None:
        h = BinaryHeap()
        random.seed(42)
        values = random.sample(range(1, 1001), 200)
        for v in values:
            h.push(v)
        result = []
        while h:
            result.append(h.pop())
        assert result == sorted(values)

    def test_string_items(self) -> None:
        h = BinaryHeap()
        for s in ["dog", "apple", "cat", "banana"]:
            h.push(s)
        assert h.pop() == "apple"
        assert h.pop() == "banana"
        assert h.pop() == "cat"
        assert h.pop() == "dog"

    def test_floating_point_items(self) -> None:
        h = BinaryHeap()
        for v in [3.14, 1.41, 2.72, 0.0, -1.5]:
            h.push(v)
        assert h.pop() == -1.5
        assert h.pop() == 0.0
        assert h.pop() == 1.41


class TestMinHeapHeapify:
    def test_heapify_empty_list(self) -> None:
        h = BinaryHeap([])
        assert len(h) == 0
        assert not h

    def test_heapify_from_list(self) -> None:
        h = BinaryHeap([10, 3, 7, 1, 14, 2])
        result = [h.pop() for _ in range(6)]
        assert result == [1, 2, 3, 7, 10, 14]

    def test_heapify_already_sorted(self) -> None:
        h = BinaryHeap([1, 2, 3, 4, 5])
        assert h.pop() == 1
        assert h.pop() == 2

    def test_heapify_reverse_sorted(self) -> None:
        h = BinaryHeap([5, 4, 3, 2, 1])
        assert h.pop() == 1
        assert h.pop() == 2


class TestMinHeapDecreaseKey:
    def test_decrease_key_moves_element_up(self) -> None:
        h = BinaryHeap([10, 20, 30, 40, 50])
        h.decrease_key(4, 5)
        assert h.peek() == 5

    def test_decrease_key_no_op_if_new_is_larger(self) -> None:
        h = BinaryHeap([10, 20, 30])
        h.decrease_key(0, 15)
        assert h.peek() == 10

    def test_decrease_key_out_of_range_raises(self) -> None:
        h = BinaryHeap([1, 2, 3])
        with pytest.raises(IndexError, match="out of range"):
            h.decrease_key(5, 0)
        with pytest.raises(IndexError, match="out of range"):
            h.decrease_key(-1, 0)

    def test_decrease_key_multiple_sifts(self) -> None:
        h = BinaryHeap([1, 5, 3, 10, 7, 8, 12])
        h.decrease_key(6, 2)
        assert h.peek() == 1
        result = [h.pop() for _ in range(7)]
        assert result == [1, 2, 3, 5, 7, 8, 10]


class TestMinHeapDelete:
    def test_delete_middle_element(self) -> None:
        h = BinaryHeap([5, 3, 8, 1, 4, 9])
        removed = h.delete(2)
        assert removed == 8
        result = [h.pop() for _ in range(5)]
        assert result == [1, 3, 4, 5, 9]

    def test_delete_last_element(self) -> None:
        h = BinaryHeap([1, 2, 3])
        removed = h.delete(2)
        assert removed == 3
        assert len(h) == 2

    def test_delete_out_of_range_raises(self) -> None:
        h = BinaryHeap([1, 2])
        with pytest.raises(IndexError, match="out of range"):
            h.delete(5)


class TestMinHeapMerge:
    def test_merge_two_heaps(self) -> None:
        h1 = BinaryHeap([5, 2, 8])
        h2 = BinaryHeap([3, 1, 9])
        h1.merge(h2)
        assert len(h1) == 6
        assert h1.pop() == 1
        assert h1.pop() == 2
        assert h1.pop() == 3

    def test_merge_empty_into_nonempty(self) -> None:
        h1 = BinaryHeap([7, 3])
        h2 = BinaryHeap()
        h1.merge(h2)
        assert len(h1) == 2
        assert h1.pop() == 3

    def test_merge_nonempty_into_empty(self) -> None:
        h1 = BinaryHeap()
        h2 = BinaryHeap([9, 1])
        h1.merge(h2)
        assert len(h1) == 2
        assert h1.pop() == 1

    def test_merge_empty_into_empty(self) -> None:
        h1 = BinaryHeap()
        h2 = BinaryHeap()
        h1.merge(h2)
        assert len(h1) == 0

    def test_merge_min_max_raises(self) -> None:
        h1 = BinaryHeap([1, 2])
        h2 = BinaryHeap([3, 4], max_heap=True)
        with pytest.raises(ValueError, match="min-heap with max-heap"):
            h1.merge(h2)


class TestMinHeapSortVerification:
    def test_heap_sort_correctness(self) -> None:
        random.seed(7)
        arr = random.sample(range(1, 5000), 500)
        h = BinaryHeap(arr)
        sorted_list = [h.pop() for _ in range(len(h))]
        assert sorted_list == sorted(arr)

    def test_heap_sort_preserves_element_counts(self) -> None:
        data = [7, 3, 7, 3, 1, 7]
        h = BinaryHeap(data)
        result = [h.pop() for _ in range(len(h))]
        assert result == sorted(data)
        assert result.count(7) == 3

    def test_bool_protocol(self) -> None:
        assert not BinaryHeap()
        h = BinaryHeap([1])
        assert h

    def test_repr(self) -> None:
        h = BinaryHeap([5, 1, 3])
        r = repr(h)
        assert "BinaryHeap" in r
        assert "3 items" in r


# ── Max-Heap Tests ──────────────────────────────────────────────────────────


class TestMaxHeapPushPop:
    def test_push_pop_max_order(self) -> None:
        h = BinaryHeap(max_heap=True)
        for v in [5, 3, 8, 1, 9, 2]:
            h.push(v)
        result = [h.pop() for _ in range(6)]
        assert result == [9, 8, 5, 3, 2, 1]

    def test_peek_max(self) -> None:
        h = BinaryHeap([10, 50, 30], max_heap=True)
        assert h.peek() == 50
        assert len(h) == 3

    def test_push_pop_max_with_duplicates(self) -> None:
        h = BinaryHeap(max_heap=True)
        for v in [5, 9, 5, 9, 1]:
            h.push(v)
        result = [h.pop() for _ in range(5)]
        assert result == [9, 9, 5, 5, 1]

    def test_heapify_max_reverse_sorted(self) -> None:
        h = BinaryHeap([1, 2, 3, 4, 5], max_heap=True)
        assert h.pop() == 5
        assert h.pop() == 4
        assert h.pop() == 3


class TestMaxHeapDecreaseKey:
    def test_decrease_key_max_heap(self) -> None:
        h = BinaryHeap([50, 40, 30, 20, 10], max_heap=True)
        h.decrease_key(4, 45)
        assert h.peek() == 50
        assert h.pop() == 50
        assert h.pop() == 45


class TestMaxHeapMerge:
    def test_merge_two_max_heaps(self) -> None:
        h1 = BinaryHeap([5, 2, 8], max_heap=True)
        h2 = BinaryHeap([3, 1, 9], max_heap=True)
        h1.merge(h2)
        assert len(h1) == 6
        assert h1.pop() == 9
        assert h1.pop() == 8
        assert h1.pop() == 5

    def test_merge_max_into_min_raises(self) -> None:
        h1 = BinaryHeap(max_heap=True)
        h2 = BinaryHeap([3, 4])
        with pytest.raises(ValueError, match="min-heap with max-heap"):
            h1.merge(h2)


class TestMaxHeapDelete:
    def test_delete_max_heap(self) -> None:
        h = BinaryHeap([30, 20, 10], max_heap=True)
        removed = h.delete(0)
        assert removed == 30
        result = [h.pop() for _ in range(2)]
        assert result == [20, 10]


# ── To-List Serialization ──────────────────────────────────────────────────


class TestToList:
    def test_to_list_min_heap(self) -> None:
        h = BinaryHeap([4, 1, 7, 3, 9])
        assert h.to_list() == [1, 3, 4, 7, 9]

    def test_to_list_max_heap(self) -> None:
        h = BinaryHeap([4, 1, 7, 3, 9], max_heap=True)
        assert h.to_list() == [1, 3, 4, 7, 9]


# ── Edge Cases ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_large_heap_stress(self) -> None:
        random.seed(99)
        values = list(range(10000))
        random.shuffle(values)
        h = BinaryHeap(values)
        result = [h.pop() for _ in range(len(h))]
        assert result == sorted(values)

    def test_negative_and_positive_mixed(self) -> None:
        h = BinaryHeap()
        for v in [-5, 10, -3, 7, 0, -1]:
            h.push(v)
        result = [h.pop() for _ in range(6)]
        assert result == [-5, -3, -1, 0, 7, 10]

    def test_interleaved_push_pop(self) -> None:
        h = BinaryHeap()
        h.push(5)
        h.push(2)
        assert h.pop() == 2
        h.push(1)
        h.push(7)
        assert h.pop() == 1
        h.push(3)
        assert h.pop() == 3
        assert h.pop() == 5
        assert h.pop() == 7
