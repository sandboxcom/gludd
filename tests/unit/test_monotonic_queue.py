"""Unit tests for monotonic queue primitives."""


from general_ludd.algorithms.monotonic_queue import (
    MaxQueue,
    MinQueue,
    MonotonicQueue,
    PriorityMonotonic,
    sliding_window_aggregate,
    sliding_window_maximum,
    sliding_window_minimum,
    windowed_stream,
)


class TestMonotonicQueue:
    def test_min_queue_front(self):
        q = MinQueue[int]()
        q.push(3)
        q.push(1)
        q.push(2)
        assert q.front() == 1
        assert len(q) == 2
        assert q.pop() == 1
        assert q.front() == 2

    def test_max_queue_front(self):
        q = MaxQueue[int]()
        q.push(1)
        q.push(3)
        q.push(2)
        assert q.front() == 3
        assert q.pop() == 3

    def test_pop_empty(self):
        q = MinQueue[int]()
        assert q.pop() is None

    def test_pop_until(self):
        q = MinQueue[int]()
        q.push(5, key=0)
        q.push(3, key=1)
        q.push(1, key=2)
        q.pop_until(1)
        assert q.front_with_key() == (2, 1)

    def test_custom_order(self):
        q = MonotonicQueue[int](order=lambda a, b: a > b)
        q.push(1)
        q.push(3)
        q.push(2)
        assert q.front() == 3

    def test_iter(self):
        q = MinQueue[int]()
        q.push(3)
        q.push(1)
        q.push(2)
        assert [v for _, v in q] == [1, 2]

    def test_bool(self):
        q = MinQueue[int]()
        assert not q
        q.push(1)
        assert q


class TestSlidingWindow:
    def test_maximum(self):
        assert sliding_window_maximum([1, 3, -1, -3, 5, 3, 6, 7], 3) == [3, 3, 5, 5, 6, 7]

    def test_minimum(self):
        assert sliding_window_minimum([1, 3, -1, -3, 5, 3, 6, 7], 3) == [-1, -3, -3, -3, 3, 3]

    def test_empty_or_invalid(self):
        assert sliding_window_maximum([], 3) == []
        assert sliding_window_maximum([1, 2], 0) == []
        assert sliding_window_minimum([], 3) == []

    def test_aggregate(self):
        assert sliding_window_aggregate([1, 2, 3, 4], 2, sum) == [3, 5, 7]


class TestPriorityMonotonic:
    def test_priority_tie_break(self):
        q = PriorityMonotonic[int]()
        q.push(5, priority=1)
        q.push(5, priority=2)
        assert q.front_priority() == (2, 5)

    def test_pop_until(self):
        q = PriorityMonotonic[int]()
        q.push(1)
        q.push(2)
        q.push(3)
        q.pop_until(1)
        assert q.front() in (2, 3)


class TestWindowedStream:
    def test_max_stream(self):
        result = windowed_stream([1, 3, 2, 5, 4], 3)
        assert result == [3, 5, 5]

    def test_min_stream(self):
        result = windowed_stream([1, 3, 2, 5, 4], 3, queue=MinQueue())
        assert result == [1, 2, 2]
