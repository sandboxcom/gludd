"""Conflict-free Replicated Data Types — state-based (CvRDT) and op-based
(CmRDT) implementations.

Types:
    GCounter    — grow-only counter
    PNCounter   — positive-negative counter (increment / decrement)
    GSet        — grow-only set
    TwoPhaseSet — 2P-set (add / remove, remove wins)
    LWWRegister — last-write-wins register (timestamped)
    ORSet       — observed-remove set (add / remove with unique tags)
    ORMap       — observed-remove map (keys → CRDT values)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")


# ── Utilities ──────────────────────────────────────────────────────────────────


def _merge_ints(a: int, b: int) -> int:
    return a if a > b else b


def _lamport_ts() -> int:
    """Monotonically increasing timestamp (wall-clock, 1 ms resolution)."""
    import time

    return int(time.time() * 1000)


# ── GCounter ───────────────────────────────────────────────────────────────────


class GCounter:
    """Grow-only counter — each replica owns a slot in a vector indexed by
    replica id.  ``increment`` only advances the local slot; ``merge`` takes
    the element-wise maximum."""

    __slots__ = ("_replica_id", "_state")

    def __init__(self, replica_id: str = "default") -> None:
        self._state: dict[str, int] = defaultdict(int)
        self._state[replica_id] = 0
        self._replica_id = replica_id

    @property
    def value(self) -> int:
        return sum(self._state.values())

    def increment(self, delta: int = 1) -> None:
        self._state[self._replica_id] += delta

    def merge(self, other: GCounter) -> GCounter:
        for rid, count in other._state.items():
            self._state[rid] = max(self._state[rid], count)
        return self

    @classmethod
    def _restore(cls, state: dict[str, int]) -> GCounter:
        obj = cls.__new__(cls)
        obj._state = defaultdict(int, state)
        obj._replica_id = next(iter(state), "default")
        return obj

    def state(self) -> dict[str, int]:
        return dict(self._state)


# ── PNCounter ──────────────────────────────────────────────────────────────────


class PNCounter:
    """Positive-negative counter — two GCounters, one for increments and one
    for decrements.  ``value = inc.value - dec.value``."""

    __slots__ = ("_dec", "_inc")

    def __init__(self, replica_id: str = "default") -> None:
        self._inc = GCounter(replica_id)
        self._dec = GCounter(replica_id)

    @property
    def value(self) -> int:
        return self._inc.value - self._dec.value

    def increment(self, delta: int = 1) -> None:
        self._inc.increment(delta)

    def decrement(self, delta: int = 1) -> None:
        self._dec.increment(delta)

    def merge(self, other: PNCounter) -> PNCounter:
        self._inc.merge(other._inc)
        self._dec.merge(other._dec)
        return self

    def state(self) -> dict[str, object]:
        return {"inc": self._inc.state(), "dec": self._dec.state()}


# ── GSet ───────────────────────────────────────────────────────────────────────


class GSet(Generic[T]):
    """Grow-only set — elements can only be added, never removed."""

    __slots__ = ("_payload",)

    def __init__(self) -> None:
        self._payload: set[T] = set()

    @property
    def value(self) -> frozenset[T]:
        return frozenset(self._payload)

    def add(self, elem: T) -> None:
        self._payload.add(elem)

    def merge(self, other: GSet[T]) -> GSet[T]:
        self._payload |= other._payload
        return self

    def state(self) -> set[T]:
        return set(self._payload)


# ── TwoPhaseSet ────────────────────────────────────────────────────────────────


class TwoPhaseSet(Generic[T]):
    """2P-set — elements are added to ``A`` and removed to ``R``.
    ``lookup(e) = e in A and e not in R``.  Remove wins over add."""

    __slots__ = ("_A", "_R")

    def __init__(self) -> None:
        self._A: set[T] = set()
        self._R: set[T] = set()

    @property
    def value(self) -> frozenset[T]:
        return frozenset(self._A - self._R)

    def add(self, elem: T) -> None:
        self._A.add(elem)

    def remove(self, elem: T) -> None:
        self._R.add(elem)

    def merge(self, other: TwoPhaseSet[T]) -> TwoPhaseSet[T]:
        self._A |= other._A
        self._R |= other._R
        return self

    def state(self) -> dict[str, set[T]]:
        return {"A": set(self._A), "R": set(self._R)}


# ── LWWRegister ────────────────────────────────────────────────────────────────


class LWWRegister(Generic[T]):
    """Last-write-wins register — each write carries a timestamp; on merge
    the entry with the higher timestamp wins.  Ties broken by replica id."""

    __slots__ = ("_replica", "_ts", "_value")

    def __init__(self, replica_id: str = "default", value: T | None = None) -> None:
        self._value: T | None = value
        self._ts: int = _lamport_ts()
        self._replica: str = replica_id

    @property
    def value(self) -> T | None:
        return self._value

    def assign(self, val: T) -> None:
        self._value = val
        self._ts = _lamport_ts()

    def merge(self, other: LWWRegister[T]) -> LWWRegister[T]:
        if other._ts > self._ts or (other._ts == self._ts and other._replica > self._replica):
            self._value = other._value
            self._ts = other._ts
            self._replica = other._replica
        return self

    def state(self) -> dict[str, object]:
        return {"v": self._value, "ts": self._ts, "replica": self._replica}


# ── ORSet ──────────────────────────────────────────────────────────────────────


class ORSet(Generic[T]):
    """Observed-remove set — each ``add(elem)`` generates a unique tag;
    a ``remove(elem)`` records the set of tags observed at that replica.
    An element is present iff at least one of its add-tags has not been
    observed by any concurrent remove."""

    __slots__ = ("_adds", "_rems")

    def __init__(self) -> None:
        self._adds: dict[T, set[str]] = defaultdict(set)
        self._rems: dict[T, set[str]] = defaultdict(set)

    @property
    def value(self) -> frozenset[T]:
        return frozenset(elem for elem, tags in self._adds.items() if tags - self._rems.get(elem, set()))

    def add(self, elem: T, tag: str | None = None) -> str:
        tag = tag or uuid.uuid4().hex
        self._adds[elem].add(tag)
        return tag

    def remove(self, elem: T) -> None:
        self._rems[elem] = set(self._adds.get(elem, set())) | self._rems.get(elem, set())

    def merge(self, other: ORSet[T]) -> ORSet[T]:
        for elem, tags in other._adds.items():
            self._adds[elem] |= tags
        for elem, tags in other._rems.items():
            self._rems[elem] |= tags
        return self

    def state(self) -> dict[str, object]:
        return {"adds": {k: set(v) for k, v in self._adds.items()}, "rems": {k: set(v) for k, v in self._rems.items()}}


# ── ORMap ──────────────────────────────────────────────────────────────────────


_VT = TypeVar("_VT", GCounter, PNCounter, GSet[Any], TwoPhaseSet[Any], LWWRegister[Any], ORSet[Any])


class ORMap(Generic[K, _VT]):
    """Observed-remove map — each key carries a CRDT value and a set of
    version tags.  Removing a key records the currently-observed tags for
    that key.  A key is present iff its version tags are not all covered
    by the remove-set."""

    __slots__ = ("_deltas", "_entries", "_value_factory")

    def __init__(
        self,
        value_factory: Callable[[], _VT] | None = None,
    ) -> None:
        self._entries: dict[K, tuple[set[str], _VT]] = {}
        self._deltas: dict[K, set[str]] = defaultdict(set)
        self._value_factory: Callable[[], _VT] | None = value_factory

    @property
    def value(self) -> dict[K, Any]:
        result: dict[K, Any] = {}
        for key, (tags, val) in self._entries.items():
            if tags - self._deltas.get(key, set()):
                result[key] = val.value if hasattr(val, "value") else val
        return result

    def get(self, key: K) -> _VT | None:
        if key in self._entries:
            tags, val = self._entries[key]
            if tags - self._deltas.get(key, set()):
                return val
        return None

    def put(self, key: K, op: str = "set", *args: Any, **kwargs: Any) -> None:
        tag = uuid.uuid4().hex
        if op == "set" and self._value_factory is not None:
            entry_val = self._value_factory()
        elif key in self._entries:
            _, entry_val = self._entries[key]
        else:
            return
        self._entries.setdefault(key, (set(), entry_val))
        tags, _ = self._entries[key]
        tags.add(tag)

    def remove(self, key: K) -> None:
        if key in self._entries:
            self._deltas[key] = self._entries[key][0] | self._deltas.get(key, set())
            del self._entries[key]

    def merge(self, other: ORMap[K, _VT]) -> ORMap[K, _VT]:
        for key, (tags, val) in other._entries.items():
            if key not in self._entries:
                self._entries[key] = (set(tags), val)
            else:
                cur_tags, cur_val = self._entries[key]
                cur_tags |= tags
                if hasattr(cur_val, "merge") and hasattr(val, "merge"):
                    cur_val.merge(val)
        for key, deltas in other._deltas.items():
            self._deltas[key] |= deltas
            if key in self._entries:
                cur_tags, _ = self._entries[key]
                if not (cur_tags - self._deltas[key]):
                    del self._entries[key]
        return self

    def state(self) -> dict[str, Any]:
        return {
            "entries": {k: (set(t), v.state()) for k, (t, v) in self._entries.items()},
            "deltas": {k: set(d) for k, d in self._deltas.items()},
        }
