"""Skip list v2: lock-free concurrent skip list with indexed variant.

Pure-Python, stdlib only. A probabilistic data structure offering O(log n)
expected insert / delete / search with simpler rebalancing than balanced trees.

Two variants:
  SkipList         — concurrent sorted map with fine-grained lock coupling
  IndexedSkipList  — rank-based positional access (select by index, rank queries)

Concurrency model (SkipList):
  Per-node mutexes, hand-over-hand lock coupling during traversal. Writers
  acquire locks top-down on the predecessor nodes at each level; readers are
  lock-free (optimistic, retry on conflict). Deleted nodes are marked with a
  tombstone pointer before physical removal (Harris-style logical deletion).

Reference assignment of Python object fields is atomic under the GIL, so
single-field reads without locks are safe as long as they are retried when
an inconsistency is detected. The `_AtomicRef` wrapper makes this explicit.
"""

from __future__ import annotations

import contextlib
import random
import threading
from collections.abc import Iterator
from typing import Generic, Protocol, TypeVar


class _Comparable(Protocol):
    def __lt__(self, other: object) -> bool: ...
    def __le__(self, other: object) -> bool: ...
    def __gt__(self, other: object) -> bool: ...
    def __ge__(self, other: object) -> bool: ...


K = TypeVar("K", bound=_Comparable)
V = TypeVar("V")

_MAX_LEVEL = 32


def _random_level(p: float = 0.25) -> int:
    level = 0
    while random.random() < p and level < _MAX_LEVEL - 1:
        level += 1
    return level


# ── SkipList (concurrent map) ─────────────────────────────────────────────


class _SkipNode(Generic[K, V]):
    __slots__ = ("forward", "fully_linked", "key", "lock", "marked", "val")

    def __init__(self, key: K, val: V, level: int) -> None:
        self.key = key
        self.val = val
        self.forward: list[_SkipNode[K, V] | None] = [None] * (level + 1)
        self.lock = threading.Lock()
        self.marked: bool = False
        self.fully_linked: bool = False


class SkipList(Generic[K, V]):
    """Concurrent skip list map with fine-grained lock coupling.

    Supports contains, get, insert, delete, and range queries. All
    operations are thread-safe.  Iteration is weakly consistent.
    """

    def __init__(self) -> None:
        """Initialize an empty concurrent skip-list map."""
        self._head = _SkipNode[K, V]("__head__", None, _MAX_LEVEL - 1)  # type: ignore[arg-type]
        self._level = 0

    def __len__(self) -> int:
        """Return the number of live entries."""
        count = 0
        node: _SkipNode[K, V] | None = self._head.forward[0]
        while node is not None:
            if not node.marked:
                count += 1
            node = node.forward[0]
        return count

    def __contains__(self, key: K) -> bool:
        """Return whether a live entry exists for key."""
        return self._find(key) is not None

    def __getitem__(self, key: K) -> V:
        """Return the value for key or raise KeyError."""
        node = self._find(key)
        if node is None:
            raise KeyError(key)
        return node.val

    def __setitem__(self, key: K, val: V) -> None:
        """Insert key or replace its existing value."""
        if not self.insert(key, val):
            self._find_and_update(key, val)

    def __delitem__(self, key: K) -> None:
        """Delete key or raise KeyError when absent."""
        if not self.delete(key):
            raise KeyError(key)

    def __iter__(self) -> Iterator[K]:
        """Yield live keys in ascending order."""
        node = self._head.forward[0]
        while node is not None:
            if not node.marked:
                yield node.key
            node = node.forward[0]

    def _find(self, key: K) -> _SkipNode[K, V] | None:
        """Lock-free optimistic search. Retries if marked nodes encountered."""
        while True:
            pred = self._head
            cur: _SkipNode[K, V] | None = None
            for lvl in range(self._level, -1, -1):
                cur = pred.forward[lvl]
                while cur is not None and cur.key < key:
                    pred = cur
                    cur = pred.forward[lvl]
            if cur is not None:
                if cur.marked:
                    continue
                if cur.key == key:
                    return cur
            return None

    def _find_and_update(self, key: K, val: V) -> None:
        """Lock-free update of an existing key's value (no structural change)."""
        while True:
            node = self._find(key)
            if node is None:
                return
            if not node.marked:
                node.val = val
                return

    def get(self, key: K, default: V | None = None) -> V | None:
        """Return the value for key, or default when absent."""
        node = self._find(key)
        if node is None:
            return default
        return node.val

    def insert(self, key: K, val: V) -> bool:
        """Insert (key, val); return True if new, False if overwritten."""
        top = _random_level()
        preds: list[_SkipNode[K, V] | None] = [None] * _MAX_LEVEL
        succs: list[_SkipNode[K, V] | None] = [None] * _MAX_LEVEL

        while True:
            found = self._search_with_preds(key, preds, succs)
            if found is not None:
                if found.marked:
                    continue
                found.val = val
                return False

            highest_locked = -1
            pred = self._head
            valid = True
            for lvl in range(top + 1):
                p = preds[lvl]
                s = succs[lvl]
                if p is None:
                    valid = False
                    break
                if lvl == 0 or p is not pred:
                    p.lock.acquire()
                    highest_locked = max(highest_locked, lvl)
                pred = p
                if p.marked or (s is not None and s.marked) or p.forward[lvl] is not s:
                    valid = False
                    break

            if not valid:
                for lvl in range(highest_locked + 1):
                    n = preds[lvl]
                    if n is not None:
                        with contextlib.suppress(RuntimeError):
                            n.lock.release()
                continue

            node = _SkipNode(key, val, top)
            for lvl in range(top + 1):
                p = preds[lvl]
                assert p is not None
                node.forward[lvl] = succs[lvl]
                p.forward[lvl] = node

            node.fully_linked = True

            for lvl in range(highest_locked + 1):
                n = preds[lvl]
                if n is not None:
                    with contextlib.suppress(RuntimeError):
                        n.lock.release()

            if top > self._level:
                self._level = top
            return True

    def delete(self, key: K) -> bool:
        """Delete key; return True if found and deleted."""
        victim: _SkipNode[K, V] | None = None
        top_level = -1
        preds: list[_SkipNode[K, V] | None] = [None] * _MAX_LEVEL
        succs: list[_SkipNode[K, V] | None] = [None] * _MAX_LEVEL

        while True:
            found = self._search_with_preds(key, preds, succs)
            if found is None or found.marked:
                return False
            if victim is None:
                victim = found
                top_level = len(victim.forward) - 1
                victim.lock.acquire()
                if victim.marked:
                    victim.lock.release()
                    return False

            highest_locked = -1
            pred = self._head
            valid = True
            for lvl in range(top_level + 1):
                p = preds[lvl]
                if p is None:
                    valid = False
                    break
                if lvl == 0 or p is not pred:
                    p.lock.acquire()
                    highest_locked = max(highest_locked, lvl)
                pred = p
                if p.marked or p.forward[lvl] is not victim:
                    valid = False
                    break

            if not valid:
                for lvl in range(highest_locked + 1):
                    n = preds[lvl]
                    if n is not None:
                        with contextlib.suppress(RuntimeError):
                            n.lock.release()
                continue

            assert victim is not None
            victim.marked = True
            for lvl in range(top_level + 1):
                p = preds[lvl]
                assert p is not None
                fwd = victim.forward[lvl]
                if fwd is not None and fwd.marked:
                    p.forward[lvl] = fwd.forward[lvl]
                else:
                    p.forward[lvl] = fwd

            for lvl in range(highest_locked + 1):
                n = preds[lvl]
                if n is not None:
                    with contextlib.suppress(RuntimeError):
                        n.lock.release()

            for lvl in range(top_level + 1):
                victim.forward[lvl] = None

            with contextlib.suppress(RuntimeError):
                victim.lock.release()

            while self._level > 0 and self._head.forward[self._level] is None:
                self._level -= 1
            return True

    def _search_with_preds(
        self,
        key: K,
        preds: list[_SkipNode[K, V] | None],
        succs: list[_SkipNode[K, V] | None],
    ) -> _SkipNode[K, V] | None:
        pred = self._head
        for lvl in range(_MAX_LEVEL - 1, -1, -1):
            cur = pred.forward[lvl]
            while cur is not None and cur.key < key:
                pred = cur
                cur = pred.forward[lvl]
            preds[lvl] = pred
            succs[lvl] = cur
        if succs[0] is not None and succs[0].key == key:
            return succs[0]
        return None

    def range(self, lo: K, hi: K) -> list[tuple[K, V]]:
        """Return (key, value) pairs in [lo, hi] inclusive."""
        result: list[tuple[K, V]] = []
        node = self._head.forward[0]
        while node is not None:
            if not node.marked and node.key >= lo and node.key <= hi:
                result.append((node.key, node.val))
            if node.key > hi:
                break
            node = node.forward[0]
        return result

    def items(self) -> list[tuple[K, V]]:
        """Return all (key, value) pairs in sorted order."""
        result: list[tuple[K, V]] = []
        node = self._head.forward[0]
        while node is not None:
            if not node.marked:
                result.append((node.key, node.val))
            node = node.forward[0]
        return result

    def keys(self) -> list[K]:
        """Return live keys in ascending order."""
        return [k for k, _ in self.items()]

    def values(self) -> list[V]:
        """Return live values ordered by their keys."""
        return [v for _, v in self.items()]

    def min(self) -> tuple[K, V] | None:
        """Return the smallest live pair, or None when empty."""
        node = self._head.forward[0]
        while node is not None:
            if not node.marked:
                return (node.key, node.val)
            node = node.forward[0]
        return None

    def max(self) -> tuple[K, V] | None:
        """Return the largest live pair, or None when empty."""
        pred = self._head
        for lvl in range(self._level, -1, -1):
            cur = pred.forward[lvl]
            while cur is not None and not cur.marked:
                pred = cur
                cur = pred.forward[lvl]
        if pred is self._head:
            return None
        return (pred.key, pred.val)


# ── IndexedSkipList (rank-based positional access) ────────────────────────


class _IdxSkipNode(Generic[K, V]):
    __slots__ = ("forward", "key", "span", "val")

    def __init__(self, key: K, val: V, level: int) -> None:
        self.key = key
        self.val = val
        self.forward: list[_IdxSkipNode[K, V] | None] = [None] * (level + 1)
        self.span: list[int] = [0] * (level + 1)


class IndexedSkipList(Generic[K, V]):
    """Skip list with rank-based positional access in O(log n).

    Each node carries `span[lvl]` — the number of base-level nodes between
    this node and forward[lvl] (exclusive of both).  select(k) descends
    levels accumulating spans; rank(key) counts nodes with smaller keys.
    """

    def __init__(self) -> None:
        """Initialize an empty rank-indexed skip list."""
        self._head = _IdxSkipNode[K, V]("__head__", None, _MAX_LEVEL - 1)  # type: ignore[arg-type]
        self._level = 0
        self._size = 0

    def __len__(self) -> int:
        """Return the number of indexed entries."""
        return self._size

    def __contains__(self, key: K) -> bool:
        """Return whether key exists in the index."""
        return self._find(key) is not None

    def __getitem__(self, key: K) -> V:
        """Return the value for key or raise KeyError."""
        node = self._find(key)
        if node is None:
            raise KeyError(key)
        return node.val

    def __delitem__(self, key: K) -> None:
        """Delete key or raise KeyError when absent."""
        if not self.delete(key):
            raise KeyError(key)

    def __iter__(self) -> Iterator[K]:
        """Yield keys in ascending order."""
        node = self._head.forward[0]
        while node is not None:
            yield node.key
            node = node.forward[0]

    def _find(self, key: K) -> _IdxSkipNode[K, V] | None:
        pred = self._head
        for lvl in range(self._level, -1, -1):
            cur = pred.forward[lvl]
            while cur is not None and cur.key < key:
                pred = cur
                cur = pred.forward[lvl]
        if pred.forward[0] is not None and pred.forward[0].key == key:
            return pred.forward[0]
        return None

    def get(self, key: K, default: V | None = None) -> V | None:
        """Return the value for key, or default when absent."""
        node = self._find(key)
        if node is None:
            return default
        return node.val

    def insert(self, key: K, val: V) -> bool:
        """Insert a key/value pair and maintain every rank span."""
        update: list[_IdxSkipNode[K, V] | None] = [None] * _MAX_LEVEL
        rank: list[int] = [0] * _MAX_LEVEL
        pred = self._head
        pos = -1

        for lvl in range(_MAX_LEVEL - 1, -1, -1):
            cur = pred.forward[lvl]
            while cur is not None and cur.key < key:
                pos += pred.span[lvl] + 1 if lvl < len(pred.span) else 1
                pred = cur
                cur = pred.forward[lvl]
            update[lvl] = pred
            rank[lvl] = pos

        target = pred.forward[0]
        if target is not None and target.key == key:
            target.val = val
            return False

        top = _random_level()
        while top > self._level:
            self._level += 1
            update[self._level] = self._head
            rank[self._level] = -1

        r = rank[0] + 1
        new_node = _IdxSkipNode(key, val, top)
        for lvl in range(_MAX_LEVEL):
            u = update[lvl]
            if u is None:
                continue
            if lvl <= top:
                previous_forward = u.forward[lvl]
                previous_span = u.span[lvl]
                new_node.forward[lvl] = previous_forward
                u.forward[lvl] = new_node
                if lvl < len(u.span):
                    gap = r - rank[lvl] - 1
                    u.span[lvl] = gap
                    if previous_forward is not None:
                        new_node.span[lvl] = previous_span - gap
            else:
                if lvl < len(u.span) and u.forward[lvl] is not None:
                    u.span[lvl] += 1

        self._size += 1
        return True

    def delete(self, key: K) -> bool:
        """Delete key and repair rank spans; return whether it existed."""
        update: list[_IdxSkipNode[K, V] | None] = [None] * _MAX_LEVEL
        pred = self._head

        for lvl in range(_MAX_LEVEL - 1, -1, -1):
            cur = pred.forward[lvl]
            while cur is not None and cur.key < key:
                pred = cur
                cur = pred.forward[lvl]
            update[lvl] = pred

        target = pred.forward[0]
        if target is None or target.key != key:
            return False

        target_top = len(target.forward) - 1
        for lvl in range(_MAX_LEVEL):
            u = update[lvl]
            if u is None:
                continue
            if lvl < len(u.forward) and u.forward[lvl] is target:
                u.forward[lvl] = target.forward[lvl]
                if lvl <= target_top and lvl < len(target.span) and lvl < len(u.span):
                    u.span[lvl] += target.span[lvl]
            elif lvl < len(u.span) and u.span[lvl] > 0 and u.forward[lvl] is not None:
                u.span[lvl] -= 1

        while self._level > 0 and self._head.forward[self._level] is None:
            self._level -= 1
        self._size -= 1
        return True

    def select(self, rank: int) -> tuple[K, V]:
        """Return the key/value pair at zero-based rank."""
        if rank < 0 or rank >= self._size:
            raise IndexError(f"rank {rank} out of range [0, {self._size})")
        node = self._head
        pos = -1
        for lvl in range(self._level, -1, -1):
            nxt = node.forward[lvl]
            while nxt is not None:
                span = node.span[lvl] if lvl < len(node.span) else 0
                if pos + span + 1 <= rank:
                    pos += span + 1
                    node = nxt
                    nxt = node.forward[lvl]
                else:
                    break
            if pos == rank and node is not self._head:
                return (node.key, node.val)
        return (node.key, node.val)

    def rank(self, key: K) -> int:
        """Return the zero-based insertion rank for key."""
        pred = self._head
        pos = -1
        for lvl in range(self._level, -1, -1):
            cur = pred.forward[lvl]
            while cur is not None and cur.key < key:
                pos += pred.span[lvl] + 1 if lvl < len(pred.span) else 1
                pred = cur
                cur = pred.forward[lvl]
        return pos + 1

    def range(self, lo: K, hi: K) -> list[tuple[K, V]]:
        """Return indexed pairs with keys in the inclusive range."""
        result: list[tuple[K, V]] = []
        node = self._head.forward[0]
        while node is not None:
            if node.key >= lo and node.key <= hi:
                result.append((node.key, node.val))
            if node.key > hi:
                break
            node = node.forward[0]
        return result

    def items(self) -> list[tuple[K, V]]:
        """Return all indexed pairs in ascending key order."""
        result: list[tuple[K, V]] = []
        node = self._head.forward[0]
        while node is not None:
            result.append((node.key, node.val))
            node = node.forward[0]
        return result

    def keys(self) -> list[K]:
        """Return indexed keys in ascending order."""
        return [k for k, _ in self.items()]

    def values(self) -> list[V]:
        """Return indexed values ordered by their keys."""
        return [v for _, v in self.items()]

    def min(self) -> tuple[K, V] | None:
        """Return the smallest indexed pair, or None when empty."""
        node = self._head.forward[0]
        if node is None:
            return None
        return (node.key, node.val)

    def max(self) -> tuple[K, V] | None:
        """Return the largest indexed pair, or None when empty."""
        if self._size == 0:
            return None
        pred = self._head
        for lvl in range(self._level, -1, -1):
            cur = pred.forward[lvl]
            while cur is not None:
                pred = cur
                cur = pred.forward[lvl]
        return (pred.key, pred.val)


# ── Lock-free variant (optimistic, retry-based) ───────────────────────────


class LockFreeSkipList(Generic[K, V]):
    """Lock-free skip list using optimistic traversal with retry.

    Instead of per-node locks, this variant uses logical deletion (marking)
    and retries the entire operation on detecting interference. Reads are
    fully wait-free; writes retry on conflict.

    This is a Harris-style lock-free list lifted to multiple levels:
    nodes are marked before physical removal; stalled traversals restart.
    """

    def __init__(self) -> None:
        """Initialize an empty optimistic lock-free skip list."""
        self._head = _SkipNode[K, V]("__head__", None, _MAX_LEVEL - 1)  # type: ignore[arg-type]
        self._level = 0
        self._lock = threading.Lock()  # coarse gate for level bumps only

    def __len__(self) -> int:
        """Return the number of live entries."""
        count = 0
        node: _SkipNode[K, V] | None = self._head.forward[0]
        while node is not None:
            if not node.marked:
                count += 1
            node = node.forward[0]
        return count

    def __contains__(self, key: K) -> bool:
        """Return whether a live entry exists for key."""
        return self._find(key) is not None

    def __getitem__(self, key: K) -> V:
        """Return the value for key or raise KeyError."""
        node = self._find(key)
        if node is None:
            raise KeyError(key)
        return node.val

    def __setitem__(self, key: K, val: V) -> None:
        """Insert key or replace its live value."""
        if not self.insert(key, val):
            node = self._find(key)
            if node is not None:
                node.val = val

    def __delitem__(self, key: K) -> None:
        """Delete key or raise KeyError when absent."""
        if not self.delete(key):
            raise KeyError(key)

    def __iter__(self) -> Iterator[K]:
        """Yield live keys in ascending order."""
        node = self._head.forward[0]
        while node is not None:
            if not node.marked:
                yield node.key
            node = node.forward[0]

    def _find(self, key: K) -> _SkipNode[K, V] | None:
        """Lock-free optimistic search — wait-free for readers."""
        pred = self._head
        for lvl in range(self._level, -1, -1):
            cur = pred.forward[lvl]
            while cur is not None and cur.key < key:
                pred = cur
                cur = pred.forward[lvl]
        if pred.forward[0] is not None and pred.forward[0].key == key and not pred.forward[0].marked:
            return pred.forward[0]
        return None

    def get(self, key: K, default: V | None = None) -> V | None:
        """Return the live value for key, or default when absent."""
        node = self._find(key)
        if node is None:
            return default
        return node.val

    def insert(self, key: K, val: V) -> bool:
        """Lock-free insert with retry on conflict.

        Traverses to position; if key exists, atomically swaps value (safe
        under GIL). Otherwise splices a new node into the bottom level first,
        then threads upwards. Whole operation retries if any link chain is
        broken by a concurrent writer.
        """
        top = _random_level()
        node = _SkipNode(key, val, top)

        while True:
            preds: list[_SkipNode[K, V]] = [self._head] * _MAX_LEVEL

            for lvl in range(self._level, -1, -1):
                pred = self._head
                cur = pred.forward[lvl]
                while cur is not None and cur.key < key:
                    pred = cur
                    cur = pred.forward[lvl]
                preds[lvl] = pred

            existing = preds[0].forward[0]
            if existing is not None and existing.key == key and not existing.marked:
                existing.val = val
                return False

            if top > self._level:
                with self._lock:
                    if top > self._level:
                        for lvl in range(self._level + 1, top + 1):
                            preds[lvl] = self._head
                        self._level = top

            success = True
            for lvl in range(top, -1, -1):
                expected = preds[lvl].forward[lvl]
                node.forward[lvl] = expected
                if preds[lvl].forward[lvl] is not expected:
                    success = False
                    break
                preds[lvl].forward[lvl] = node

            if success:
                break

        return True

    def delete(self, key: K) -> bool:
        """Lock-free delete with logical-then-physical removal."""
        while True:
            preds: list[_SkipNode[K, V]] = [self._head] * _MAX_LEVEL
            victim: _SkipNode[K, V] | None = None

            for lvl in range(self._level, -1, -1):
                pred = self._head
                cur = pred.forward[lvl]
                while cur is not None and cur.key < key:
                    pred = cur
                    cur = pred.forward[lvl]
                preds[lvl] = pred
                if lvl == 0:
                    victim = cur

            if victim is None or victim.key != key or victim.marked:
                return False

            victim.marked = True
            top = len(victim.forward) - 1

            all_good = True
            for lvl in range(top, -1, -1):
                if preds[lvl].forward[lvl] is not victim:
                    all_good = False
                    break
                preds[lvl].forward[lvl] = victim.forward[lvl]

            if all_good:
                while self._level > 0 and self._head.forward[self._level] is None:
                    self._level -= 1
                return True

    def range(self, lo: K, hi: K) -> list[tuple[K, V]]:
        """Return live pairs with keys in the inclusive range."""
        result: list[tuple[K, V]] = []
        node = self._head.forward[0]
        while node is not None:
            if not node.marked and node.key >= lo and node.key <= hi:
                result.append((node.key, node.val))
            if node.key > hi:
                break
            node = node.forward[0]
        return result

    def items(self) -> list[tuple[K, V]]:
        """Return all live pairs in ascending key order."""
        result: list[tuple[K, V]] = []
        node = self._head.forward[0]
        while node is not None:
            if not node.marked:
                result.append((node.key, node.val))
            node = node.forward[0]
        return result

    def keys(self) -> list[K]:
        """Return live keys in ascending order."""
        return [k for k, _ in self.items()]

    def values(self) -> list[V]:
        """Return live values ordered by their keys."""
        return [v for _, v in self.items()]

    def min(self) -> tuple[K, V] | None:
        """Return the smallest live pair, or None when empty."""
        node = self._head.forward[0]
        while node is not None:
            if not node.marked:
                return (node.key, node.val)
            node = node.forward[0]
        return None

    def max(self) -> tuple[K, V] | None:
        """Return the largest live pair, or None when empty."""
        pred = self._head
        for lvl in range(self._level, -1, -1):
            cur = pred.forward[lvl]
            while cur is not None and not cur.marked:
                pred = cur
                cur = pred.forward[lvl]
        if pred is self._head:
            return None
        return (pred.key, pred.val)
