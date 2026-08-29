"""2-3 finger tree: deque, sequence, and priority-queue adaptations.

Pure-Python, stdlib only.  Based on Hinze & Paterson (2006).
Deque: O(1) push/pop both ends, O(log n) concat.
Sequence: O(log n) random access by index, O(log n) split/concat.
Priority: O(1) push/pop (ordered input), O(1) peek-min/peek-max.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T")


class Node2(Generic[T]):
    """Store a measured two-element internal finger-tree node."""

    __slots__ = ("a", "b")

    def __init__(self, a: T, b: T) -> None:
        """Initialize a two-element node."""
        self.a = a
        self.b = b

    def to_list(self) -> list[T]:
        """Return the node's elements in order."""
        return [self.a, self.b]

    def __repr__(self) -> str:
        """Return a developer representation."""
        return f"Node2({self.a!r}, {self.b!r})"


class Node3(Generic[T]):
    """Store a measured three-element internal finger-tree node."""

    __slots__ = ("a", "b", "c")

    def __init__(self, a: T, b: T, c: T) -> None:
        """Initialize a three-element node."""
        self.a = a
        self.b = b
        self.c = c

    def to_list(self) -> list[T]:
        """Return the node's elements in order."""
        return [self.a, self.b, self.c]

    def __repr__(self) -> str:
        """Return a developer representation."""
        return f"Node3({self.a!r}, {self.b!r}, {self.c!r})"


class Empty:
    """Represent the singleton empty finger tree."""

    __slots__ = ()
    _instance: Empty | None = None

    def __new__(cls) -> Empty:
        """Return the process-local empty-tree singleton."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        """Return ``False`` for the empty tree."""
        return False


class Single(Generic[T]):
    """Represent a finger tree containing one value."""

    __slots__ = ("v",)

    def __init__(self, v: T) -> None:
        """Initialize a one-value tree."""
        self.v = v


class Deep(Generic[T]):
    """Represent a finger tree with prefix, middle, and suffix digits."""

    __slots__ = ("_size", "middle", "prefix", "suffix")

    def __init__(
        self,
        prefix: list[T],
        middle: Empty | Single[Any] | Deep[Any],
        suffix: list[T],
    ) -> None:
        """Initialize a deep tree from its three measured regions."""
        self.prefix = prefix
        self.middle = middle
        self.suffix = suffix
        self._size: int = -1

    def size(self) -> int:
        """Return and cache the recursively measured element count."""
        if self._size < 0:
            self._size = len(self.prefix) + _deep_size(self.middle) + len(self.suffix)
        return self._size


FingerTree = Empty | Single[Any] | Deep[Any]


def _elem_count(x: object) -> int:
    if isinstance(x, Node2):
        return _elem_count(x.a) + _elem_count(x.b)
    if isinstance(x, Node3):
        return _elem_count(x.a) + _elem_count(x.b) + _elem_count(x.c)
    return 1


def _deep_size(m: Empty | Single[Any] | Deep[Any]) -> int:
    if isinstance(m, Empty):
        return 0
    if isinstance(m, Single):
        return _elem_count(m.v)
    if isinstance(m, Deep):
        return sum(_elem_count(x) for x in m.prefix) + _deep_size(m.middle) + sum(_elem_count(x) for x in m.suffix)


def _tree_to_list(z: FingerTree) -> list[Any]:
    result: list[Any] = []
    _preorder(z, result)
    return result


def _preorder(z: FingerTree, out: list[Any]) -> None:
    if isinstance(z, Empty):
        return
    if isinstance(z, Single):
        v = z.v
        if isinstance(v, (Node2, Node3)):
            for x in v.to_list():
                if isinstance(x, (Node2, Node3)):
                    _preorder_flatten(x, out)
                else:
                    out.append(x)
        else:
            out.append(v)
        return
    if isinstance(z, Deep):
        for x in z.prefix:
            if isinstance(x, (Node2, Node3)):
                _preorder_flatten(x, out)
            else:
                out.append(x)
        _preorder(z.middle, out)
        for x in z.suffix:
            if isinstance(x, (Node2, Node3)):
                _preorder_flatten(x, out)
            else:
                out.append(x)


def _preorder_flatten(node: Node2[Any] | Node3[Any], out: list[Any]) -> None:
    for x in node.to_list():
        if isinstance(x, (Node2, Node3)):
            _preorder_flatten(x, out)
        else:
            out.append(x)


def _nodes_of(prefix: list[Any], suffix: list[Any]) -> list[Any]:
    all_items = [*prefix, *suffix]
    result: list[Any] = []
    i = 0
    while i < len(all_items):
        remaining = len(all_items) - i
        if remaining in {2, 4}:
            result.append(Node2(all_items[i], all_items[i + 1]))
            i += 2
        elif remaining >= 3:
            result.append(Node3(all_items[i], all_items[i + 1], all_items[i + 2]))
            i += 3
        else:
            raise ValueError("finger-tree bridge cannot contain a singleton")
    return result


def size(z: FingerTree) -> int:
    """Return the recursively measured number of leaf elements."""
    if isinstance(z, Empty):
        return 0
    if isinstance(z, Single):
        return _elem_count(z.v)
    if isinstance(z, Deep):
        return z.size()
    return 0


def is_empty(z: FingerTree) -> bool:
    """Return whether a finger tree is empty."""
    return isinstance(z, Empty)


def _digit_to_leaves(items: list[Any]) -> list[Any]:
    out: list[Any] = []
    for x in items:
        if isinstance(x, Node2):
            out.extend(_digit_to_leaves([x.a, x.b]))
        elif isinstance(x, Node3):
            out.extend(_digit_to_leaves([x.a, x.b, x.c]))
        else:
            out.append(x)
    return out


# ---------------------------------------------------------------------------
# Push / Pop — left end
# ---------------------------------------------------------------------------


def push_left(z: FingerTree, v: T) -> Single[T] | Deep[T]:
    """Return a tree with ``v`` prepended."""
    if isinstance(z, Empty):
        return Single(v)
    if isinstance(z, Single):
        return Deep([v], Empty(), [z.v])
    if isinstance(z, Deep):
        pf: list[Any] = z.prefix
        if len(pf) >= 4:
            return Deep(
                [v, pf[0]],
                _push_left_deep(z.middle, Node3(pf[1], pf[2], pf[3])),
                z.suffix,
            )
        return Deep([v, *pf], z.middle, z.suffix)
    raise TypeError(f"Unexpected tree shape: {z!r}")


def _push_left_deep(
    m: Empty | Single[Any] | Deep[Any], node: Node2[Any] | Node3[Any]
) -> Empty | Single[Any] | Deep[Any]:
    if isinstance(m, Empty):
        return Single(node)
    if isinstance(m, Single):
        return Deep([node], Empty(), [m.v])
    if isinstance(m, Deep):
        pf: list[Node2[Any] | Node3[Any]] = m.prefix
        if len(pf) >= 4:
            return Deep(
                [node, pf[0]],
                _push_left_deep(m.middle, Node3(pf[1], pf[2], pf[3])),
                m.suffix,
            )
        return Deep([node, *pf], m.middle, m.suffix)
    raise TypeError(f"Unexpected middle shape: {m!r}")


def pop_left(z: FingerTree) -> tuple[Any, FingerTree]:
    """Remove and return the leftmost value and remaining tree."""
    return _pop_left_atomic(z)


def _pop_left_atomic(z: FingerTree) -> tuple[Any, FingerTree]:
    if isinstance(z, Empty):
        raise IndexError("pop from empty finger tree")
    if isinstance(z, Single):
        return z.v, Empty()
    if isinstance(z, Deep):
        first = z.prefix[0]
        rest = z.prefix[1:]
        if rest:
            return first, Deep(rest, z.middle, z.suffix)
        return first, _absorb_left(z.middle, z.suffix)
    raise TypeError(f"Unexpected tree shape: {z!r}")


def _absorb_left(m: Empty | Single[Any] | Deep[Any], suffix: list[Any]) -> FingerTree:
    if isinstance(m, Empty):
        return _parts_to_tree(suffix)

    node, new_middle = _pop_left_atomic(m)
    if not isinstance(node, (Node2, Node3)):
        raise TypeError(f"Unexpected middle element: {node!r}")
    return Deep(node.to_list(), new_middle, suffix)


def _parts_to_tree(parts: list[Any]) -> FingerTree:
    if not parts:
        return Empty()
    if len(parts) == 1:
        return Single(parts[0])
    if len(parts) == 2:
        return Deep([parts[0]], Empty(), [parts[1]])
    return Deep(parts[:2], Empty(), parts[2:])


def peek_left(z: FingerTree) -> Any:
    """Return the leftmost leaf value without changing the tree."""
    if isinstance(z, Empty):
        raise IndexError("peek from empty finger tree")
    if isinstance(z, Single):
        v = z.v
        if isinstance(v, Node2):
            return v.a
        if isinstance(v, Node3):
            return v.a
        return v
    if isinstance(z, Deep):
        first = z.prefix[0]
        if isinstance(first, Node2):
            return first.a
        if isinstance(first, Node3):
            return first.a
        return first
    raise TypeError(f"Unexpected tree shape: {z!r}")


# ---------------------------------------------------------------------------
# Push / Pop — right end
# ---------------------------------------------------------------------------


def push_right(z: FingerTree, v: T) -> Single[T] | Deep[T]:
    """Return a tree with ``v`` appended."""
    if isinstance(z, Empty):
        return Single(v)
    if isinstance(z, Single):
        return Deep([z.v], Empty(), [v])
    if isinstance(z, Deep):
        sf: list[Any] = z.suffix
        if len(sf) >= 4:
            return Deep(
                z.prefix,
                _push_right_deep(z.middle, Node3(sf[0], sf[1], sf[2])),
                [sf[3], v],
            )
        return Deep(z.prefix, z.middle, [*sf, v])
    raise TypeError(f"Unexpected tree shape: {z!r}")


def _push_right_deep(
    m: Empty | Single[Any] | Deep[Any], node: Node2[Any] | Node3[Any]
) -> Empty | Single[Any] | Deep[Any]:
    if isinstance(m, Empty):
        return Single(node)
    if isinstance(m, Single):
        return Deep([m.v], Empty(), [node])
    if isinstance(m, Deep):
        sf: list[Node2[Any] | Node3[Any]] = m.suffix
        if len(sf) >= 4:
            return Deep(
                m.prefix,
                _push_right_deep(m.middle, Node3(sf[0], sf[1], sf[2])),
                [sf[3], node],
            )
        return Deep(m.prefix, m.middle, [*sf, node])
    raise TypeError(f"Unexpected middle shape: {m!r}")


def pop_right(z: FingerTree) -> tuple[Any, FingerTree]:
    """Remove and return the rightmost value and remaining tree."""
    return _pop_right_atomic(z)


def _pop_right_atomic(z: FingerTree) -> tuple[Any, FingerTree]:
    if isinstance(z, Empty):
        raise IndexError("pop from empty finger tree")
    if isinstance(z, Single):
        return z.v, Empty()
    if isinstance(z, Deep):
        last = z.suffix[-1]
        rest = z.suffix[:-1]
        if rest:
            return last, Deep(z.prefix, z.middle, rest)
        return last, _absorb_right(z.prefix, z.middle)
    raise TypeError(f"Unexpected tree shape: {z!r}")


def _absorb_right(prefix: list[Any], m: Empty | Single[Any] | Deep[Any]) -> FingerTree:
    if isinstance(m, Empty):
        return _parts_to_tree(prefix)

    node, new_middle = _pop_right_atomic(m)
    if not isinstance(node, (Node2, Node3)):
        raise TypeError(f"Unexpected middle element: {node!r}")
    return Deep(prefix, new_middle, node.to_list())


def peek_right(z: FingerTree) -> Any:
    """Return the rightmost leaf value without changing the tree."""
    if isinstance(z, Empty):
        raise IndexError("peek from empty finger tree")
    if isinstance(z, Single):
        v = z.v
        if isinstance(v, Node3):
            return v.c
        if isinstance(v, Node2):
            return v.b
        return v
    if isinstance(z, Deep):
        last = z.suffix[-1]
        if isinstance(last, Node3):
            return last.c
        if isinstance(last, Node2):
            return last.b
        return last
    raise TypeError(f"Unexpected tree shape: {z!r}")


# ---------------------------------------------------------------------------
# Concatenation — O(log n)
# ---------------------------------------------------------------------------


def concat(t1: FingerTree, t2: FingerTree) -> FingerTree:
    """Concatenate two finger trees while preserving order."""
    if isinstance(t1, Empty):
        return t2
    if isinstance(t2, Empty):
        return t1
    if isinstance(t1, Single) and isinstance(t2, Single):
        return Deep([t1.v], Empty(), [t2.v])
    if isinstance(t1, Single) and isinstance(t2, Deep):
        parts = [t1.v, *_digit_to_leaves(t2.prefix)]
        return Deep(parts, t2.middle, t2.suffix)
    if isinstance(t1, Deep) and isinstance(t2, Single):
        parts = [*_digit_to_leaves(t1.suffix), t2.v]
        return Deep(t1.prefix, t1.middle, parts)
    if isinstance(t1, Deep) and isinstance(t2, Deep):
        mid = _merge_middles(
            t1.middle,
            _digit_to_leaves(t1.suffix),
            _digit_to_leaves(t2.prefix),
            t2.middle,
        )
        return Deep(t1.prefix, mid, t2.suffix)
    raise TypeError(f"Unexpected concat shapes: {t1!r} {t2!r}")


def _merge_middles(
    m1: Empty | Single[Any] | Deep[Any],
    s1: list[Any],
    p2: list[Any],
    m2: Empty | Single[Any] | Deep[Any],
) -> Empty | Single[Any] | Deep[Any]:
    nodes = _nodes_of(s1, p2)
    result = m1
    for n in nodes:
        result = _push_right_deep(result, n)
    return _merge_trees(result, m2)


def _merge_trees(
    a: Empty | Single[Any] | Deep[Any], b: Empty | Single[Any] | Deep[Any]
) -> Empty | Single[Any] | Deep[Any]:
    if isinstance(a, Empty):
        return b
    if isinstance(b, Empty):
        return a
    if isinstance(a, Single) and isinstance(b, Single):
        return Deep([a.v], Empty(), [b.v])
    if isinstance(a, Single) and isinstance(b, Deep):
        return Deep([a.v, *b.prefix], b.middle, b.suffix)
    if isinstance(a, Deep) and isinstance(b, Single):
        return Deep(a.prefix, a.middle, [*a.suffix, b.v])
    if isinstance(a, Deep) and isinstance(b, Deep):
        mid = _merge_middles(a.middle, a.suffix, b.prefix, b.middle)
        return Deep(a.prefix, mid, b.suffix)
    return a


# ---------------------------------------------------------------------------
# Indexed access — O(log n) via iterative walk
# ---------------------------------------------------------------------------


def get(z: FingerTree, idx: int) -> Any:
    """Return a leaf by positive or negative index."""
    sz = size(z)
    if idx < 0:
        idx += sz
    if idx < 0 or idx >= sz:
        raise IndexError(f"index {idx} out of range [0, {sz})")
    return _get_index(z, idx)


def _get_index(z: FingerTree, idx: int) -> Any:
    if isinstance(z, Single):
        return _get_from_value(z.v, idx)
    if isinstance(z, Deep):
        return _get_deep(z, idx)
    raise IndexError("index out of range")


def _get_from_value(v: object, idx: int) -> Any:
    if isinstance(v, Node2):
        if idx == 0:
            return _get_from_value(v.a, 0) if idx < _elem_count(v.a) else _get_from_value(v.b, 0)
        left_sz = _elem_count(v.a)
        if idx < left_sz:
            return _get_from_value(v.a, idx)
        return _get_from_value(v.b, idx - left_sz)
    if isinstance(v, Node3):
        left_sz = _elem_count(v.a)
        if idx < left_sz:
            return _get_from_value(v.a, idx)
        mid_sz = _elem_count(v.b)
        if idx < left_sz + mid_sz:
            return _get_from_value(v.b, idx - left_sz)
        return _get_from_value(v.c, idx - left_sz - mid_sz)
    if idx == 0:
        return v
    raise IndexError("index out of range")


def _get_deep(z: Deep[Any], idx: int) -> Any:
    offset = 0
    for x in z.prefix:
        ec = _elem_count(x)
        if idx < offset + ec:
            return _get_from_value(x, idx - offset)
        offset += ec

    msize = _deep_size(z.middle)
    if idx < offset + msize:
        return _get_index(z.middle, idx - offset)
    offset += msize

    for x in z.suffix:
        ec = _elem_count(x)
        if idx < offset + ec:
            return _get_from_value(x, idx - offset)
        offset += ec

    raise IndexError("index out of range")


# ---------------------------------------------------------------------------
# Split at index
# ---------------------------------------------------------------------------


def split_at_index(z: FingerTree, idx: int) -> tuple[FingerTree, FingerTree]:
    """Split a tree immediately before the normalized index."""
    sz = size(z)
    if idx < 0:
        idx += sz
    if idx <= 0:
        return Empty(), z
    if idx >= sz:
        return z, Empty()
    return _split_at(z, idx)


def _split_at(z: FingerTree, idx: int) -> tuple[FingerTree, FingerTree]:
    if isinstance(z, Single):
        return Empty(), z
    if isinstance(z, Deep):
        offset = 0
        for i, x in enumerate(z.prefix):
            ec = _elem_count(x)
            if idx <= offset + ec:
                pos = idx - offset
                if ec == 1:
                    return (
                        _parts_to_tree(z.prefix[:i]),
                        _build_deep(z.prefix[i:], z.middle, z.suffix),
                    )
                left_parts, right_parts = _split_value(x, pos)
                lp = z.prefix[:i] + left_parts
                rp = right_parts + z.prefix[i + 1 :]
                left = _parts_to_tree(lp)
                r = _build_deep(rp, z.middle, z.suffix)
                return left, r
            offset += ec

        msize = _deep_size(z.middle)
        if idx <= offset + msize:
            ml, mr = _split_at(z.middle, idx - offset)
            return (
                _build_deep(z.prefix, ml, []),
                _build_deep([], mr, z.suffix),
            )
        offset += msize

        for i, x in enumerate(z.suffix):
            ec = _elem_count(x)
            if idx <= offset + ec:
                pos = idx - offset
                if ec == 1:
                    return (
                        _build_deep(z.prefix, z.middle, z.suffix[: i + 1]),
                        _parts_to_tree(z.suffix[i + 1 :]),
                    )
                left_parts, right_parts = _split_value(x, pos)
                return (
                    _build_deep(z.prefix, z.middle, z.suffix[:i] + left_parts),
                    _parts_to_tree(right_parts + z.suffix[i + 1 :]),
                )
            offset += ec
    return Empty(), z


def _split_value(v: object, pos: int) -> tuple[list[Any], list[Any]]:
    if isinstance(v, Node2):
        left_sz = _elem_count(v.a)
        if pos < left_sz:
            la, ra = _split_value(v.a, pos)
            return la, [*ra, v.b]
        la2, ra2 = _split_value(v.b, pos - left_sz)
        return [v.a, *la2], ra2
    if isinstance(v, Node3):
        left_sz = _elem_count(v.a)
        if pos < left_sz:
            la, ra = _split_value(v.a, pos)
            return la, [*ra, v.b, v.c]
        mid_sz = _elem_count(v.b)
        if pos < left_sz + mid_sz:
            lb, rb = _split_value(v.b, pos - left_sz)
            return [v.a, *lb], [*rb, v.c]
        lc, rc = _split_value(v.c, pos - left_sz - mid_sz)
        return [v.a, v.b, *lc], rc
    if pos == 0:
        return [], [v]
    return [v], []


def _build_deep(prefix: list[Any], middle: Empty | Single[Any] | Deep[Any], suffix: list[Any]) -> FingerTree:
    if not prefix and isinstance(middle, Empty) and not suffix:
        return Empty()
    if not prefix and isinstance(middle, Empty) and len(suffix) == 1:
        return Single(suffix[0])
    if not suffix and isinstance(middle, Empty) and len(prefix) == 1:
        return Single(prefix[0])
    if isinstance(middle, Single) and not prefix and not suffix:
        return middle
    return Deep(prefix or [], middle, suffix or [])


# ---------------------------------------------------------------------------
# Deque
# ---------------------------------------------------------------------------


class Deque(Generic[T]):
    """Provide a double-ended queue backed by a finger tree."""

    __slots__ = ("_len", "_root")

    def __init__(self) -> None:
        """Initialize an empty deque."""
        self._root: FingerTree = Empty()
        self._len = 0

    @classmethod
    def from_iter(cls, items: list[T]) -> Deque[T]:
        """Build a deque from ordered items."""
        dq: Deque[T] = cls()
        for v in items:
            dq.push(v)
        return dq

    def __len__(self) -> int:
        """Return the number of elements."""
        return self._len

    def __bool__(self) -> bool:
        """Return whether the deque contains any elements."""
        return self._len > 0

    def push(self, v: T) -> None:
        """Append a value at the right end."""
        self._root = push_right(self._root, v)
        self._len += 1

    def push_left(self, v: T) -> None:
        """Prepend a value at the left end."""
        self._root = push_left(self._root, v)
        self._len += 1

    def pop(self) -> T:
        """Remove and return the rightmost value."""
        if self._len == 0:
            raise IndexError("pop from empty deque")
        v, self._root = pop_right(self._root)
        self._len -= 1
        return cast(T, v)

    def pop_left(self) -> T:
        """Remove and return the leftmost value."""
        if self._len == 0:
            raise IndexError("pop from empty deque")
        v, self._root = pop_left(self._root)
        self._len -= 1
        return cast(T, v)

    def peek(self) -> T:
        """Return the rightmost value without removal."""
        return cast(T, peek_right(self._root))

    def peek_left(self) -> T:
        """Return the leftmost value without removal."""
        return cast(T, peek_left(self._root))

    def extend(self, items: list[T]) -> None:
        """Append ordered items at the right end."""
        for v in items:
            self.push(v)

    def extend_left(self, items: list[T]) -> None:
        """Prepend ordered items at the left end."""
        for v in reversed(items):
            self.push_left(v)

    def rotate(self, n: int = 1) -> None:
        """Rotate right by ``n`` positions."""
        if self._len == 0:
            return
        for _ in range(n % self._len):
            self.push_left(self.pop())

    def to_list(self) -> list[T]:
        """Return all values in deque order."""
        return _tree_to_list(self._root)

    def __iter__(self) -> Iterator[T]:
        """Iterate over values in deque order."""
        return iter(self.to_list())

    def __repr__(self) -> str:
        """Return a developer representation."""
        return f"Deque({self.to_list()!r})"


# ---------------------------------------------------------------------------
# Sequence
# ---------------------------------------------------------------------------


class Sequence(Generic[T]):
    """Provide an indexed sequence backed by a finger tree."""

    __slots__ = ("_root", "_size")

    def __init__(self) -> None:
        """Initialize an empty sequence."""
        self._root: FingerTree = Empty()
        self._size = 0

    @classmethod
    def from_iter(cls, items: list[T]) -> Sequence[T]:
        """Build a sequence from ordered items."""
        seq: Sequence[T] = cls()
        for v in items:
            seq.push(v)
        return seq

    def __len__(self) -> int:
        """Return the number of elements."""
        return self._size

    def __bool__(self) -> bool:
        """Return whether the sequence contains any elements."""
        return self._size > 0

    def __getitem__(self, idx: int) -> T:
        """Return a value by positive or negative index."""
        return cast(T, get(self._root, idx))

    def push(self, v: T) -> None:
        """Append a value."""
        self._root = push_right(self._root, v)
        self._size += 1

    def push_left(self, v: T) -> None:
        """Prepend a value."""
        self._root = push_left(self._root, v)
        self._size += 1

    def pop(self) -> T:
        """Remove and return the final value."""
        if self._size == 0:
            raise IndexError("pop from empty sequence")
        v, self._root = pop_right(self._root)
        self._size -= 1
        return cast(T, v)

    def pop_left(self) -> T:
        """Remove and return the first value."""
        if self._size == 0:
            raise IndexError("pop from empty sequence")
        v, self._root = pop_left(self._root)
        self._size -= 1
        return cast(T, v)

    def peek(self) -> T:
        """Return the final value without removal."""
        return cast(T, peek_right(self._root))

    def peek_left(self) -> T:
        """Return the first value without removal."""
        return cast(T, peek_left(self._root))

    def extend(self, items: list[T]) -> None:
        """Append ordered items."""
        for v in items:
            self.push(v)

    def concat(self, other: Sequence[T]) -> None:
        """Append another sequence in place."""
        self._root = concat(self._root, other._root)
        self._size += len(other)

    def split_at(self, idx: int) -> tuple[Sequence[T], Sequence[T]]:
        """Return independent sequences split before ``idx``."""
        left, r = split_at_index(self._root, idx)
        ls = Sequence[T]()
        ls._root = left
        ls._size = size(left)
        rs = Sequence[T]()
        rs._root = r
        rs._size = size(r)
        return ls, rs

    def to_list(self) -> list[T]:
        """Return all values in sequence order."""
        return _tree_to_list(self._root)

    def __iter__(self) -> Iterator[T]:
        """Iterate over values in sequence order."""
        return iter(self.to_list())

    def __repr__(self) -> str:
        """Return a developer representation."""
        return f"Sequence({self.to_list()!r})"


# ---------------------------------------------------------------------------
# Priority deque
# ---------------------------------------------------------------------------


class PriorityDeque(Generic[T]):
    """Provide a two-ended priority deque for ordered insertions."""

    __slots__ = ("_root", "_size")

    def __init__(self) -> None:
        """Initialize an empty priority deque."""
        self._root: FingerTree = Empty()
        self._size = 0

    def __len__(self) -> int:
        """Return the number of elements."""
        return self._size

    def push_min(self, v: T) -> None:
        """Insert a new minimum at the left end."""
        self._root = push_left(self._root, v)
        self._size += 1

    def push_max(self, v: T) -> None:
        """Insert a new maximum at the right end."""
        self._root = push_right(self._root, v)
        self._size += 1

    def pop_min(self) -> T:
        """Remove and return the minimum value."""
        if self._size == 0:
            raise IndexError("pop from empty priority deque")
        v, self._root = pop_left(self._root)
        self._size -= 1
        return cast(T, v)

    def pop_max(self) -> T:
        """Remove and return the maximum value."""
        if self._size == 0:
            raise IndexError("pop from empty priority deque")
        v, self._root = pop_right(self._root)
        self._size -= 1
        return cast(T, v)

    def peek_min(self) -> T:
        """Return the minimum value without removal."""
        return cast(T, peek_left(self._root))

    def peek_max(self) -> T:
        """Return the maximum value without removal."""
        return cast(T, peek_right(self._root))

    def to_list(self) -> list[T]:
        """Return all values from minimum to maximum."""
        return _tree_to_list(self._root)

    def __repr__(self) -> str:
        """Return a developer representation."""
        return f"PriorityDeque({self.to_list()!r})"
