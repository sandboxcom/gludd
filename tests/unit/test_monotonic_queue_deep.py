"""Deep monotonic queue / deque tests.

Covers: MonotonicQueue push/pop/front/back, MinQueue, MaxQueue,
sliding window min/max, SlidingWindow generic aggregate,
PriorityMonotonic priority ordering, pop_until expiry,
empty-queue guards, windowed_stream, edge cases, and
interleaved push/pop patterns.
"""

from __future__ import annotations

from src.general_ludd.algorithms.monotonic_queue import (
    MaxQueue,
    MinQueue,
    MonotonicQueue,
    PriorityMonotonic,
    sliding_window_aggregate,
    sliding_window_maximum,
    sliding_window_minimum,
    windowed_stream,
)

# ── MonotonicQueue (generic) ──────────────────────────────────────────────


class TestMonotonicQueueBasic:
    """Core push / pop / front / back behaviour."""

    def test_min_queue_preserves_front_minimum(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue(order=lambda a, b: a < b)
        for v in [5, 3, 8, 1, 9]:
            q.push(v)
        assert q.front() == 1

    def test_max_queue_preserves_front_maximum(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue(order=lambda a, b: a > b)
        for v in [5, 3, 8, 1, 9]:
            q.push(v)
        assert q.front() == 9

    def test_pop_returns_extremum(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue(order=lambda a, b: a < b)
        q.push(3)
        q.push(1)
        q.push(2)
        assert q.pop() == 1
        assert q.front() == 2

    def test_back_returns_most_recent_non_dominated(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue(order=lambda a, b: a > b)
        q.push(1)
        q.push(5)
        q.push(3)
        assert q.back() == 3

    def test_pop_until_evicts_by_key(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue(order=lambda a, b: a < b)
        for i, v in enumerate([4, 2, 6, 1, 7]):
            q.push(v, key=i)
        q.pop_until(2)
        assert q.front() == 1

    def test_empty_queue_guards(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue()
        assert q.front() is None
        assert q.back() is None
        assert q.pop() is None
        assert q.front_with_key() is None
        assert len(q) == 0
        assert not q

    def test_len_and_bool(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue()
        assert not q
        q.push(5)
        assert q
        assert len(q) == 1
        q.pop()
        assert not q

    def test_front_with_key(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue(order=lambda a, b: a < b)
        q.push(7, key=10)
        q.push(3, key=11)
        q.push(5, key=12)
        assert q.front_with_key() == (11, 3)


# ── MinQueue / MaxQueue ───────────────────────────────────────────────────


class TestMinQueue:
    def test_min_queue_simple(self) -> None:
        q = MinQueue[int]()
        q.push(10)
        q.push(5)
        q.push(8)
        q.push(2)
        assert q.front() == 2

    def test_min_queue_push_ascending(self) -> None:
        q = MinQueue[int]()
        for v in [1, 2, 3, 4, 5]:
            q.push(v)
        assert len(q) == 5
        assert q.front() == 1
        assert q.back() == 5


class TestMaxQueue:
    def test_max_queue_simple(self) -> None:
        q = MaxQueue[int]()
        q.push(10)
        q.push(5)
        q.push(12)
        q.push(3)
        assert q.front() == 12

    def test_max_queue_push_descending(self) -> None:
        q = MaxQueue[int]()
        for v in [5, 4, 3, 2, 1]:
            q.push(v)
        assert len(q) == 5
        assert list(int(v) for _, v in q) == [5, 4, 3, 2, 1]


# ── Sliding window ────────────────────────────────────────────────────────


class TestSlidingWindowMaximum:
    def test_basic(self) -> None:
        assert sliding_window_maximum([1, 3, -1, -3, 5, 3, 6, 7], 3) == [
            3,
            3,
            5,
            5,
            6,
            7,
        ]

    def test_k_equals_one(self) -> None:
        assert sliding_window_maximum([4, 2, 7, 1], 1) == [4, 2, 7, 1]

    def test_k_equals_length(self) -> None:
        assert sliding_window_maximum([3, 1, 4, 2], 4) == [4]

    def test_single_element(self) -> None:
        assert sliding_window_maximum([99], 1) == [99]

    def test_empty_input(self) -> None:
        assert sliding_window_maximum([], 3) == []

    def test_k_zero(self) -> None:
        assert sliding_window_maximum([1, 2, 3], 0) == []

    def test_k_greater_than_length(self) -> None:
        assert sliding_window_maximum([1, 2], 5) == []


class TestSlidingWindowMinimum:
    def test_basic(self) -> None:
        assert sliding_window_minimum([1, 3, -1, -3, 5, 3, 6, 7], 3) == [
            -1,
            -3,
            -3,
            -3,
            3,
            3,
        ]

    def test_k_equals_one(self) -> None:
        assert sliding_window_minimum([4, 2, 7, 1], 1) == [4, 2, 7, 1]

    def test_k_equals_length(self) -> None:
        assert sliding_window_minimum([3, 1, 4, 2], 4) == [1]

    def test_all_equal(self) -> None:
        assert sliding_window_minimum([5, 5, 5, 5], 2) == [5, 5, 5]

    def test_empty_input(self) -> None:
        assert sliding_window_minimum([], 3) == []

    def test_negative_values(self) -> None:
        assert sliding_window_minimum([-5, -2, -8, -1], 2) == [-5, -8, -8]


class TestSlidingWindowAggregate:
    def test_sum_aggregate(self) -> None:
        result = sliding_window_aggregate([1, 2, 3, 4, 5], 3, aggregate=sum)
        assert result == [6, 9, 12]

    def test_max_aggregate(self) -> None:
        result = sliding_window_aggregate([1, 5, 3, 7, 2], 3, aggregate=max)
        assert result == [5, 7, 7]

    def test_empty(self) -> None:
        assert sliding_window_aggregate([], 3, aggregate=sum) == []

    def test_k_zero(self) -> None:
        assert sliding_window_aggregate([1, 2, 3], 0, aggregate=sum) == []


# ── PriorityMonotonic ─────────────────────────────────────────────────────


class TestPriorityMonotonic:
    def test_higher_priority_dominates_equal_value(self) -> None:
        pq: PriorityMonotonic[int] = PriorityMonotonic(order=lambda a, b: a > b)
        pq.push(5, priority=0)
        pq.push(5, priority=10)
        assert pq.front_priority() == (10, 5)

    def test_value_order_overrides_priority_for_different_values(self) -> None:
        pq: PriorityMonotonic[int] = PriorityMonotonic(order=lambda a, b: a < b)
        pq.push(8, priority=100)
        pq.push(3, priority=1)
        assert pq.front() == 3

    def test_pop_return_value(self) -> None:
        pq: PriorityMonotonic[int] = PriorityMonotonic(order=lambda a, b: a > b)
        pq.push(10, 0)
        pq.push(1, 10)
        assert pq.pop() == 10
        assert pq.pop() == 1
        assert pq.pop() is None

    def test_pop_until_evicts_by_insertion_index(self) -> None:
        pq: PriorityMonotonic[int] = PriorityMonotonic(order=lambda a, b: a < b)
        pq.push(10)
        pq.push(5)
        pq.push(8)
        pq.push(1)
        pq.pop_until(1)
        assert pq.front() == 1

    def test_empty_priority_queue(self) -> None:
        pq: PriorityMonotonic[int] = PriorityMonotonic()
        assert pq.front() is None
        assert pq.front_priority() is None
        assert pq.pop() is None
        assert len(pq) == 0


# ── windowed_stream ───────────────────────────────────────────────────────


class TestWindowedStream:
    def test_max_stream(self) -> None:
        result = windowed_stream([1, 3, -1, -3, 5, 3, 6, 7], 3)
        assert result == [3, 3, 5, 5, 6, 7]

    def test_with_explicit_min_queue(self) -> None:
        mq: MinQueue[int] = MinQueue()
        result = windowed_stream([4, 1, 3, 0, 5], 3, queue=mq)
        assert result == [1, 0, 0]


# ── Edge / concurrency patterns ───────────────────────────────────────────


class TestEdgeCases:
    def test_duplicate_values(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue(order=lambda a, b: a < b)
        for v in [3, 3, 3]:
            q.push(v)
        assert q.front() == 3
        assert len(q) == 3

    def test_interleaved_push_pop(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue(order=lambda a, b: a < b)
        q.push(5)
        q.push(7)
        assert q.pop() == 5
        assert q.front() == 7
        q.push(3)
        assert q.pop() == 3

    def test_max_queue_interleaved(self) -> None:
        q: MaxQueue[int] = MaxQueue()
        q.push(5)
        q.push(10)
        q.push(7)
        assert q.front() == 10
        assert q.pop() == 10
        assert q.front() == 7

    def test_custom_order_stability(self) -> None:
        q: MonotonicQueue[int] = MonotonicQueue(order=lambda a, b: a > b)
        for v in [1, 5, 2, 8, 3]:
            q.push(v)
        assert list(int(v) for _, v in q) == [8, 3]

    def test_pop_until_no_op_on_fresh_queue(self) -> None:
        q = MinQueue[int]()
        q.push(10)
        q.pop_until(-1)  # key ≤ -1 evicts nothing
        assert q.front() == 10

    def test_windowed_stream_empty(self) -> None:
        assert windowed_stream([], 3) == []

    def test_windowed_stream_k_larger_than_input(self) -> None:
        assert windowed_stream([1, 2], 5) == []
