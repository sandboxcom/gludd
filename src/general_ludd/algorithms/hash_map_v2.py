"""Hash map v2: Robin Hood hashing, Swiss table, open addressing with linear/quadratic probing.

Pure-Python, stdlib only. Each map stores (key, value) pairs with O(1) average
operations. All use open addressing (no chaining).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

K = TypeVar("K")
V = TypeVar("V")

_SENTINEL = object()
_TOMBSTONE = object()
_EMPTY: int = 0x80
_TOMBSTONE_CTRL: int = 0xFE


def _fmix64(k: int) -> int:
    k ^= k >> 33
    k *= 0xFF51AFD7ED558CCD
    k &= 0xFFFFFFFFFFFFFFFF
    k ^= k >> 33
    k *= 0xC4CEB9FE1A85EC53
    k &= 0xFFFFFFFFFFFFFFFF
    k ^= k >> 33
    return k


# ── Robin Hood HashMap ──────────────────────────────────────────────────


@dataclass
class _RobinEntry(Generic[K, V]):
    key: object = _SENTINEL
    value: object = _SENTINEL
    psl: int = 0  # probe sequence length from ideal bucket

    @property
    def is_empty(self) -> bool:
        return self.key is _SENTINEL

    @property
    def is_tombstone(self) -> bool:
        return self.key is _TOMBSTONE

    @property
    def is_occupied(self) -> bool:
        return self.key is not _SENTINEL and self.key is not _TOMBSTONE


class RobinHoodHashMap(Generic[K, V]):
    """Open-addressing hash map with Robin Hood displacement.

    On collision, the new entry displaces existing entries with lower probe-
    sequence length, keeping the cluster's variance low.  Load factor < 0.7.
    """

    def __init__(self, capacity: int = 16) -> None:
        """Initialize an empty map with at least four slots."""
        self._cap = max(capacity, 4)
        self._size = 0
        self._entries: list[_RobinEntry[K, V]] = [_RobinEntry() for _ in range(self._cap)]

    def __len__(self) -> int:
        """Return the number of occupied entries."""
        return self._size

    def __contains__(self, key: K) -> bool:
        """Return whether key is present."""
        return self._find(key) != -1

    def __getitem__(self, key: K) -> V:
        """Return the value for key or raise KeyError."""
        i = self._find(key)
        if i == -1:
            raise KeyError(key)
        return cast(V, self._entries[i].value)

    def __setitem__(self, key: K, value: V) -> None:
        """Insert or update a key/value pair."""
        self._insert(key, value)

    def __delitem__(self, key: K) -> None:
        """Delete key or raise KeyError when absent."""
        i = self._find(key)
        if i == -1:
            raise KeyError(key)
        self._entries[i].key = _TOMBSTONE
        self._entries[i].value = _SENTINEL
        self._entries[i].psl = 0
        self._size -= 1

    def get(self, key: K, default: object = None) -> object:
        """Return the value for key, or default when absent."""
        i = self._find(key)
        return self._entries[i].value if i != -1 else default

    def items(self) -> list[tuple[K, V]]:
        """Return occupied key/value pairs."""
        return [(cast(K, e.key), cast(V, e.value)) for e in self._entries if e.is_occupied]

    def keys(self) -> list[K]:
        """Return occupied keys."""
        return [cast(K, e.key) for e in self._entries if e.is_occupied]

    def values(self) -> list[V]:
        """Return values from occupied entries."""
        return [cast(V, e.value) for e in self._entries if e.is_occupied]

    def _hash(self, key: object) -> int:
        return _fmix64(hash(key) & 0xFFFFFFFFFFFFFFFF) % self._cap

    def _find(self, key: K) -> int:
        h = self._hash(key)
        for dist in range(self._cap):
            i = (h + dist) % self._cap
            e = self._entries[i]
            if e.is_empty:
                return -1
            if e.is_occupied and e.key == key:
                return i
        return -1

    def _resize(self) -> None:
        old = self._entries
        self._cap = max(self._cap * 2, 4)
        self._entries = [_RobinEntry() for _ in range(self._cap)]
        self._size = 0
        for e in old:
            if e.is_occupied:
                self._insert(cast(K, e.key), cast(V, e.value))

    def _insert(self, key: K, value: V) -> None:
        if self._size + 1 > int(self._cap * 0.7):
            self._resize()
        h = self._hash(key)
        dist = 0
        cur_key: object = key
        cur_val = value
        for _ in range(self._cap):
            i = (h + dist) % self._cap
            e = self._entries[i]
            if not e.is_occupied:
                e.key = cur_key
                e.value = cur_val
                e.psl = dist
                self._size += 1
                return
            if e.key == cur_key:
                e.value = cur_val
                return
            if e.psl < dist:
                self._entries[i].key, cur_key = cur_key, self._entries[i].key
                e.value, cur_val = cur_val, e.value
                dist, self._entries[i].psl = self._entries[i].psl, dist
            dist += 1
        self._resize()
        self._insert(key, value)

    def __iter__(self) -> Iterator[K]:
        """Return an iterator over occupied keys."""
        return iter(self.keys())

    def __repr__(self) -> str:
        """Return a debug representation of the map."""
        return f"RobinHoodHashMap({dict(self.items())})"


# ── Swiss Table (FlatHashMap) ───────────────────────────────────────────


class SwissHashMap(Generic[K, V]):
    """Flat hash map using a separate metadata (control) array à la Swiss table.

    The control array stores per-slot metadata: 0x80 = empty, 0xFE = tombstone,
    or the low 7 bits of the key's hash (h2).  This lets us reject misses with
    a single byte compare on most probes.
    """

    def __init__(self, capacity: int = 16) -> None:
        """Initialize an empty map with at least four slots."""
        self._cap = max(capacity, 4)
        self._size = 0
        self._tombstones = 0
        self._keys: list[object] = [_SENTINEL] * self._cap
        self._values: list[object] = [_SENTINEL] * self._cap
        self._ctrl: bytearray = bytearray([_EMPTY] * self._cap)

    def __len__(self) -> int:
        """Return the number of occupied entries."""
        return self._size

    def __contains__(self, key: K) -> bool:
        """Return whether key is present."""
        return self._find(key) != -1

    def __getitem__(self, key: K) -> V:
        """Return the value for key or raise KeyError."""
        i = self._find(key)
        if i == -1:
            raise KeyError(key)
        return cast(V, self._values[i])

    def __setitem__(self, key: K, value: V) -> None:
        """Insert or update a key/value pair."""
        self._insert(key, value)

    def __delitem__(self, key: K) -> None:
        """Delete key or raise KeyError when absent."""
        i = self._find(key)
        if i == -1:
            raise KeyError(key)
        self._ctrl[i] = _TOMBSTONE_CTRL
        self._keys[i] = _TOMBSTONE
        self._values[i] = _SENTINEL
        self._size -= 1
        self._tombstones += 1

    def get(self, key: K, default: object = None) -> object:
        """Return the value for key, or default when absent."""
        i = self._find(key)
        return self._values[i] if i != -1 else default

    def items(self) -> list[tuple[K, V]]:
        """Return occupied key/value pairs."""
        result: list[tuple[K, V]] = []
        for i in range(self._cap):
            if self._ctrl[i] != _EMPTY and self._ctrl[i] != _TOMBSTONE_CTRL:
                result.append((cast(K, self._keys[i]), cast(V, self._values[i])))
        return result

    def keys(self) -> list[K]:
        """Return occupied keys."""
        return [
            cast(K, self._keys[i])
            for i in range(self._cap)
            if self._ctrl[i] != _EMPTY and self._ctrl[i] != _TOMBSTONE_CTRL
        ]

    def values(self) -> list[V]:
        """Return values from occupied entries."""
        return [
            cast(V, self._values[i])
            for i in range(self._cap)
            if self._ctrl[i] != _EMPTY and self._ctrl[i] != _TOMBSTONE_CTRL
        ]

    def _h1(self, key: object) -> int:
        return _fmix64(hash(key) & 0xFFFFFFFFFFFFFFFF) % self._cap

    def _h2(self, key: object) -> int:
        return hash(key) & 0x7F

    def _find(self, key: K) -> int:
        h2 = self._h2(key)
        h1 = self._h1(key)
        for dist in range(self._cap):
            i = (h1 + dist) % self._cap
            c = self._ctrl[i]
            if c == _EMPTY:
                return -1
            if c == h2 and self._keys[i] == key:
                return i
        return -1

    def _resize(self) -> None:
        old_keys = self._keys
        old_vals = self._values
        old_ctrl = self._ctrl
        self._cap = max(self._cap * 2, 4)
        self._keys = [_SENTINEL] * self._cap
        self._values = [_SENTINEL] * self._cap
        self._ctrl = bytearray([_EMPTY] * self._cap)
        self._size = 0
        self._tombstones = 0
        for i, c in enumerate(old_ctrl):
            if c != _EMPTY and c != _TOMBSTONE_CTRL:
                self._insert(cast(K, old_keys[i]), cast(V, old_vals[i]))

    def _insert(self, key: K, value: V) -> None:
        if self._size + self._tombstones + 1 > int(self._cap * 0.875):
            self._resize()
        h2 = self._h2(key)
        h1 = self._h1(key)
        for dist in range(self._cap):
            i = (h1 + dist) % self._cap
            c = self._ctrl[i]
            if c in (_EMPTY, _TOMBSTONE_CTRL):
                self._ctrl[i] = h2
                self._keys[i] = key
                self._values[i] = value
                if c == _TOMBSTONE_CTRL:
                    self._tombstones -= 1
                self._size += 1
                return
            if c == h2 and self._keys[i] == key:
                self._values[i] = value
                return

    def __iter__(self) -> Iterator[K]:
        """Return an iterator over occupied keys."""
        return iter(self.keys())

    def __repr__(self) -> str:
        """Return a debug representation of the map."""
        return f"SwissHashMap({dict(self.items())})"


# ── Linear Probing HashMap ──────────────────────────────────────────────


class LinearProbingHashMap(Generic[K, V]):
    """Open-addressing hash map with linear probing (stride = 1)."""

    def __init__(self, capacity: int = 16) -> None:
        """Initialize an empty map with at least four slots."""
        self._cap = max(capacity, 4)
        self._size = 0
        self._keys: list[object] = [_SENTINEL] * self._cap
        self._values: list[object] = [_SENTINEL] * self._cap

    def __len__(self) -> int:
        """Return the number of occupied entries."""
        return self._size

    def __contains__(self, key: K) -> bool:
        """Return whether key is present."""
        return self._find(key) != -1

    def __getitem__(self, key: K) -> V:
        """Return the value for key or raise KeyError."""
        i = self._find(key)
        if i == -1:
            raise KeyError(key)
        return cast(V, self._values[i])

    def __setitem__(self, key: K, value: V) -> None:
        """Insert or update a key/value pair."""
        self._insert(key, value)

    def __delitem__(self, key: K) -> None:
        """Delete key or raise KeyError when absent."""
        i = self._find(key)
        if i == -1:
            raise KeyError(key)
        self._keys[i] = _TOMBSTONE
        self._values[i] = _SENTINEL
        self._size -= 1

    def get(self, key: K, default: object = None) -> object:
        """Return the value for key, or default when absent."""
        i = self._find(key)
        return self._values[i] if i != -1 else default

    def items(self) -> list[tuple[K, V]]:
        """Return occupied key/value pairs."""
        result: list[tuple[K, V]] = []
        for i in range(self._cap):
            if self._is_occupied(i):
                result.append((cast(K, self._keys[i]), cast(V, self._values[i])))
        return result

    def keys(self) -> list[K]:
        """Return occupied keys."""
        return [cast(K, self._keys[i]) for i in range(self._cap) if self._is_occupied(i)]

    def values(self) -> list[V]:
        """Return values from occupied entries."""
        return [cast(V, self._values[i]) for i in range(self._cap) if self._is_occupied(i)]

    def _is_occupied(self, i: int) -> bool:
        return self._keys[i] is not _SENTINEL and self._keys[i] is not _TOMBSTONE

    def _hash(self, key: object) -> int:
        return _fmix64(hash(key) & 0xFFFFFFFFFFFFFFFF) % self._cap

    def _find(self, key: K) -> int:
        h = self._hash(key)
        for dist in range(self._cap):
            i = (h + dist) % self._cap
            k = self._keys[i]
            if k is _SENTINEL:
                return -1
            if k is not _TOMBSTONE and k == key:
                return i
        return -1

    def _resize(self) -> None:
        old_keys = self._keys
        old_vals = self._values
        self._cap = max(self._cap * 2, 4)
        self._keys = [_SENTINEL] * self._cap
        self._values = [_SENTINEL] * self._cap
        self._size = 0
        for i in range(len(old_keys)):
            if old_keys[i] is not _SENTINEL and old_keys[i] is not _TOMBSTONE:
                self._insert(cast(K, old_keys[i]), cast(V, old_vals[i]))

    def _insert(self, key: K, value: V) -> None:
        if self._size + 1 > int(self._cap * 0.7):
            self._resize()
        h = self._hash(key)
        for dist in range(self._cap):
            i = (h + dist) % self._cap
            k = self._keys[i]
            if k is _SENTINEL or k is _TOMBSTONE:
                self._keys[i] = key
                self._values[i] = value
                self._size += 1
                return
            if k == key:
                self._values[i] = value
                return

    def __iter__(self) -> Iterator[K]:
        """Return an iterator over occupied keys."""
        return iter(self.keys())

    def __repr__(self) -> str:
        """Return a debug representation of the map."""
        return f"LinearProbingHashMap({dict(self.items())})"


# ── Quadratic Probing HashMap ───────────────────────────────────────────


class QuadraticProbingHashMap(Generic[K, V]):
    """Open-addressing hash map with quadratic probing (c1=0, c2=1).

    Probe sequence: (h + i*i) % capacity for i = 0, 1, 2, ...
    Guarantees visit every slot when capacity is a power of two.
    """

    def __init__(self, capacity: int = 16) -> None:
        """Initialize an empty map with at least four slots."""
        self._cap = max(capacity, 4)
        self._size = 0
        self._keys: list[object] = [_SENTINEL] * self._cap
        self._values: list[object] = [_SENTINEL] * self._cap

    def __len__(self) -> int:
        """Return the number of occupied entries."""
        return self._size

    def __contains__(self, key: K) -> bool:
        """Return whether key is present."""
        return self._find(key) != -1

    def __getitem__(self, key: K) -> V:
        """Return the value for key or raise KeyError."""
        i = self._find(key)
        if i == -1:
            raise KeyError(key)
        return cast(V, self._values[i])

    def __setitem__(self, key: K, value: V) -> None:
        """Insert or update a key/value pair."""
        self._insert(key, value)

    def __delitem__(self, key: K) -> None:
        """Delete key or raise KeyError when absent."""
        i = self._find(key)
        if i == -1:
            raise KeyError(key)
        self._keys[i] = _TOMBSTONE
        self._values[i] = _SENTINEL
        self._size -= 1

    def get(self, key: K, default: object = None) -> object:
        """Return the value for key, or default when absent."""
        i = self._find(key)
        return self._values[i] if i != -1 else default

    def items(self) -> list[tuple[K, V]]:
        """Return occupied key/value pairs."""
        result: list[tuple[K, V]] = []
        for i in range(self._cap):
            if self._is_occupied(i):
                result.append((cast(K, self._keys[i]), cast(V, self._values[i])))
        return result

    def keys(self) -> list[K]:
        """Return occupied keys."""
        return [cast(K, self._keys[i]) for i in range(self._cap) if self._is_occupied(i)]

    def values(self) -> list[V]:
        """Return values from occupied entries."""
        return [cast(V, self._values[i]) for i in range(self._cap) if self._is_occupied(i)]

    def _is_occupied(self, i: int) -> bool:
        return self._keys[i] is not _SENTINEL and self._keys[i] is not _TOMBSTONE

    def _hash(self, key: object) -> int:
        return _fmix64(hash(key) & 0xFFFFFFFFFFFFFFFF) % self._cap

    def _find(self, key: K) -> int:
        h = self._hash(key)
        for i_sq in range(self._cap):
            i = (h + i_sq * i_sq) % self._cap
            k = self._keys[i]
            if k is _SENTINEL:
                return -1
            if k is not _TOMBSTONE and k == key:
                return i
        return -1

    def _resize(self) -> None:
        old_keys = self._keys
        old_vals = self._values
        self._cap = max(self._cap * 2, 4)
        self._keys = [_SENTINEL] * self._cap
        self._values = [_SENTINEL] * self._cap
        self._size = 0
        for i in range(len(old_keys)):
            if old_keys[i] is not _SENTINEL and old_keys[i] is not _TOMBSTONE:
                self._insert(cast(K, old_keys[i]), cast(V, old_vals[i]))

    def _insert(self, key: K, value: V) -> None:
        if self._size + 1 > int(self._cap * 0.7):
            self._resize()
        h = self._hash(key)
        for i_sq in range(self._cap):
            i = (h + i_sq * i_sq) % self._cap
            k = self._keys[i]
            if k is _SENTINEL or k is _TOMBSTONE:
                self._keys[i] = key
                self._values[i] = value
                self._size += 1
                return
            if k == key:
                self._values[i] = value
                return

    def __iter__(self) -> Iterator[K]:
        """Return an iterator over occupied keys."""
        return iter(self.keys())

    def __repr__(self) -> str:
        """Return a debug representation of the map."""
        return f"QuadraticProbingHashMap({dict(self.items())})"
