"""Fixed-capacity circular (ring) buffer backed by ``collections.deque``.

A bounded container that overwrites the oldest element when full,
providing amortised O(1) push/pop with a contiguous logical view.
"""

from __future__ import annotations

import copy
from collections import deque
from collections.abc import Iterator
from typing import Any


class RingBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be >= 1")
        self._capacity: int = capacity
        self._deque: deque[Any] = deque(maxlen=capacity)

    # ------------------------------------------------------------------ public

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        return len(self._deque)

    def push(self, item: Any) -> Any | None:
        evicted: Any | None = None
        if len(self._deque) == self._capacity:
            evicted = self._deque[0]
        self._deque.append(item)
        return evicted

    def pop(self) -> Any:
        if not self._deque:
            raise IndexError("pop from empty ring buffer")
        return self._deque.popleft()

    def peek(self) -> Any:
        if not self._deque:
            raise IndexError("peek from empty ring buffer")
        return self._deque[0]

    def is_empty(self) -> bool:
        return len(self._deque) == 0

    def is_full(self) -> bool:
        return len(self._deque) == self._capacity

    def clear(self) -> None:
        self._deque.clear()

    def snapshot(self) -> list[Any]:
        return list(self._deque)

    def resize(self, new_capacity: int) -> None:
        if new_capacity <= 0:
            raise ValueError("capacity must be >= 1")
        if new_capacity == self._capacity:
            return
        items = list(self._deque)
        self._capacity = new_capacity
        if len(items) > new_capacity:
            items = items[len(items) - new_capacity :]
        self._deque = deque(items, maxlen=new_capacity)

    # -------------------------------------------------------------- containers

    def __len__(self) -> int:
        return len(self._deque)

    def __bool__(self) -> bool:
        return len(self._deque) > 0

    def __iter__(self) -> Iterator[Any]:
        return iter(self._deque)

    def __contains__(self, item: Any) -> bool:
        return item in self._deque

    def __getitem__(self, index: int) -> Any:
        try:
            return self._deque[index]
        except IndexError:
            raise IndexError("ring buffer index out of range") from None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RingBuffer):
            return NotImplemented
        return list(self._deque) == list(other._deque)

    def __repr__(self) -> str:
        items = list(self._deque)
        return f"RingBuffer(capacity={self._capacity}, size={len(self._deque)}, items={items!r})"

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> RingBuffer:
        cls = type(self)
        result = cls.__new__(cls)
        result._capacity = self._capacity
        result._deque = deque(
            copy.deepcopy(list(self._deque), memo or {}),
            maxlen=self._capacity,
        )
        return result
