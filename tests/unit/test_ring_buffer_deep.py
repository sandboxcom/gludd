"""Deep tests for the RingBuffer class — push/pop, wrap-around, empty/full,
iteration, snapshot, resize, container protocols, and edge cases.
"""

from __future__ import annotations

import copy

import pytest

from general_ludd.ring_buffer import RingBuffer


class TestConstruction:
    def test_default_construction(self) -> None:
        buf = RingBuffer(4)
        assert buf.capacity == 4
        assert buf.size == 0
        assert buf.is_empty()
        assert not buf.is_full()

    def test_capacity_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            RingBuffer(0)
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            RingBuffer(-1)


class TestPushPop:
    def test_push_increases_size(self) -> None:
        buf = RingBuffer(5)
        buf.push("a")
        assert buf.size == 1
        buf.push("b")
        assert buf.size == 2

    def test_pop_returns_oldest(self) -> None:
        buf = RingBuffer(10)
        buf.push("a")
        buf.push("b")
        buf.push("c")
        assert buf.pop() == "a"
        assert buf.pop() == "b"
        assert buf.pop() == "c"

    def test_pop_from_empty_raises(self) -> None:
        buf = RingBuffer(3)
        with pytest.raises(IndexError, match="pop from empty ring buffer"):
            buf.pop()

    def test_push_pop_fifo_order(self) -> None:
        buf = RingBuffer(100)
        for i in range(50):
            buf.push(i)
        for i in range(50):
            assert buf.pop() == i
        assert buf.is_empty()


class TestWrapAround:
    def test_evicts_oldest_when_full(self) -> None:
        buf = RingBuffer(3)
        buf.push(1)
        buf.push(2)
        buf.push(3)
        # full — next push overwrites oldest
        evicted = buf.push(4)
        assert evicted == 1
        assert list(buf) == [2, 3, 4]

    def test_push_returns_none_while_not_full(self) -> None:
        buf = RingBuffer(3)
        assert buf.push(1) is None
        assert buf.push(2) is None
        assert buf.push(3) is None

    def test_evicted_always_oldest(self) -> None:
        buf = RingBuffer(2)
        assert buf.push("x") is None
        assert buf.push("y") is None
        assert buf.push("z") == "x"
        assert buf.push("w") == "y"
        assert list(buf) == ["z", "w"]

    def test_multiple_wraps_retains_correct_order(self) -> None:
        buf = RingBuffer(3)
        for i in range(10):
            buf.push(i)
        # capacity=3, so last 3: [7,8,9]
        assert list(buf) == [7, 8, 9]
        assert buf.size == 3


class TestEmptyFull:
    def test_empty_after_clear(self) -> None:
        buf = RingBuffer(4)
        for i in range(4):
            buf.push(i)
        buf.clear()
        assert buf.is_empty()
        assert buf.size == 0
        assert len(buf) == 0
        assert not buf

    def test_full_after_filling(self) -> None:
        buf = RingBuffer(3)
        assert not buf.is_full()
        for i in range(3):
            buf.push(i)
        assert buf.is_full()
        assert buf.size == buf.capacity

    def test_not_full_after_pop(self) -> None:
        buf = RingBuffer(3)
        for i in range(3):
            buf.push(i)
        assert buf.is_full()
        buf.pop()
        assert not buf.is_full()

    def test_bool_false_when_empty(self) -> None:
        assert not RingBuffer(5)

    def test_bool_true_when_non_empty(self) -> None:
        buf = RingBuffer(5)
        buf.push(1)
        assert buf


class TestPeek:
    def test_peek_returns_oldest_without_removal(self) -> None:
        buf = RingBuffer(10)
        buf.push("a")
        buf.push("b")
        assert buf.peek() == "a"
        assert buf.size == 2

    def test_peek_empty_raises(self) -> None:
        buf = RingBuffer(5)
        with pytest.raises(IndexError, match="peek from empty ring buffer"):
            buf.peek()


class TestIteration:
    def test_iter_yields_in_fifo_order(self) -> None:
        buf = RingBuffer(5)
        for x in "abcde":
            buf.push(x)
        assert list(iter(buf)) == ["a", "b", "c", "d", "e"]

    def test_empty_iterator_yields_nothing(self) -> None:
        buf = RingBuffer(3)
        assert list(buf) == []

    def test_iter_after_wrap(self) -> None:
        buf = RingBuffer(3)
        for x in "abcde":
            buf.push(x)
        assert list(buf) == ["c", "d", "e"]


class TestSnapshot:
    def test_snapshot_returns_copy(self) -> None:
        buf = RingBuffer(3)
        buf.push("x")
        buf.push("y")
        snap = buf.snapshot()
        assert snap == ["x", "y"]
        snap.append("z")
        assert buf.size == 2

    def test_snapshot_empty(self) -> None:
        assert RingBuffer(10).snapshot() == []

    def test_snapshot_after_wraparound(self) -> None:
        buf = RingBuffer(2)
        buf.push(1)
        buf.push(2)
        buf.push(3)
        assert buf.snapshot() == [2, 3]


class TestResize:
    def test_resize_larger_preserves_all_items(self) -> None:
        buf = RingBuffer(3)
        for i in range(3):
            buf.push(i)
        buf.resize(6)
        assert buf.capacity == 6
        assert buf.size == 3
        assert list(buf) == [0, 1, 2]

    def test_resize_smaller_keeps_newest(self) -> None:
        buf = RingBuffer(5)
        for i in range(5):
            buf.push(i)
        buf.resize(3)
        assert buf.capacity == 3
        assert buf.size == 3
        assert list(buf) == [2, 3, 4]

    def test_resize_smaller_than_size_drops_oldest(self) -> None:
        buf = RingBuffer(10)
        for i in range(10):
            buf.push(i)
        buf.resize(4)
        assert buf.size == 4
        assert list(buf) == [6, 7, 8, 9]

    def test_resize_to_same_does_nothing(self) -> None:
        buf = RingBuffer(4)
        buf.push(1)
        buf.resize(4)
        assert buf.capacity == 4
        assert buf.size == 1
        assert list(buf) == [1]

    def test_resize_to_one_from_many(self) -> None:
        buf = RingBuffer(5)
        for i in range(5):
            buf.push(i)
        buf.resize(1)
        assert buf.capacity == 1
        assert buf.size == 1
        assert list(buf) == [4]

    def test_resize_from_one_to_many(self) -> None:
        buf = RingBuffer(1)
        buf.push(99)
        buf.resize(5)
        assert buf.capacity == 5
        assert buf.size == 1
        assert list(buf) == [99]

    def test_resize_invalid_capacity(self) -> None:
        buf = RingBuffer(3)
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            buf.resize(0)


class TestContainerProtocols:
    def test_len(self) -> None:
        buf = RingBuffer(5)
        assert len(buf) == 0
        buf.push(1)
        assert len(buf) == 1
        buf.push(2)
        assert len(buf) == 2

    def test_contains(self) -> None:
        buf = RingBuffer(10)
        buf.push("hello")
        buf.push("world")
        assert "hello" in buf
        assert "world" in buf
        assert "nope" not in buf

    def test_getitem_positive_index(self) -> None:
        buf = RingBuffer(5)
        for x in "abcde":
            buf.push(x)
        assert buf[0] == "a"
        assert buf[2] == "c"
        assert buf[4] == "e"

    def test_getitem_negative_index(self) -> None:
        buf = RingBuffer(5)
        for x in "abcde":
            buf.push(x)
        assert buf[-1] == "e"
        assert buf[-3] == "c"
        assert buf[-5] == "a"

    def test_getitem_out_of_range_raises(self) -> None:
        buf = RingBuffer(3)
        buf.push(1)
        buf.push(2)
        with pytest.raises(IndexError, match="ring buffer index out of range"):
            _ = buf[5]
        with pytest.raises(IndexError, match="ring buffer index out of range"):
            _ = buf[-4]

    def test_eq_same_content(self) -> None:
        a = RingBuffer(5)
        b = RingBuffer(7)
        for x in [1, 2, 3]:
            a.push(x)
            b.push(x)
        assert a == b

    def test_eq_different_content(self) -> None:
        a = RingBuffer(5)
        b = RingBuffer(5)
        a.push(1)
        b.push(2)
        assert a != b

    def test_eq_different_capacity_same_items(self) -> None:
        a = RingBuffer(3)
        b = RingBuffer(10)
        a.push("x")
        b.push("x")
        assert a == b

    def test_eq_non_ringbuffer(self) -> None:
        assert RingBuffer(3) != "not a buffer"


class TestClear:
    def test_clear_resets_size_and_head(self) -> None:
        buf = RingBuffer(3)
        buf.push(1)
        buf.push(2)
        buf.push(3)
        buf.pop()
        buf.push(4)
        buf.clear()
        assert buf.size == 0
        assert buf.is_empty()
        assert list(buf) == []

    def test_clear_then_push_reuses_buffer(self) -> None:
        buf = RingBuffer(3)
        buf.push(1)
        buf.clear()
        buf.push("a")
        buf.push("b")
        assert list(buf) == ["a", "b"]


class TestDeepCopy:
    def test_deepcopy_is_independent(self) -> None:
        buf = RingBuffer(3)
        buf.push([1, 2])
        buf.push([3, 4])
        dup = copy.deepcopy(buf)
        assert dup == buf
        buf.push([5, 6])
        assert list(dup) == [[1, 2], [3, 4]]

    def test_deepcopy_mutation_isolation(self) -> None:
        buf = RingBuffer(3)
        buf.push([1])
        dup = copy.deepcopy(buf)
        dup.pop()[0] = 999
        assert buf.peek() == [1]


class TestRepr:
    def test_repr_includes_capacity_size_items(self) -> None:
        buf = RingBuffer(3)
        buf.push("a")
        r = repr(buf)
        assert "RingBuffer" in r
        assert "capacity=3" in r
        assert "size=1" in r
        assert "'a'" in r

    def test_repr_empty(self) -> None:
        r = repr(RingBuffer(2))
        assert "size=0" in r
        assert "items=[]" in r


class TestCapacityOne:
    def test_single_element_buffer(self) -> None:
        buf = RingBuffer(1)
        assert buf.push(42) is None
        assert buf.is_full()
        assert buf.peek() == 42
        assert buf.push(99) == 42
        assert buf.peek() == 99
        assert buf.pop() == 99
        assert buf.is_empty()

    def test_capacity_one_resize(self) -> None:
        buf = RingBuffer(1)
        buf.push(1)
        buf.resize(3)
        buf.push(2)
        buf.push(3)
        assert list(buf) == [1, 2, 3]
