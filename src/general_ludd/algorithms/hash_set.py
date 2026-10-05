"""Open-addressing hash set with linear probing and dynamic resizing.

The implementation includes union, intersection, difference, and subset
operations.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator
from typing import Generic, TypeVar, cast

T = TypeVar("T")

_SENTINEL = object()
_TOMBSTONE = object()


class HashSet(Generic[T]):
    """Represent ``HashSet`` values."""
    _DEFAULT_CAPACITY: int = 8
    _LOAD_FACTOR_THRESHOLD: float = 0.7

    _slots: list[object]
    _size: int
    _tombstones: int

    def __init__(self, items: Iterable[T] | None = None) -> None:
        """Initialize a ``HashSet`` instance."""
        self._slots = [_SENTINEL] * self._DEFAULT_CAPACITY
        self._size = 0
        self._tombstones = 0
        if items is not None:
            for item in items:
                self.add(item)

    def __len__(self) -> int:
        """Return the number of stored items."""
        return self._size

    def __contains__(self, item: T) -> bool:
        """Return whether the item is present."""
        return self.contains(item)

    def __iter__(self) -> Iterator[T]:
        """Iterate over the stored items."""
        for slot in self._slots:
            if slot is not _SENTINEL and slot is not _TOMBSTONE:
                yield cast(T, slot)

    def __repr__(self) -> str:
        """Return a developer-readable representation."""
        return f"HashSet({list(self)!r})"

    def __eq__(self, other: object) -> bool:
        """Compare this instance with another value."""
        if not isinstance(other, HashSet):
            return NotImplemented
        if len(self) != len(other):
            return False
        return self.issubset(other)

    def __or__(self, other: HashSet[T]) -> HashSet[T]:
        """Combine with another value."""
        return self.union(other)

    def __and__(self, other: HashSet[T]) -> HashSet[T]:
        """Intersect with another value."""
        return self.intersection(other)

    def __sub__(self, other: HashSet[T]) -> HashSet[T]:
        """Subtract another value."""
        return self.difference(other)

    def __le__(self, other: HashSet[T]) -> bool:
        """Return whether this value is a subset."""
        return self.issubset(other)

    def __lt__(self, other: HashSet[T]) -> bool:
        """Compare this instance with another value."""
        return self.issubset(other) and len(self) < len(other)

    def __ge__(self, other: HashSet[T]) -> bool:
        """Execute ``__ge__``."""
        return other.issubset(self)

    def __gt__(self, other: HashSet[T]) -> bool:
        """Execute ``__gt__``."""
        return other.issubset(self) and len(self) > len(other)

    # ── probe helpers ─────────────────────────────────────────────

    def _probe(self, item: object) -> int:
        cap = len(self._slots)
        idx = (hash(item) & 0x7FFFFFFF) % cap
        first_tombstone: int | None = None
        while True:
            slot = self._slots[idx]
            if slot is _SENTINEL:
                return first_tombstone if first_tombstone is not None else idx
            if slot is _TOMBSTONE:
                if first_tombstone is None:
                    first_tombstone = idx
            elif slot == item:
                return idx
            idx = (idx + 1) % cap

    def _find(self, item: object) -> int:
        cap = len(self._slots)
        idx = (hash(item) & 0x7FFFFFFF) % cap
        while True:
            slot = self._slots[idx]
            if slot is _SENTINEL:
                return -1
            if slot is not _TOMBSTONE and slot == item:
                return idx
            idx = (idx + 1) % cap

    def _resize(self, new_cap: int) -> None:
        old = self._slots
        self._slots = [_SENTINEL] * new_cap
        self._size = 0
        self._tombstones = 0
        for slot in old:
            if slot is not _SENTINEL and slot is not _TOMBSTONE:
                self._add_no_resize(cast(T, slot))

    def _add_no_resize(self, item: T) -> None:
        idx = self._probe(item)
        if self._slots[idx] is _TOMBSTONE:
            self._tombstones -= 1
        self._slots[idx] = item
        self._size += 1

    # ── public API ────────────────────────────────────────────────

    def add(self, item: T) -> None:
        """Add the value."""
        if (self._size + self._tombstones + 1) > self._LOAD_FACTOR_THRESHOLD * len(self._slots):
            self._resize(len(self._slots) * 2)
        idx = self._probe(item)
        slot = self._slots[idx]
        if slot is _SENTINEL or slot is _TOMBSTONE:
            if slot is _TOMBSTONE:
                self._tombstones -= 1
            self._slots[idx] = item
            self._size += 1

    def contains(self, item: T) -> bool:
        """Execute ``contains``."""
        return self._find(item) != -1

    def remove(self, item: T) -> None:
        """Execute ``remove``."""
        idx = self._find(item)
        if idx == -1:
            raise KeyError(item)
        self._slots[idx] = _TOMBSTONE
        self._tombstones += 1
        self._size -= 1

        if self._tombstones > len(self._slots) // 4 and len(self._slots) > self._DEFAULT_CAPACITY:
            self._resize(len(self._slots) // 2)

    def discard(self, item: T) -> None:
        """Execute ``discard``."""
        with contextlib.suppress(KeyError):
            self.remove(item)

    def clear(self) -> None:
        """Clear the value."""
        self._slots = [_SENTINEL] * self._DEFAULT_CAPACITY
        self._size = 0
        self._tombstones = 0

    # ── set-theoretic operations ──────────────────────────────────

    def union(self, other: HashSet[T]) -> HashSet[T]:
        """Execute ``union``."""
        result: HashSet[T] = HashSet(self)
        for item in other:
            result.add(item)
        return result

    def intersection(self, other: HashSet[T]) -> HashSet[T]:
        """Execute ``intersection``."""
        result: HashSet[T] = HashSet()
        smaller = self if len(self) <= len(other) else other
        larger = other if smaller is self else self
        for item in smaller:
            if larger.contains(item):
                result.add(item)
        return result

    def difference(self, other: HashSet[T]) -> HashSet[T]:
        """Execute ``difference``."""
        result: HashSet[T] = HashSet()
        for item in self:
            if not other.contains(item):
                result.add(item)
        return result

    def symmetric_difference(self, other: HashSet[T]) -> HashSet[T]:
        """Execute ``symmetric_difference``."""
        return self.union(other).difference(self.intersection(other))

    def issubset(self, other: HashSet[T]) -> bool:
        """Execute ``issubset``."""
        if len(self) > len(other):
            return False
        return all(other.contains(item) for item in self)

    def issuperset(self, other: HashSet[T]) -> bool:
        """Execute ``issuperset``."""
        return other.issubset(self)

    def isdisjoint(self, other: HashSet[T]) -> bool:
        """Execute ``isdisjoint``."""
        return len(self.intersection(other)) == 0

    def update(self, other: HashSet[T]) -> None:
        """Execute ``update``."""
        for item in other:
            self.add(item)

    def intersection_update(self, other: HashSet[T]) -> None:
        """Execute ``intersection_update``."""
        to_remove: list[T] = []
        for item in self:
            if not other.contains(item):
                to_remove.append(item)
        for item in to_remove:
            self.remove(item)

    def difference_update(self, other: HashSet[T]) -> None:
        """Execute ``difference_update``."""
        for item in other:
            self.discard(item)

    def copy(self) -> HashSet[T]:
        """Execute ``copy``."""
        return HashSet(self)

    @property
    def capacity(self) -> int:
        """Execute ``capacity``."""
        return len(self._slots)

    @property
    def size(self) -> int:
        """Execute ``size``."""
        return self._size
