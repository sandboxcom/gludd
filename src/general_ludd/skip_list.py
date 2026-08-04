from __future__ import annotations

import math
import random
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class _SkipNode(Generic[K, V]):
    __slots__ = ("forward", "key", "value")

    def __init__(self, key: K, value: V, level: int) -> None:
        self.key: K = key
        self.value: V = value
        self.forward: list[_SkipNode[K, V] | None] = [None] * level


class SkipList(Generic[K, V]):
    """A probabilistic skip list with O(log n) expected insert/search/delete.

    Implements ordered iteration, range queries, and __contains__.
    Uses geometric distribution for level generation (p = 0.5).
    """

    _P: float = 0.5

    def __init__(self) -> None:
        self._max_level: int = 1
        self._head: _SkipNode[K, V] = _SkipNode(key=None, value=None, level=self._max_level)  # type: ignore[arg-type]
        self._size: int = 0
        self._level_count: int = 1
        self._sentinel_key: K = self._head.key  # type: ignore[assignment]

    def _random_level(self) -> int:
        level = 1
        m = max(1, int(math.log2(max(self._size, 1) + 1)) + 1)
        while random.random() < self._P and level < m:
            level += 1
        return level

    def _ensure_head_level(self, level: int) -> None:
        if level > self._level_count:
            self._head.forward.extend([None] * (level - self._level_count))
            self._level_count = level
            if level > self._max_level:
                self._max_level = level

    def insert(self, key: K, value: V) -> None:
        update: list[_SkipNode[K, V] | None] = [None] * self._level_count
        current = self._head
        for i in range(self._level_count - 1, -1, -1):
            while current.forward[i] is not None and current.forward[i].key < key:  # type: ignore[operator]
                current = current.forward[i]
            update[i] = current

        current = current.forward[0] if current.forward[0] is not None else None
        if current is not None and current.key == key:  # type: ignore[operator]
            current.value = value
            return

        new_level = self._random_level()
        self._ensure_head_level(new_level)

        if new_level > len(update):
            for _i in range(len(update), new_level):
                update.append(self._head)

        node = _SkipNode(key, value, new_level)
        for i in range(new_level):
            node.forward[i] = update[i].forward[i] if update[i] is not None else None
            if update[i] is not None:
                update[i].forward[i] = node
        self._size += 1

    def search(self, key: K) -> V | None:
        current = self._head
        for i in range(self._level_count - 1, -1, -1):
            while current.forward[i] is not None and current.forward[i].key < key:  # type: ignore[operator]
                current = current.forward[i]
        current = current.forward[0] if current.forward[0] is not None else None
        if current is not None and current.key == key:  # type: ignore[operator]
            return current.value
        return None

    def delete(self, key: K) -> bool:
        update: list[_SkipNode[K, V] | None] = [None] * self._level_count
        current = self._head
        for i in range(self._level_count - 1, -1, -1):
            while current.forward[i] is not None and current.forward[i].key < key:  # type: ignore[operator]
                current = current.forward[i]
            update[i] = current

        current = current.forward[0] if current.forward[0] is not None else None
        if current is None or current.key != key:  # type: ignore[operator]
            return False

        for i in range(self._level_count):
            if update[i] is not None and update[i].forward[i] == current:
                update[i].forward[i] = current.forward[i]

        while self._level_count > 1 and self._head.forward[self._level_count - 1] is None:
            self._level_count -= 1
            self._head.forward.pop()

        self._size -= 1
        return True

    def range_query(self, low: K, high: K) -> list[tuple[K, V]]:
        result: list[tuple[K, V]] = []
        current = self._head.forward[0]
        while current is not None and current.key < low:  # type: ignore[operator]
            current = current.forward[0]
        while current is not None and current.key <= high:  # type: ignore[operator]
            result.append((current.key, current.value))
            current = current.forward[0]
        return result

    def __iter__(self):
        current = self._head.forward[0]
        while current is not None:
            yield (current.key, current.value)
            current = current.forward[0]

    def __reversed__(self):
        keys_vals: list[tuple[K, V]] = list(self)
        yield from reversed(keys_vals)

    def __len__(self) -> int:
        return self._size

    def __contains__(self, key: K) -> bool:
        return self.search(key) is not None

    def __repr__(self) -> str:
        return f"SkipList(size={self._size}, max_level={self._max_level})"
