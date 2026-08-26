"""Clojure-style persistent vector: 32-way trie, tail, transient, path copying.

RRB-tree-inspired relaxed radix balanced design with O(log₃₂ n) lookup,
update, and conj.  Transient support for batch mutation; path copying for
structural sharing on every non-transient mutation.

Pure-Python, stdlib only.  Follows project conventions (__slots__, Generic,
from __future__ import annotations).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T")

_SHIFT_INC = 5
_BRANCH = 1 << _SHIFT_INC  # 32
_MASK = _BRANCH - 1


def _node_new() -> list[Any]:
    return [None] * _BRANCH


def _node_copy_set(node: list[Any], idx: int, val: Any) -> list[Any]:
    c = node[:]
    c[idx] = val
    return c


def _tailoff(cnt: int) -> int:
    if cnt < _BRANCH:
        return 0
    return ((cnt - 1) >> _SHIFT_INC) << _SHIFT_INC


def _new_path(shift: int, node: list[Any]) -> list[Any]:
    """Create a path from shift down to leaf, storing node at the leaf."""
    if shift == 0:
        return node
    n = _node_new()
    n[0] = _new_path(shift - _SHIFT_INC, node)
    return n


def _push_tail(cnt: int, shift: int, root: list[Any], tail: list[Any]) -> list[Any]:
    """Insert tail into the trie via path copying.  cnt is the count BEFORE conj."""
    tail_off = cnt - len(tail)
    subidx = (tail_off >> shift) & _MASK
    if shift == _SHIFT_INC:
        return _node_copy_set(root, subidx, tail)
    child = root[subidx]
    if child is None:
        child = _node_new()
    ns = _push_tail(cnt, shift - _SHIFT_INC, child, tail)
    return _node_copy_set(root, subidx, ns)


def _array_for(cnt: int, shift: int, root: list[Any], tail: list[Any]) -> list[Any]:
    if cnt == 0:
        return tail
    node = root
    for level in range(shift, 0, -_SHIFT_INC):
        idx = (cnt >> level) & _MASK
        n = node[idx]
        if n is None:
            return tail
        node = n
    return node


def _pop_tail(cnt: int, shift: int, root: list[Any]) -> list[Any] | None:
    """Remove the tail leaf from the trie.  cnt is the count AFTER pop."""
    subidx = (cnt >> shift) & _MASK
    if shift > _SHIFT_INC:
        child = root[subidx]
        if child is None:
            return None
        newchild = _pop_tail(cnt, shift - _SHIFT_INC, child)
        if newchild is None:
            if subidx == 0:
                return None
            return _node_copy_set(root, subidx, None)
        return _node_copy_set(root, subidx, newchild)
    # shift == _SHIFT_INC: leaf level
    if subidx == 0:
        return None
    return _node_copy_set(root, subidx, None)


class PersistentVector(Generic[T]):
    """Immutable persistent vector with structural sharing.

    O(log₃₂ n) lookup, update, conj, pop.  32-way branching for
    shallow depth; tail optimization avoids trie for last ≤32 elements.
    """

    __slots__ = ("_cnt", "_root", "_shift", "_tail")

    def __init__(
        self,
        cnt: int,
        shift: int,
        root: list[Any],
        tail: list[Any],
    ) -> None:
        """Initialize an immutable vector from its trie components."""
        self._cnt = cnt
        self._shift = shift
        self._root = root
        self._tail = tail

    @classmethod
    def empty(cls) -> PersistentVector[T]:
        """Return a new empty persistent vector."""
        return cls(0, _SHIFT_INC, _node_new(), [])

    @staticmethod
    def from_iterable(items: Iterable[T]) -> PersistentVector[T]:
        """Build a persistent vector from an iterable."""
        tv: TransientVector[T] = TransientVector.empty()
        for item in items:
            tv = tv.conj(item)
        return tv.persistent()

    # ---- Public immutable API ----

    def __len__(self) -> int:
        """Return the number of elements."""
        return self._cnt

    def __getitem__(self, index: int) -> Any:
        """Return an element by positive or negative index."""
        if index < 0:
            index += self._cnt
        if not (0 <= index < self._cnt):
            raise IndexError(f"index {index} out of range for length {self._cnt}")
        arr = self._array_for(index)
        return arr[index & _MASK]

    def __iter__(self) -> Iterator[Any]:
        """Iterate over elements in insertion order."""
        for i in range(self._cnt):
            yield self[i]

    def __eq__(self, other: object) -> bool:
        """Compare vectors by type, length, and ordered contents."""
        if not isinstance(other, PersistentVector):
            return NotImplemented
        if self._cnt != other._cnt:
            return False
        return all(self[i] == other[i] for i in range(self._cnt))

    def __repr__(self) -> str:
        """Return a bounded representation of the vector contents."""
        if self._cnt <= 20:
            items = ", ".join(repr(self[i]) for i in range(self._cnt))
        else:
            front = ", ".join(repr(self[i]) for i in range(10))
            back = ", ".join(repr(self[i]) for i in range(self._cnt - 5, self._cnt))
            items = f"{front}, ..., {back}"
        return f"PV([{items}])"

    def conj(self, val: T) -> PersistentVector[T]:
        """Return a new vector with val appended."""
        if len(self._tail) < _BRANCH:
            newtail = [*self._tail, val]
            return PersistentVector(self._cnt + 1, self._shift, self._root, newtail)

        # tail is full; push it into the trie
        newroot = self._root
        newshift = self._shift
        tail_off = self._cnt - len(self._tail)

        if (tail_off >> _SHIFT_INC) >= (1 << self._shift):
            # root overflow — push level up
            nr = _node_new()
            nr[0] = newroot
            nr[1] = _new_path(self._shift, self._tail)
            newroot = nr
            newshift += _SHIFT_INC
        else:
            newroot = _push_tail(self._cnt, self._shift, newroot, self._tail)

        return PersistentVector(self._cnt + 1, newshift, newroot, [val])

    def pop(self) -> PersistentVector[T]:
        """Return a new vector with the last element removed."""
        if self._cnt == 0:
            raise IndexError("pop from empty vector")
        if self._cnt == 1:
            return PersistentVector(0, _SHIFT_INC, _node_new(), [])

        if len(self._tail) > 1:
            newtail = self._tail[:-1]
            return PersistentVector(self._cnt - 1, self._shift, self._root, newtail)

        # tail has 0 or 1 element; pull a new tail from the trie
        newcnt = self._cnt - 1
        newtail = list(_array_for(newcnt - 1, self._shift, self._root, self._tail))
        newroot = self._root
        newshift = self._shift

        nr = _pop_tail(newcnt, self._shift, self._root)
        newroot = nr if nr is not None else _node_new()

        if newshift > _SHIFT_INC and newroot[0] is not None and newroot[1] is None:
            newroot = newroot[0]
            newshift -= _SHIFT_INC

        return PersistentVector(newcnt, newshift, newroot, newtail)

    def assoc(self, index: int, val: T) -> PersistentVector[T]:
        """Return a new vector with index replaced by val."""
        if index < 0:
            index += self._cnt
        if not (0 <= index < self._cnt):
            raise IndexError(f"index {index} out of range for length {self._cnt}")
        if index >= _tailoff(self._cnt):
            newtail = self._tail[:]
            newtail[index & _MASK] = val
            return PersistentVector(self._cnt, self._shift, self._root, newtail)
        newroot = self._do_assoc(self._shift, self._root, index, val)
        return PersistentVector(self._cnt, self._shift, newroot, self._tail)

    def _do_assoc(self, shift: int, node: list[Any], index: int, val: T) -> list[Any]:
        subidx = (index >> shift) & _MASK
        if shift == 0:
            return _node_copy_set(node, subidx, val)
        child = node[subidx]
        ns = self._do_assoc(shift - _SHIFT_INC, child, index, val)
        return _node_copy_set(node, subidx, ns)

    def peek(self) -> T | None:
        """Return the final element, or ``None`` when empty."""
        if self._cnt == 0:
            return None
        return cast(T, self[self._cnt - 1])

    def _array_for(self, index: int) -> list[Any]:
        if index >= _tailoff(self._cnt):
            return self._tail
        node = self._root
        for level in range(self._shift, 0, -_SHIFT_INC):
            idx = (index >> level) & _MASK
            node = node[idx]
        return node

    # ---- Transient bridge ----

    def transient(self) -> TransientVector[T]:
        """Return a mutable transient view for batched changes."""
        return TransientVector(self._cnt, self._shift, self._root, self._tail)


class TransientVector(Generic[T]):
    """Mutable transient vector for batch updates; call .persistent() to seal.

    Operations are O(log₃₂ n) but avoid path copying (mutate in place).
    A transient vector must NOT be used after calling .persistent().
    """

    __slots__ = ("_cnt", "_editable", "_root", "_sealed", "_shift", "_tail")

    def __init__(
        self,
        cnt: int,
        shift: int,
        root: list[Any],
        tail: list[Any],
    ) -> None:
        """Initialize a transient vector from mutable trie components."""
        self._cnt = cnt
        self._shift = shift
        self._root = root
        self._tail = tail
        self._editable: set[int] = {id(root), id(tail)}
        self._sealed = False

    @classmethod
    def empty(cls) -> TransientVector[T]:
        """Return an empty transient vector."""
        r = _node_new()
        return cls(0, _SHIFT_INC, r, [])

    def _ensure_editable(self, node: list[Any]) -> list[Any]:
        if id(node) in self._editable:
            return node
        n = node[:]
        self._editable.add(id(n))
        return n

    def conj(self, val: T) -> TransientVector[T]:
        """Append a value in place and return this transient vector."""
        if self._sealed:
            raise RuntimeError("transient vector already sealed")

        if len(self._tail) < _BRANCH:
            self._tail.append(val)
            self._cnt += 1
            return self

        # tail is full; push it into the trie
        newroot = self._ensure_editable(self._root)
        newshift = self._shift
        tail_off = self._cnt - len(self._tail)

        if (tail_off >> _SHIFT_INC) >= (1 << self._shift):
            nr = _node_new()
            self._editable.add(id(nr))
            nr[0] = newroot
            nr[1] = _new_path(self._shift, self._tail)
            newroot = nr
            newshift += _SHIFT_INC
        else:
            newroot = _push_tail(self._cnt, self._shift, newroot, self._tail)

        self._root = newroot
        self._shift = newshift
        self._tail = [val]
        self._editable.add(id(self._tail))
        self._cnt += 1
        return self

    def pop(self) -> TransientVector[T]:
        """Remove the final value in place and return this transient vector."""
        if self._sealed:
            raise RuntimeError("transient vector already sealed")
        if self._cnt == 0:
            raise IndexError("pop from empty transient vector")
        if self._cnt == 1:
            self._cnt = 0
            self._tail = []
            self._editable.add(id(self._tail))
            self._root = _node_new()
            self._editable.add(id(self._root))
            self._shift = _SHIFT_INC
            return self
        if len(self._tail) > 1:
            self._tail.pop()
            self._cnt -= 1
            return self

        # tail has 0 or 1 element; pull a new tail from the trie
        newcnt = self._cnt - 1
        newtail = list(_array_for(newcnt - 1, self._shift, self._root, self._tail))
        self._editable.add(id(newtail))

        newroot = self._ensure_editable(self._root)
        newshift = self._shift
        nr = _pop_tail(newcnt, self._shift, newroot)
        if nr is not None:
            newroot = nr
            self._editable.add(id(newroot))
        else:
            newroot = _node_new()
            self._editable.add(id(newroot))

        if newshift > _SHIFT_INC and newroot[0] is not None and newroot[1] is None:
            newroot = newroot[0]
            newshift -= _SHIFT_INC

        self._root = newroot
        self._shift = newshift
        self._tail = newtail
        self._cnt = newcnt
        return self

    def assoc(self, index: int, val: T) -> TransientVector[T]:
        """Replace an indexed value in place and return this transient vector."""
        if self._sealed:
            raise RuntimeError("transient vector already sealed")
        if index < 0:
            index += self._cnt
        if not (0 <= index < self._cnt):
            raise IndexError(f"index {index} out of range for length {self._cnt}")
        if index >= _tailoff(self._cnt):
            self._tail[index & _MASK] = val
            return self
        self._root = self._do_assoc(self._shift, self._root, index, val)
        return self

    def _do_assoc(self, shift: int, node: list[Any], index: int, val: T) -> list[Any]:
        node = self._ensure_editable(node)
        subidx = (index >> shift) & _MASK
        if shift == 0:
            node[subidx] = val
            return node
        node[subidx] = self._do_assoc(shift - _SHIFT_INC, node[subidx], index, val)
        return node

    def persistent(self) -> PersistentVector[T]:
        """Seal this transient and return its immutable vector."""
        self._sealed = True
        return PersistentVector(self._cnt, self._shift, self._root, self._tail)

    def __len__(self) -> int:
        """Return the number of elements."""
        return self._cnt

    def __getitem__(self, index: int) -> Any:
        """Return an element by positive or negative index."""
        if index < 0:
            index += self._cnt
        if not (0 <= index < self._cnt):
            raise IndexError(f"index {index} out of range for length {self._cnt}")
        arr = self._array_for(index)
        return arr[index & _MASK]

    def _array_for(self, index: int) -> list[Any]:
        if index >= _tailoff(self._cnt):
            return self._tail
        node = self._root
        for level in range(self._shift, 0, -_SHIFT_INC):
            idx = (index >> level) & _MASK
            node = node[idx]
        return node
