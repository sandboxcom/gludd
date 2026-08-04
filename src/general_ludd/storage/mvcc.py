"""Multi-version concurrency control (MVCC) key-value store.

Transactions provide snapshot isolation: each transaction sees a consistent
snapshot of committed data at its start time.  Write intents are buffered
locally and atomically committed only if no conflicting writes occurred.
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")

_next_id = itertools.count(1)
_id_lock = threading.Lock()


def _new_id() -> int:
    with _id_lock:
        return next(_next_id)


@dataclass(slots=True)
class _Version(Generic[V]):
    txid: int
    value: V
    previous: _Version[V] | None = None


class MVCCStore(Generic[K, V]):
    """Versioned key-value store with snapshot-isolation transactions.

    Each key points to a chain of committed versions (newest first).
    Transactions read the latest version committed before their snapshot
    and buffer writes locally for atomic commit with conflict detection.
    """

    def __init__(self) -> None:
        self._data: dict[K, _Version[V]] = {}
        self._lock = threading.Lock()

    def _latest_committed(self, key: K, before_txid: int) -> _Version[V] | None:
        v = self._data.get(key)
        while v is not None and v.txid >= before_txid:
            v = v.previous
        return v

    def _read_committed(self, key: K, before_txid: int) -> V:
        v = self._latest_committed(key, before_txid)
        if v is None:
            raise KeyError(f"key {key!r} not found")
        return v.value

    def _read_committed_optional(self, key: K, before_txid: int) -> V | None:
        v = self._latest_committed(key, before_txid)
        return v.value if v is not None else None

    # -- public read (current committed state) ---------------------------------

    def get(self, key: K) -> V:
        with self._lock:
            v = self._data.get(key)
            if v is None:
                raise KeyError(f"key {key!r} not found")
            return v.value

    def get_optional(self, key: K) -> V | None:
        with self._lock:
            v = self._data.get(key)
            return v.value if v is not None else None

    def contains(self, key: K) -> bool:
        with self._lock:
            return key in self._data

    def keys(self) -> list[K]:
        with self._lock:
            return list(self._data.keys())

    def put(self, key: K, value: V) -> None:
        with self._lock:
            v = self._data.get(key)
            self._data[key] = _Version(txid=_new_id(), value=value, previous=v)

    def delete(self, key: K) -> None:
        with self._lock:
            if key not in self._data:
                raise KeyError(f"key {key!r} not found")
            del self._data[key]

    # -- transaction factory ---------------------------------------------------

    def begin(self) -> Transaction[K, V]:
        return Transaction(self)


@dataclass
class Transaction(Generic[K, V]):
    """A snapshot-isolation transaction on an MVCCStore.

    Reads see the committed state at the snapshot version.  Writes are
    buffered in a local write-set and become visible atomically on commit.
    Commit aborts with TransactionConflictError if any key in the write-set
    was modified by another transaction after the snapshot was taken.
    """

    store: MVCCStore[K, V]
    _snapshot_txid: int = field(default_factory=_new_id)
    _write_set: dict[K, V] = field(default_factory=dict)
    _deleted: set[K] = field(default_factory=set)
    _committed: bool = False
    _rolled_back: bool = False

    @property
    def snapshot_txid(self) -> int:
        return self._snapshot_txid

    @property
    def write_set(self) -> dict[K, V]:
        return dict(self._write_set)

    @property
    def is_committed(self) -> bool:
        return self._committed

    @property
    def is_rolled_back(self) -> bool:
        return self._rolled_back

    # -- reads (snapshot-consistent) -------------------------------------------

    def read(self, key: K) -> V:
        self._check_active()
        if key in self._deleted:
            raise KeyError(f"key {key!r} deleted in this transaction")
        if key in self._write_set:
            return self._write_set[key]
        return self.store._read_committed(key, self._snapshot_txid)

    def read_optional(self, key: K) -> V | None:
        self._check_active()
        if key in self._deleted:
            return None
        if key in self._write_set:
            return self._write_set[key]
        return self.store._read_committed_optional(key, self._snapshot_txid)

    def contains(self, key: K) -> bool:
        self._check_active()
        if key in self._deleted:
            return False
        if key in self._write_set:
            return True
        try:
            self.store._read_committed(key, self._snapshot_txid)
            return True
        except KeyError:
            return False

    # -- writes (buffered locally) ---------------------------------------------

    def write(self, key: K, value: V) -> None:
        self._check_active()
        self._write_set[key] = value
        self._deleted.discard(key)

    def delete(self, key: K) -> None:
        self._check_active()
        if not self.contains(key):
            raise KeyError(f"key {key!r} not found in snapshot")
        self._write_set.pop(key, None)
        self._deleted.add(key)

    # -- lifecycle -------------------------------------------------------------

    def _check_active(self) -> None:
        if self._committed:
            raise RuntimeError("Transaction already committed")
        if self._rolled_back:
            raise RuntimeError("Transaction already rolled back")

    def commit(self) -> None:
        self._check_active()
        with self.store._lock:
            for key in self._write_set:
                current = self.store._data.get(key)
                if current is not None and current.txid > self._snapshot_txid:
                    raise TransactionConflictError(
                        f"conflict on key {key!r}: committed txid {current.txid} > snapshot {self._snapshot_txid}"
                    )
            for key in self._deleted:
                current = self.store._data.get(key)
                if current is not None and current.txid > self._snapshot_txid:
                    raise TransactionConflictError(
                        f"delete-conflict on key {key!r}: "
                        f"committed txid {current.txid} > snapshot {self._snapshot_txid}"
                    )
            commit_txid = _new_id()
            for key, value in self._write_set.items():
                prior = self.store._data.get(key)
                self.store._data[key] = _Version(txid=commit_txid, value=value, previous=prior)
            for key in self._deleted:
                self.store._data.pop(key, None)
        self._committed = True

    def rollback(self) -> None:
        self._check_active()
        self._write_set.clear()
        self._deleted.clear()
        self._rolled_back = True


class TransactionConflictError(Exception):
    """Raised when commit detects a write-write conflict."""
