from __future__ import annotations

import heapq
from collections.abc import Callable
from typing import Any, Generic, TypeVar

_T = TypeVar("_T")


class PriorityQueue(Generic[_T]):
    def __init__(self, *, max_heap: bool = False, key: Callable[[_T], Any] | None = None) -> None:
        self._heap: list[tuple[Any, int, _T]] = []
        self._counter = 0
        self._max_heap = max_heap
        self._key = key

    def push(self, item: _T, *, priority: Any | None = None) -> None:
        if priority is None:
            priority = 0 if self._key is None else self._key(item)
        if self._max_heap:
            priority = _invert(priority)
        heapq.heappush(self._heap, (priority, self._counter, item))
        self._counter += 1

    def pop(self) -> _T:
        if not self._heap:
            raise IndexError("pop from empty PriorityQueue")
        _, _, item = heapq.heappop(self._heap)
        return item

    def peek(self) -> _T:
        if not self._heap:
            raise IndexError("peek on empty PriorityQueue")
        return self._heap[0][2]

    def is_empty(self) -> bool:
        return len(self._heap) == 0

    def __len__(self) -> int:
        return len(self._heap)

    def __bool__(self) -> bool:
        return not self.is_empty()

    def __repr__(self) -> str:
        return f"PriorityQueue({len(self._heap)} items)"


def _invert(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return -value
    return value
