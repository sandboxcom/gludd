"""Deep tests for the 2-3 finger tree: core operations, Deque, Sequence,
PriorityDeque, concat, split, edge cases, and stress tests.

Tests exercise the full internal structure: Empty, Single, Deep with
non-trivial middle trees, digit splitting, absorption, and rebalancing.
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.finger_tree import (
    Deep,
    Deque,
    Empty,
    Node2,
    Node3,
    PriorityDeque,
    Sequence,
    Single,
    concat,
    get,
    is_empty,
    peek_left,
    peek_right,
    pop_left,
    pop_right,
    push_left,
    push_right,
    size,
    split_at_index,
)

# ---------------------------------------------------------------------------
# 1. Empty tree invariants
# ---------------------------------------------------------------------------


class TestEmptyTree:
    def test_empty_is_singleton(self) -> None:
        a = Empty()
        b = Empty()
        assert a is b

    def test_empty_size_zero(self) -> None:
        assert size(Empty()) == 0

    def test_empty_is_empty(self) -> None:
        assert is_empty(Empty())

    def test_empty_pop_raises(self) -> None:
        with pytest.raises(IndexError):
            pop_left(Empty())
        with pytest.raises(IndexError):
            pop_right(Empty())

    def test_empty_peek_raises(self) -> None:
        with pytest.raises(IndexError):
            peek_left(Empty())
        with pytest.raises(IndexError):
            peek_right(Empty())


# ---------------------------------------------------------------------------
# 2. Single-element tree
# ---------------------------------------------------------------------------


class TestSingleTree:
    def test_single_size_one(self) -> None:
        t = Single(42)
        assert size(t) == 1
        assert not is_empty(t)

    def test_single_peek(self) -> None:
        t = Single("x")
        assert peek_left(t) == "x"
        assert peek_right(t) == "x"

    def test_single_pop_left(self) -> None:
        v, rest = pop_left(Single(7))
        assert v == 7
        assert isinstance(rest, Empty)

    def test_single_pop_right(self) -> None:
        v, rest = pop_right(Single(7))
        assert v == 7
        assert isinstance(rest, Empty)

    def test_single_push_creates_deep(self) -> None:
        t = push_left(Single(2), 1)
        assert isinstance(t, Deep)
        assert t.prefix == [1]
        assert isinstance(t.middle, Empty)
        assert t.suffix == [2]
        assert size(t) == 2


# ---------------------------------------------------------------------------
# 3. push / pop interplay and digit splitting
# ---------------------------------------------------------------------------


class TestPushPopInterplay:
    def test_push_many_and_pop_all(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(50):
            t = push_right(t, i)
        assert size(t) == 50
        for i in range(49, -1, -1):
            v, t = pop_right(t)
            assert v == i
        assert isinstance(t, Empty)

    def test_push_left_pop_left_fifo(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(20):
            t = push_right(t, i)
        for i in range(20):
            v, t = pop_left(t)
            assert v == i

    def test_push_left_pop_right_lifo(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(10):
            t = push_left(t, i)
        for i in range(10):
            v, t = pop_right(t)
            assert v == i

    def test_digit_split_on_push_left(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(10):
            t = push_right(t, i)
        t = push_left(t, 100)
        assert peek_left(t) == 100
        assert peek_right(t) == 9
        assert size(t) == 11

    def test_digit_split_on_push_right(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(10):
            t = push_left(t, i)
        t = push_right(t, 100)
        assert peek_right(t) == 100
        assert peek_left(t) == 9
        assert size(t) == 11


# ---------------------------------------------------------------------------
# 4. Concat
# ---------------------------------------------------------------------------


class TestConcat:
    def test_concat_empty_with_nonempty(self) -> None:
        t = push_right(push_right(Empty(), 1), 2)
        assert size(concat(Empty(), t)) == 2
        assert size(concat(t, Empty())) == 2

    def test_concat_two_singles(self) -> None:
        result = concat(Single(1), Single(2))
        assert size(result) == 2
        assert peek_left(result) == 1
        assert peek_right(result) == 2

    def test_concat_two_deep_trees(self) -> None:
        a: Empty | Single[int] | Deep[int] = Empty()
        b: Empty | Single[int] | Deep[int] = Empty()
        for i in range(7):
            a = push_right(a, i)
            b = push_right(b, i + 100)
        c = concat(a, b)
        assert size(c) == 14
        assert peek_left(c) == 0
        assert peek_right(c) == 106

    def test_concat_fully_drains(self) -> None:
        a: Empty | Single[int] | Deep[int] = Empty()
        for i in range(20):
            a = push_right(a, i)
        c = concat(a, Empty())
        assert size(c) == 20

    def test_concat_preserves_order(self) -> None:
        a: Empty | Single[int] | Deep[int] = Empty()
        b: Empty | Single[int] | Deep[int] = Empty()
        for i in range(5):
            a = push_right(a, i)
        for i in range(5):
            b = push_right(b, i + 10)
        t = concat(a, b)
        out: list[int] = []
        cur = t
        while not isinstance(cur, Empty):
            v, cur = pop_left(cur)
            out.append(v)
        assert out == [0, 1, 2, 3, 4, 10, 11, 12, 13, 14]


# ---------------------------------------------------------------------------
# 5. Indexed access (get)
# ---------------------------------------------------------------------------


class TestIndexedAccess:
    def test_get_from_deep(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(30):
            t = push_right(t, i)
        for i in range(30):
            assert get(t, i) == i

    def test_get_negative_index(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(10):
            t = push_right(t, i)
        assert get(t, -1) == 9
        assert get(t, -2) == 8

    def test_get_out_of_range_raises(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(5):
            t = push_right(t, i)
        with pytest.raises(IndexError):
            get(t, 5)
        with pytest.raises(IndexError):
            get(t, -6)


# ---------------------------------------------------------------------------
# 6. Split at index
# ---------------------------------------------------------------------------


class TestSplit:
    def test_split_empty(self) -> None:
        left, r = split_at_index(Empty(), 0)
        assert isinstance(left, Empty)
        assert isinstance(r, Empty)

    def test_split_single(self) -> None:
        left, r = split_at_index(Single(42), 1)
        assert peek_left(left) == 42
        assert isinstance(r, Empty)

    def test_split_deep_middle(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(15):
            t = push_right(t, i)
        left, r = split_at_index(t, 5)
        assert size(left) == 5
        assert size(r) == 10
        assert get(left, 0) == 0
        assert get(r, 0) == 5

    def test_split_at_zero(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(10):
            t = push_right(t, i)
        left, r = split_at_index(t, 0)
        assert isinstance(left, Empty)
        assert size(r) == 10

    def test_split_at_end(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(10):
            t = push_right(t, i)
        left, r = split_at_index(t, 10)
        assert size(left) == 10
        assert isinstance(r, Empty)


# ---------------------------------------------------------------------------
# 7. Deque wrapper
# ---------------------------------------------------------------------------


class TestDeque:
    def test_deque_basic_ops(self) -> None:
        dq: Deque[int] = Deque()
        assert len(dq) == 0
        assert not dq

        dq.push(1)
        dq.push(2)
        dq.push_left(0)
        assert len(dq) == 3
        assert dq.to_list() == [0, 1, 2]

    def test_deque_pop_mixed_ends(self) -> None:
        dq: Deque[int] = Deque()
        for i in range(20):
            dq.push(i)
        assert dq.peek() == 19
        assert dq.peek_left() == 0
        assert dq.pop() == 19
        assert dq.pop_left() == 0
        assert dq.pop() == 18
        assert dq.pop_left() == 1

    def test_deque_extend_left(self) -> None:
        dq: Deque[int] = Deque()
        dq.extend_left([3, 2, 1])
        assert dq.to_list() == [3, 2, 1]

    def test_deque_rotate(self) -> None:
        dq: Deque[int] = Deque.from_iter([1, 2, 3])
        dq.rotate(1)
        assert dq.to_list() == [3, 1, 2]
        dq.rotate(3)
        assert dq.to_list() == [3, 1, 2]

    def test_deque_pop_empty_raises(self) -> None:
        dq: Deque[int] = Deque()
        with pytest.raises(IndexError):
            dq.pop()
        with pytest.raises(IndexError):
            dq.pop_left()

    def test_deque_iteration(self) -> None:
        dq: Deque[int] = Deque.from_iter([10, 20, 30])
        assert list(dq) == [10, 20, 30]

    def test_deque_large(self) -> None:
        dq: Deque[int] = Deque()
        for i in range(1000):
            dq.push(i)
        assert len(dq) == 1000
        for i in range(1000):
            assert dq.pop_left() == i
        assert len(dq) == 0

    def test_deque_mixed_ends_large(self) -> None:
        dq: Deque[int] = Deque()
        for i in range(200):
            if i % 2 == 0:
                dq.push_left(i)
            else:
                dq.push(i)
        assert len(dq) == 200
        elements: list[int] = []
        for i in range(200):
            if i % 2 == 0:
                elements.append(dq.pop())
            else:
                elements.append(dq.pop_left())
        assert len(elements) == 200


# ---------------------------------------------------------------------------
# 8. Sequence wrapper
# ---------------------------------------------------------------------------


class TestSequence:
    def test_sequence_index(self) -> None:
        seq: Sequence[str] = Sequence.from_iter(["a", "b", "c", "d"])
        assert seq[0] == "a"
        assert seq[3] == "d"
        assert seq[-1] == "d"

    def test_sequence_concat(self) -> None:
        a: Sequence[int] = Sequence.from_iter([1, 2, 3])
        b: Sequence[int] = Sequence.from_iter([4, 5, 6])
        a.concat(b)
        assert len(a) == 6
        assert a.to_list() == [1, 2, 3, 4, 5, 6]

    def test_sequence_split_at(self) -> None:
        seq: Sequence[int] = Sequence.from_iter([10, 20, 30, 40, 50])
        left, r = seq.split_at(2)
        assert left.to_list() == [10, 20]
        assert r.to_list() == [30, 40, 50]

    def test_sequence_push_pop(self) -> None:
        seq: Sequence[int] = Sequence()
        seq.push(10)
        seq.push_left(5)
        seq.push(20)
        assert seq.to_list() == [5, 10, 20]
        assert seq.pop() == 20
        assert seq.pop_left() == 5
        assert seq[0] == 10

    def test_sequence_iteration(self) -> None:
        seq: Sequence[int] = Sequence.from_iter([100, 200, 300])
        assert list(seq) == [100, 200, 300]

    def test_sequence_index_out_of_range(self) -> None:
        seq: Sequence[int] = Sequence.from_iter([1, 2])
        with pytest.raises(IndexError):
            _ = seq[5]
        with pytest.raises(IndexError):
            _ = seq[-3]


# ---------------------------------------------------------------------------
# 9. Priority deque
# ---------------------------------------------------------------------------


class TestPriorityDeque:
    def test_priority_ordered_push_pop(self) -> None:
        pdq: PriorityDeque[int] = PriorityDeque()
        for i in range(10):
            pdq.push_max(i)
        for i in range(10):
            assert pdq.pop_min() == i

    def test_priority_min_peek(self) -> None:
        pdq: PriorityDeque[int] = PriorityDeque()
        pdq.push_min(3)
        pdq.push_max(7)
        pdq.push_min(1)
        assert pdq.peek_min() == 1
        assert pdq.peek_max() == 7

    def test_priority_pop_empty_raises(self) -> None:
        pdq: PriorityDeque[int] = PriorityDeque()
        with pytest.raises(IndexError):
            pdq.pop_min()
        with pytest.raises(IndexError):
            pdq.pop_max()

    def test_priority_mixed_ops(self) -> None:
        pdq: PriorityDeque[int] = PriorityDeque()
        pdq.push_min(10)
        pdq.push_max(20)
        pdq.push_min(0)
        pdq.push_max(30)
        assert pdq.pop_min() == 0
        assert pdq.pop_max() == 30
        assert pdq.pop_min() == 10
        assert pdq.pop_max() == 20
        assert len(pdq) == 0


# ---------------------------------------------------------------------------
# 10. Stress: deep tree (forces multiple levels of middle nodes)
# ---------------------------------------------------------------------------


class TestStressDeepTree:
    def test_very_deep_tree(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        N = 500
        for i in range(N):
            t = push_right(t, i)
        assert size(t) == N
        assert get(t, 0) == 0
        assert get(t, N - 1) == N - 1
        assert get(t, N // 2) == N // 2

    def test_very_deep_tree_mixed_push(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(250):
            t = push_left(t, -i)
            t = push_right(t, i)
        assert size(t) == 500
        assert peek_left(t) == -249
        assert peek_right(t) == 249

    def test_very_deep_tree_pop_all(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        N = 300
        for i in range(N):
            t = push_right(t, i)
        for i in range(N):
            v, t = pop_left(t)
            assert v == i
        assert isinstance(t, Empty)


# ---------------------------------------------------------------------------
# 11. Node2/Node3 helpers
# ---------------------------------------------------------------------------


class TestNodes:
    def test_node2_to_list(self) -> None:
        n = Node2(1, 2)
        assert n.to_list() == [1, 2]

    def test_node3_to_list(self) -> None:
        n = Node3(1, 2, 3)
        assert n.to_list() == [1, 2, 3]

    def test_node2_repr(self) -> None:
        assert repr(Node2("x", "y")) == "Node2('x', 'y')"

    def test_node3_repr(self) -> None:
        assert repr(Node3("a", "b", "c")) == "Node3('a', 'b', 'c')"


# ---------------------------------------------------------------------------
# 12. Deep tree size invariants
# ---------------------------------------------------------------------------


class TestDeepSize:
    def test_deep_size_accurate_after_push(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        t = push_right(t, 1)
        t = push_right(t, 2)
        t = push_right(t, 3)
        t = push_left(t, 0)
        assert size(t) == 4

    def test_deep_size_accurate_after_pop(self) -> None:
        t: Empty | Single[int] | Deep[int] = Empty()
        for i in range(20):
            t = push_right(t, i)
        _, t = pop_left(t)
        _, t = pop_right(t)
        assert size(t) == 18

    def test_deep_size_accurate_after_concat(self) -> None:
        a: Empty | Single[int] | Deep[int] = Empty()
        b: Empty | Single[int] | Deep[int] = Empty()
        for i in range(5):
            a = push_right(a, i)
            b = push_right(b, i + 10)
        c = concat(a, b)
        assert size(c) == 10
