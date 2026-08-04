"""Eventual consistency primitives: read-repair, hinted handoff, Merkle tree
anti-entropy sync.

Read-repair: on read, if replicas disagree, the coordinator pushes the newest
version to stale replicas.  Hinted handoff: if a write target is unreachable,
a healthy node accepts the write on its behalf and delivers it when the target
re-joins.  Merkle tree sync: two nodes exchange tree hashes level-by-level to
identify divergent keys, then exchange values for only those keys.

Vector clocks track causality; the module depends on
``general_ludd.distributed.vector_clock.VectorClock``.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from general_ludd.distributed.vector_clock import VectorClock


@dataclass(slots=True)
class VersionedValue:
    value: Any
    version: VectorClock


class DataStore:
    """In-memory key-value store with per-key vector clocks.

    Each key maps to a ``VersionedValue``.  ``vector_clock`` of the data
    within the entire store.  ``local_version`` provides a monotonic
    update-in-place counter when vector clocks are not available.
    """

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self._data: dict[str, VersionedValue] = {}
        self._local_version: dict[str, int] = {}

    def put(self, key: str, value: Any, version: VectorClock | None = None) -> VectorClock:
        if version is None:
            lv = self._local_version.get(key, 0) + 1
            self._local_version[key] = lv
            version = VectorClock({self.node_id: lv})
        self._data[key] = VersionedValue(value=value, version=version)
        return version

    def get(self, key: str) -> VersionedValue | None:
        return self._data.get(key)

    def get_version(self, key: str) -> VectorClock | None:
        entry = self._data.get(key)
        return entry.version if entry else None

    def list_keys(self) -> list[str]:
        return sorted(self._data.keys())

    def state(self) -> dict[str, VersionedValue]:
        return dict(self._data)

    def update(self, key: str, value: Any, version: VectorClock) -> bool:
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
    """Read *key* from every available replica and push the newest version
    to any replica that is behind.

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
        self.node_id = node_id
        self._hints: dict[str, list[Hint]] = defaultdict(list)
        self._unreachable: set[str] = set()

    def mark_unreachable(self, node_id: str) -> None:
        self._unreachable.add(node_id)

    def mark_reachable(self, node_id: str) -> None:
        self._unreachable.discard(node_id)

    @property
    def unreachable(self) -> frozenset[str]:
        return frozenset(self._unreachable)

    def record_if_unreachable(
        self,
        target: str,
        key: str,
        value: Any,
        version: VectorClock,
    ) -> bool:
        if target not in self._unreachable:
            return False
        self._hints[target].append(Hint(target=target, key=key, value=value, version=version))
        return True

    def pending_hints(self, target: str) -> list[Hint]:
        return list(self._hints.get(target, []))

    def hint_count(self, target: str) -> int:
        return len(self._hints.get(target, []))

    def total_hints(self) -> int:
        return sum(len(h) for h in self._hints.values())

    def deliver_hints(self, target: str, target_store: DataStore) -> int:
        hints = self._hints.pop(target, [])
        delivered = 0
        for hint in hints:
            target_store.update(hint.key, hint.value, hint.version)
            delivered += 1
        return delivered

    def deliver_all(self, stores: dict[str, DataStore]) -> int:
        total = 0
        for target in list(self._hints.keys()):
            if target in stores:
                total += self.deliver_hints(target, stores[target])
        return total

    def expire_hints(self, max_age_seconds: float) -> int:
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


# ── Merkle Tree ──────────────────────────────────────────────────────────────


def _hash_data(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(slots=True)
class MerkleNode:
    hash: str
    left: MerkleNode | None = None
    right: MerkleNode | None = None
    key_range: tuple[str, str] | None = None


class MerkleTree:
    """Binary Merkle tree over key-value pairs for anti-entropy.

    Each leaf is the hash of ``(key, value, version)``; internal nodes are
    the hash of ``(left.hash + right.hash)``.  Two nodes compare root hashes
    and recurse into mismatched subtrees to find divergent keys.
    """

    def __init__(self, data: list[tuple[str, Any, VectorClock]]):
        if not data:
            self._leaves: list[tuple[str, str]] = []
            self.root: MerkleNode | None = None
            return
        self._leaves = [(_hash_data(f"{k}:{v!r}:{vc._counters!r}".encode()), k) for k, v, vc in data]
        self.root = self._build_tree(self._leaves, 0, len(self._leaves) - 1)

    def _build_tree(self, leaves: list[tuple[str, str]], left: int, right: int) -> MerkleNode | None:
        if left > right:
            return None
        if left == right:
            h, key = leaves[left]
            return MerkleNode(hash=h, key_range=(key, key))
        mid = (left + right) // 2
        left_child = self._build_tree(leaves, left, mid)
        right_child = self._build_tree(leaves, mid + 1, right)
        lh = left_child.hash if left_child else ""
        rh = right_child.hash if right_child else ""
        combined = _hash_data(f"{lh}{rh}".encode())
        lk = leaves[left][1]
        rk = leaves[right][1]
        return MerkleNode(hash=combined, left=left_child, right=right_child, key_range=(lk, rk))

    def root_hash(self) -> str:
        return self.root.hash if self.root else ""

    def compare(self, other: MerkleTree) -> set[str]:
        if self.root is None and other.root is None:
            return set()
        if self.root is None:
            return {k for _, k in other._leaves}
        if other.root is None:
            return {k for _, k in self._leaves}
        mismatches: set[str] = set()
        self._diff_nodes(self.root, other.root, mismatches)
        return mismatches

    def _diff_nodes(self, a: MerkleNode | None, b: MerkleNode | None, result: set[str]) -> None:
        if a is None and b is None:
            return
        if a is None and b is not None:
            self._collect_keys(b, result)
            return
        if b is None and a is not None:
            self._collect_keys(a, result)
            return
        if a is None or b is None:
            return
        if a.hash == b.hash:
            return
        a_leaf = a.left is None and a.right is None
        b_leaf = b.left is None and b.right is None
        if a_leaf or b_leaf:
            if a_leaf and a.key_range:
                result.add(a.key_range[0])
            if b_leaf and b.key_range:
                result.add(b.key_range[0])
            return
        self._diff_nodes(a.left, b.left, result)
        self._diff_nodes(a.right, b.right, result)

    def _collect_keys(self, node: MerkleNode, result: set[str]) -> None:
        if node.left is None and node.right is None:
            if node.key_range:
                result.add(node.key_range[0])
            return
        if node.left:
            self._collect_keys(node.left, result)
        if node.right:
            self._collect_keys(node.right, result)


def merkle_sync(
    store_a: DataStore,
    store_b: DataStore,
) -> dict[str, tuple[str, str]]:
    """Anti-entropy sync: build Merkle trees for both stores, find divergent
    keys, and exchange values.  Returns ``{key: (action_a, action_b)}`` where
    actions are ``"push"``, ``"pull"``, or ``"equal"``."""
    keys = sorted(set(store_a.list_keys()) | set(store_b.list_keys()))

    def _build_for(store: DataStore) -> MerkleTree:
        items: list[tuple[str, Any, VectorClock]] = []
        for k in keys:
            vv = store.get(k)
            if vv is not None:
                items.append((k, vv.value, vv.version))
            else:
                items.append((k, None, VectorClock()))
        return MerkleTree(items)

    tree_a = _build_for(store_a)
    tree_b = _build_for(store_b)
    divergent = tree_a.compare(tree_b)

    actions: dict[str, tuple[str, str]] = {}
    for key in divergent:
        va = store_a.get(key)
        vb = store_b.get(key)
        if va is None and vb is not None:
            store_a.put(key, vb.value, vb.version)
            actions[key] = ("pull", "equal")
        elif vb is None and va is not None:
            store_b.put(key, va.value, va.version)
            actions[key] = ("equal", "pull")
        elif va is not None and vb is not None:
            if va.version < vb.version:
                store_a.put(key, vb.value, vb.version)
                actions[key] = ("pull", "equal")
            elif vb.version < va.version:
                store_b.put(key, va.value, va.version)
                actions[key] = ("equal", "pull")
            else:
                merged = va.version.merge(vb.version)
                store_a.put(key, va.value, merged)
                store_b.put(key, vb.value, merged)
                actions[key] = ("equal", "equal")
    return actions
