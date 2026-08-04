"""Fixed-capacity circular (ring) buffer.

A bounded container that overwrites the oldest element when full,
providing amortised O(1) push/pop with a contiguous logical view.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any


class RingBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be >= 1")
        self._buf: list[Any] = [None] * capacity
        self._capacity: int = capacity
        self._head: int = 0
        self._size: int = 0

    # ------------------------------------------------------------------ public

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        return self._size

    def push(self, item: Any) -> Any | None:
        evicted: Any | None = None
        if self._size == self._capacity:
            evicted = self._buf[self._head]
        self._buf[self._head] = item
        self._head = (self._head + 1) % self._capacity
        if self._size < self._capacity:
            self._size += 1
        return evicted

    def pop(self) -> Any:
        if self._size == 0:
            raise IndexError("pop from empty ring buffer")
        tail = (self._head - self._size) % self._capacity
        item = self._buf[tail]
        self._buf[tail] = None
        self._size -= 1
        return item

    def peek(self) -> Any:
        if self._size == 0:
            raise IndexError("peek from empty ring buffer")
        tail = (self._head - self._size) % self._capacity
        return self._buf[tail]

    def is_empty(self) -> bool:
        return self._size == 0

    def is_full(self) -> bool:
        return self._size == self._capacity

    def clear(self) -> None:
        for i in range(self._capacity):
            self._buf[i] = None
        self._head = 0
        self._size = 0

    def snapshot(self) -> list[Any]:
        return list(self)

    def resize(self, new_capacity: int) -> None:
        if new_capacity <= 0:
            raise ValueError("capacity must be >= 1")
        if new_capacity == self._capacity:
            return
        snapshot = list(self)
        if len(snapshot) > new_capacity:
            snapshot = snapshot[len(snapshot) - new_capacity :]
        self._buf = [None] * new_capacity
        self._capacity = new_capacity
        self._head = 0
        self._size = 0
        for item in snapshot:
            self.push(item)

    # -------------------------------------------------------------- containers

    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def __iter__(self) -> Iterator[Any]:
        tail = (self._head - self._size) % self._capacity
        for i in range(self._size):
            yield self._buf[(tail + i) % self._capacity]

    def __contains__(self, item: Any) -> bool:
        return any(existing == item for existing in self)

    def __getitem__(self, index: int) -> Any:
        size = self._size
        if index < -size or index >= size:
            raise IndexError("ring buffer index out of range")
        if index < 0:
            index += size
        tail = (self._head - size) % self._capacity
        return self._buf[(tail + index) % self._capacity]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RingBuffer):
            return NotImplemented
        if self._size != other._size:
            return False
        return list(self) == list(other)

    def __repr__(self) -> str:
        items = list(self)
        return f"RingBuffer(capacity={self._capacity}, size={self._size}, items={items!r})"

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> RingBuffer:
        cls = type(self)
        result = cls.__new__(cls)
        result._buf = copy.deepcopy(self._buf, memo or {})
        result._capacity = self._capacity
        result._head = self._head
        result._size = self._size
        return result
