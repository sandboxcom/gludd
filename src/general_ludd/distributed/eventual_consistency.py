"""Provide eventual-consistency primitives.

Read-repair pushes the newest version when replicas disagree. Hinted handoff
stores writes for unreachable nodes, while Merkle trees support anti-entropy
sync. On read, the coordinator pushes the newest
version to stale replicas.  Hinted handoff: if a write target is unreachable,
a healthy node accepts the write on its behalf and delivers it when the target
re-joins.  Merkle tree sync: two nodes exchange tree hashes level-by-level to
identify divergent keys, then exchange values for only those keys.

Vector clocks track causality; the module depends on
``general_ludd.distributed.vector_clock.VectorClock``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from general_ludd.distributed._merkle_tree import MerkleNode as MerkleNode
from general_ludd.distributed._merkle_tree import MerkleTree as MerkleTree
from general_ludd.distributed._merkle_tree import merkle_sync as merkle_sync
from general_ludd.distributed.vector_clock import VectorClock


@dataclass(slots=True)
class VersionedValue:
    """Pair a stored value with its causal version."""

    value: Any
    version: VectorClock


class DataStore:
    """In-memory key-value store with per-key vector clocks.

    Each key maps to a ``VersionedValue``.  ``vector_clock`` of the data
    within the entire store.  ``local_version`` provides a monotonic
    update-in-place counter when vector clocks are not available.
    """

    def __init__(self, node_id: str) -> None:
        """Initialize an empty store owned by ``node_id``."""
        self.node_id = node_id
        self._data: dict[str, VersionedValue] = {}
        self._local_version: dict[str, int] = {}

    def put(self, key: str, value: Any, version: VectorClock | None = None) -> VectorClock:
        """Store a value and return its assigned vector clock."""
        if version is None:
            lv = self._local_version.get(key, 0) + 1
            self._local_version[key] = lv
            version = VectorClock({self.node_id: lv})
        self._data[key] = VersionedValue(value=value, version=version)
        return version

    def get(self, key: str) -> VersionedValue | None:
        """Return a stored value and version, if present."""
        return self._data.get(key)

    def get_version(self, key: str) -> VectorClock | None:
        """Return the vector clock for a key, if present."""
        entry = self._data.get(key)
        return entry.version if entry else None

    def list_keys(self) -> list[str]:
        """Return stored keys in stable order."""
        return sorted(self._data.keys())

    def state(self) -> dict[str, VersionedValue]:
        """Return a shallow snapshot of the store."""
        return dict(self._data)

    def update(self, key: str, value: Any, version: VectorClock) -> bool:
        """Apply a causally newer or concurrent value when appropriate."""
        existing = self._data.get(key)
        if existing is None:
            self._data[key] = VersionedValue(value=value, version=version)
            return True
        existing_vc = existing.version
        is_newer = existing_vc < version
        is_concurrent = not is_newer and not version <= existing_vc
        if is_newer:
            self._data[key] = VersionedValue(value=value, version=version)
            return True
        if is_concurrent:
            merged_vc = existing_vc.merge(version)
            self._data[key] = VersionedValue(value=value, version=merged_vc)
            return True
        return False


# ── Read-Repair ──────────────────────────────────────────────────────────────


@dataclass
class ReadRepairResult:
    """Describe the value chosen by a read-repair operation."""

    value: Any
    version: VectorClock
    repairs: list[str] = field(default_factory=list)
    quorum_met: bool = True


def read_repair(
    key: str,
    replicas: dict[str, DataStore],
    quorum: int | None = None,
    coordinator_choice: str | None = None,
) -> ReadRepairResult:
    """Read a key and push the newest version to stale replicas.

    *quorum* defaults to ``⌊n/2⌋ + 1``.  *coordinator_choice* is the
    id of the preferred coordinator reply when multiple timestamps are equal.
    """
    n = len(replicas)
    if quorum is None:
        quorum = n // 2 + 1

    responses: list[tuple[str, VersionedValue]] = []
    for rid, store in replicas.items():
        vv = store.get(key)
        if vv is not None:
            responses.append((rid, vv))

    if len(responses) < quorum:
        {rid: vv for rid, vv in responses}
        if key in {k for rid, vv in responses for k in vv.version}:
            return ReadRepairResult(
                value=None,
                version=VectorClock(),
                repairs=[],
                quorum_met=False,
            )
        return ReadRepairResult(value=None, version=VectorClock(), repairs=[], quorum_met=False)

    best_rid, best = max(
        responses,
        key=lambda item: (list(item[1].version._counters.values()), item[0]),
    )

    repairs: list[str] = []
    for rid, vv in responses:
        if rid == best_rid:
            continue
        if vv.version < best.version:
            replicas[rid].update(key, best.value, best.version)
            repairs.append(rid)

    missing = [rid for rid in replicas if key not in replicas[rid]._data]
    for rid in missing:
        replicas[rid].put(key, best.value, best.version)
        repairs.append(rid)

    return ReadRepairResult(value=best.value, version=best.version, repairs=repairs, quorum_met=True)


# ── Hinted Handoff ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class Hint:
    """Represent a deferred write for an unavailable replica."""

    target: str
    key: str
    value: Any
    version: VectorClock
    timestamp: float = field(default_factory=lambda: __import__("time").time())


class HintedHandoff:
    """Accept writes on behalf of unreachable nodes, deliver them later.

    Unreachable nodes are recorded via ``mark_unreachable()``.  Writes to
    those nodes land in the local hint buffer.  ``deliver_hints()`` replays
    buffered writes once the target is reachable again.
    """

    def __init__(self, node_id: str) -> None:
        """Initialize an empty handoff buffer owned by ``node_id``."""
        self.node_id = node_id
        self._hints: dict[str, list[Hint]] = defaultdict(list)
        self._unreachable: set[str] = set()

    def mark_unreachable(self, node_id: str) -> None:
        """Mark a replica unavailable for direct writes."""
        self._unreachable.add(node_id)

    def mark_reachable(self, node_id: str) -> None:
        """Mark a replica available for direct writes."""
        self._unreachable.discard(node_id)

    @property
    def unreachable(self) -> frozenset[str]:
        """Return the currently unavailable replica identifiers."""
        return frozenset(self._unreachable)

    def record_if_unreachable(
        self,
        target: str,
        key: str,
        value: Any,
        version: VectorClock,
    ) -> bool:
        """Buffer a write if its target is currently unavailable."""
        if target not in self._unreachable:
            return False
        self._hints[target].append(Hint(target=target, key=key, value=value, version=version))
        return True

    def pending_hints(self, target: str) -> list[Hint]:
        """Return buffered writes for a target."""
        return list(self._hints.get(target, []))

    def hint_count(self, target: str) -> int:
        """Return the number of buffered writes for a target."""
        return len(self._hints.get(target, []))

    def total_hints(self) -> int:
        """Return the total number of buffered writes."""
        return sum(len(h) for h in self._hints.values())

    def deliver_hints(self, target: str, target_store: DataStore) -> int:
        """Replay all buffered writes for one target."""
        hints = self._hints.pop(target, [])
        delivered = 0
        for hint in hints:
            target_store.update(hint.key, hint.value, hint.version)
            delivered += 1
        return delivered

    def deliver_all(self, stores: dict[str, DataStore]) -> int:
        """Replay buffered writes to every available target store."""
        total = 0
        for target in list(self._hints.keys()):
            if target in stores:
                total += self.deliver_hints(target, stores[target])
        return total

    def expire_hints(self, max_age_seconds: float) -> int:
        """Discard hints older than a maximum age."""
        import time

        now = time.time()
        removed = 0
        for target in list(self._hints.keys()):
            before = len(self._hints[target])
            self._hints[target] = [h for h in self._hints[target] if now - h.timestamp <= max_age_seconds]
            removed += before - len(self._hints[target])
            if not self._hints[target]:
                del self._hints[target]
        return removed
