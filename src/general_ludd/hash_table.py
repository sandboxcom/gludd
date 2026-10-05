"""Hash table with chaining and ConsistentHashRing for distributed key placement."""

from __future__ import annotations

import bisect
import hashlib
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")

_DEFAULT_CAPACITY = 8
_LOAD_FACTOR_THRESHOLD = 0.75


class _Entry(Generic[K, V]):
    __slots__ = ("key", "next", "value")

    def __init__(self, key: K, value: V) -> None:
        self.key = key
        self.value = value
        self.next: _Entry[K, V] | None = None


class HashTable(Generic[K, V]):
    """Hash table with chaining."""

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        """Initialize the hash table with a capacity."""
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._size = 0
        self._buckets: list[_Entry[K, V] | None] = [None] * capacity

    @property
    def capacity(self) -> int:
        """Return the bucket capacity."""
        return self._capacity

    @property
    def size(self) -> int:
        """Return the number of entries."""
        return self._size

    def put(self, key: K, value: V) -> None:
        """Insert or update a key-value pair."""
        idx = self._index(key)
        entry = self._buckets[idx]
        while entry is not None:
            if entry.key == key:
                entry.value = value
                return
            entry = entry.next
        new_entry = _Entry(key, value)
        new_entry.next = self._buckets[idx]
        self._buckets[idx] = new_entry
        self._size += 1
        if self._size > self._capacity * _LOAD_FACTOR_THRESHOLD:
            self._rehash()

    def get(self, key: K) -> V:
        """Return the value for a key."""
        entry = self._find(key)
        if entry is None:
            raise KeyError(f"missing key: {key!r}")
        return entry.value

    def delete(self, key: K) -> None:
        """Remove a key from the table."""
        idx = self._index(key)
        entry = self._buckets[idx]
        prev: _Entry[K, V] | None = None
        while entry is not None:
            if entry.key == key:
                if prev is None:
                    self._buckets[idx] = entry.next
                else:
                    prev.next = entry.next
                self._size -= 1
                return
            prev = entry
            entry = entry.next
        raise KeyError(f"absent key: {key!r}")

    def contains(self, key: K) -> bool:
        """Return whether the key is present."""
        return self._find(key) is not None

    def _find(self, key: K) -> _Entry[K, V] | None:
        entry = self._buckets[self._index(key)]
        while entry is not None:
            if entry.key == key:
                return entry
            entry = entry.next
        return None

    def _index(self, key: K) -> int:
        return hash(key) % self._capacity

    def _rehash(self) -> None:
        old_buckets = self._buckets
        self._capacity *= 2
        self._buckets = [None] * self._capacity
        self._size = 0
        for head in old_buckets:
            entry = head
            while entry is not None:
                self.put(entry.key, entry.value)
                entry = entry.next


def _hash_key(key: str) -> int:
    return int(hashlib.md5(key.encode("utf-8"), usedforsecurity=False).hexdigest(), 16)


class ConsistentHashRing:
    """Consistent hash ring mapping keys to nodes."""

    def __init__(self, virtual_nodes: int = 64) -> None:
        """Initialize the ring with a virtual node count."""
        if virtual_nodes < 1:
            raise ValueError("virtual_nodes must be >= 1")
        self._virtual_nodes = virtual_nodes
        self._ring: list[int] = []
        self._node_map: dict[int, str] = {}

    @property
    def node_count(self) -> int:
        """Return the number of distinct nodes."""
        return len(set(self._node_map.values()))

    def add_node(self, node_id: str) -> None:
        """Add a node to the ring."""
        if node_id in set(self._node_map.values()):
            return
        for i in range(self._virtual_nodes):
            vkey = f"{node_id}:vn{i}"
            h = _hash_key(vkey)
            pos = bisect.bisect_left(self._ring, h)
            self._ring.insert(pos, h)
            self._node_map[h] = node_id

    def remove_node(self, node_id: str) -> None:
        """Remove a node from the ring."""
        if node_id not in set(self._node_map.values()):
            raise ValueError(f"unknown node: {node_id!r}")
        to_remove = [h for h, n in self._node_map.items() if n == node_id]
        for h in to_remove:
            idx = bisect.bisect_left(self._ring, h)
            if idx < len(self._ring) and self._ring[idx] == h:
                del self._ring[idx]
            del self._node_map[h]

    def get_node(self, key: str) -> str:
        """Return the node responsible for a key."""
        if not self._ring:
            raise ValueError("ring is empty — add nodes first")
        h = _hash_key(key)
        pos = bisect.bisect_left(self._ring, h)
        if pos == len(self._ring):
            pos = 0
        return self._node_map[self._ring[pos]]
