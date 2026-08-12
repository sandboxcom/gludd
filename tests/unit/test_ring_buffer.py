"""Tests for src/general_ludd/ring_buffer.py"""

from __future__ import annotations

import copy

import pytest

from general_ludd.ring_buffer import RingBuffer


class TestRingBufferInit:
    def test_positive_capacity(self):
        rb = RingBuffer(5)
        assert rb.capacity == 5
        assert rb.size == 0

    def test_zero_capacity_raises(self):
        with pytest.raises(ValueError, match=">= 1"):
            RingBuffer(0)

    def test_negative_capacity_raises(self):
        with pytest.raises(ValueError, match=">= 1"):
            RingBuffer(-3)


class TestPush:
    def test_push_below_capacity(self):
        rb = RingBuffer(3)
        evicted = rb.push(1)
        assert evicted is None
        assert rb.size == 1

    def test_push_at_capacity_evicts(self):
        rb = RingBuffer(3)
        rb.push(1)
        rb.push(2)
        rb.push(3)
        evicted = rb.push(4)
        assert evicted == 1
        assert rb.size == 3

    def test_push_eviction_order(self):
        rb = RingBuffer(2)
        rb.push("a")
        rb.push("b")
        evicted = rb.push("c")
        assert evicted == "a"
        assert rb.snapshot() == ["b", "c"]


class TestPop:
    def test_pop_returns_oldest(self):
        rb = RingBuffer(3)
        rb.push(10)
        rb.push(20)
        assert rb.pop() == 10
        assert rb.size == 1

    def test_pop_from_empty_raises(self):
        rb = RingBuffer(5)
        with pytest.raises(IndexError, match="empty"):
            rb.pop()


class TestPeek:
    def test_peek_returns_oldest_without_removing(self):
        rb = RingBuffer(3)
        rb.push(100)
        rb.push(200)
        assert rb.peek() == 100
        assert rb.size == 2

    def test_peek_empty_raises(self):
        rb = RingBuffer(5)
        with pytest.raises(IndexError, match="empty"):
            rb.peek()


class TestEmptyFull:
    def test_is_empty_initially(self):
        assert RingBuffer(5).is_empty()

    def test_is_full(self):
        rb = RingBuffer(2)
        rb.push(1)
        rb.push(2)
        assert rb.is_full()


class TestClear:
    def test_clear(self):
        rb = RingBuffer(5)
        rb.push(1)
        rb.push(2)
        rb.clear()
        assert rb.is_empty()
        assert rb.size == 0


class TestSnapshot:
    def test_snapshot_returns_list(self):
        rb = RingBuffer(3)
        rb.push("x")
        rb.push("y")
        assert rb.snapshot() == ["x", "y"]

    def test_snapshot_is_copy(self):
        rb = RingBuffer(3)
        rb.push(1)
        snap = rb.snapshot()
        snap[0] = 999
        assert rb.snapshot()[0] == 1


class TestResize:
    def test_resize_smaller_truncates(self):
        rb = RingBuffer(5)
        for i in range(5):
            rb.push(i)
        rb.resize(3)
        assert rb.snapshot() == [2, 3, 4]
        assert rb.capacity == 3

    def test_resize_larger_preserves(self):
        rb = RingBuffer(3)
        rb.push(10)
        rb.push(20)
        rb.resize(5)
        assert rb.snapshot() == [10, 20]
        assert rb.capacity == 5

    def test_resize_same_noop(self):
        rb = RingBuffer(3)
        rb.push(1)
        rb.push(2)
        rb.resize(3)
        assert rb.snapshot() == [1, 2]

    def test_resize_invalid_raises(self):
        rb = RingBuffer(3)
        with pytest.raises(ValueError, match=">= 1"):
            rb.resize(0)


class TestDunderMethods:
    def test_len(self):
        rb = RingBuffer(5)
        rb.push(1)
        rb.push(2)
        assert len(rb) == 2

    def test_bool(self):
        rb = RingBuffer(3)
        assert not rb
        rb.push(1)
        assert rb

    def test_iter(self):
        rb = RingBuffer(3)
        rb.push("a")
        rb.push("b")
        assert list(rb) == ["a", "b"]

    def test_contains(self):
        rb = RingBuffer(3)
        rb.push(42)
        assert 42 in rb
        assert 99 not in rb

    def test_getitem(self):
        rb = RingBuffer(3)
        rb.push(10)
        rb.push(20)
        assert rb[0] == 10
        assert rb[1] == 20

    def test_getitem_out_of_range(self):
        rb = RingBuffer(3)
        with pytest.raises(IndexError):
            rb[0]

    def test_eq(self):
        rb1 = RingBuffer(3)
        rb2 = RingBuffer(3)
        rb1.push(1)
        rb2.push(1)
        assert rb1 == rb2

    def test_not_eq_different_content(self):
        rb1 = RingBuffer(3)
        rb2 = RingBuffer(3)
        rb1.push(1)
        rb2.push(2)
        assert rb1 != rb2

    def test_repr(self):
        rb = RingBuffer(3)
        rb.push(1)
        r = repr(rb)
        assert "RingBuffer" in r
        assert "capacity=3" in r
        assert "size=1" in r


class TestDeepcopy:
    def test_deepcopy_independence(self):
        rb = RingBuffer(3)
        rb.push([1, 2])
        rb2 = copy.deepcopy(rb)
        rb2.snapshot()[0].append(3)
        assert rb.snapshot() == [[1, 2]]
        assert rb2.snapshot() == [[1, 2, 3]]
