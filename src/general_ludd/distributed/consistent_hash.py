"""Weighted consistent hashing ring with virtual nodes.

Provides O(log N) node lookup for a configurable number of virtual
nodes per unit weight.  Supports add/remove with weight, key migration
analysis, and distribution statistics.
"""

from __future__ import annotations

import bisect
import hashlib
from collections import Counter


def _hash(key: str) -> int:
    return int(hashlib.md5(key.encode("utf-8"), usedforsecurity=False).hexdigest(), 16)


class ConsistentHashRing:
    """Weighted consistent hashing ring.

    Each node receives ``virtual_nodes * weight`` points on the ring.
    Higher-weight nodes own proportionally more key space.
    """

    def __init__(self, virtual_nodes: int = 64) -> None:
        """Initialize the ring with a virtual node count."""
        if virtual_nodes < 1:
            raise ValueError("virtual_nodes must be >= 1")
        self._vn = virtual_nodes
        self._ring: list[int] = []
        self._node_for_hash: dict[int, str] = {}
        self._nodes: dict[str, int] = {}

    # ------------------------------------------------------------------ query

    @property
    def virtual_nodes(self) -> int:
        """Return the virtual node count."""
        return self._vn

    @property
    def nodes(self) -> tuple[str, ...]:
        """Return the registered node ids."""
        return tuple(self._nodes)

    def weight_of(self, node_id: str) -> int:
        """Return the weight of a node."""
        return self._nodes[node_id]

    def __len__(self) -> int:
        """Return the number of nodes."""
        return len(self._nodes)

    def __contains__(self, node_id: str) -> bool:
        """Return whether the node is registered."""
        return node_id in self._nodes

    # ------------------------------------------------------------------ mutate

    def add_node(self, node_id: str, weight: int = 1) -> None:
        """Add a node with the given weight."""
        if weight < 1:
            raise ValueError(f"weight must be >= 1, got {weight}")
        if node_id in self._nodes:
            self._nodes[node_id] = weight
            return
        self._nodes[node_id] = weight
        self._insert_points(node_id, weight)

    def remove_node(self, node_id: str) -> None:
        """Remove a node and all its virtual points from the ring."""
        if node_id not in self._nodes:
            raise KeyError(f"unknown node: {node_id!r}")
        total_points = self._nodes.pop(node_id) * self._vn
        for i in range(total_points):
            vkey = f"{node_id}:vn{i}"
            h = _hash(vkey)
            self._remove_point(h)
            del self._node_for_hash[h]

    def set_weight(self, node_id: str, weight: int) -> None:
        """Set the weight of a node."""
        if weight < 1:
            raise ValueError(f"weight must be >= 1, got {weight}")
        if node_id not in self._nodes:
            raise KeyError(f"unknown node: {node_id!r}")
        old = self._nodes[node_id]
        if weight == old:
            return
        # remove old points, insert new ones
        for i in range(old * self._vn):
            h = _hash(f"{node_id}:vn{i}")
            self._remove_point(h)
            del self._node_for_hash[h]
        self._nodes[node_id] = weight
        self._insert_points(node_id, weight)

    # ------------------------------------------------------------------ lookup

    def get_node(self, key: str) -> str:
        """Return the node responsible for a key."""
        if not self._ring:
            raise RuntimeError("ring is empty")
        h = _hash(key)
        idx = bisect.bisect_right(self._ring, h)
        if idx == len(self._ring):
            idx = 0
        return self._node_for_hash[self._ring[idx]]

    def get_nodes(self, key: str, count: int) -> list[str]:
        """Return *count* distinct successor nodes for a key (preference list)."""
        if not self._ring:
            raise RuntimeError("ring is empty")
        if count < 1:
            raise ValueError("count must be >= 1")
        h = _hash(key)
        start = bisect.bisect_right(self._ring, h)
        seen: set[str] = set()
        result: list[str] = []
        for offset in range(len(self._ring)):
            idx = (start + offset) % len(self._ring)
            node = self._node_for_hash[self._ring[idx]]
            if node not in seen:
                seen.add(node)
                result.append(node)
                if len(result) == count:
                    break
        return result

    # ------------------------------------------------------------------ analysis

    def key_distribution(self, keys: list[str]) -> dict[str, int]:
        """Count how many of *keys* map to each node."""
        dist: Counter[str] = Counter()
        for k in keys:
            dist[self.get_node(k)] += 1
        return dict(dist)

    def migration_count(self, old_ring: ConsistentHashRing, keys: list[str]) -> int:
        """Number of keys that would change owner from *old_ring* to self."""
        return sum(1 for k in keys if old_ring.get_node(k) != self.get_node(k))

    def point_distribution(self) -> dict[str, int]:
        """Number of ring points owned by each node."""
        dist: Counter[str] = Counter()
        for h in self._ring:
            dist[self._node_for_hash[h]] += 1
        return dict(dist)

    def balance_ratio(self, keys: list[str]) -> float:
        """Stddev of key counts divided by mean (lower = more balanced)."""
        if not self._nodes:
            return 0.0
        dist = self.key_distribution(keys)
        if len(dist) < 2:
            return 0.0
        counts = list(dist.values())
        mean = sum(counts) / len(counts)
        variance = sum((c - mean) ** 2 for c in counts) / len(counts)
        stddev = variance**0.5
        return stddev / mean if mean > 0 else 0.0

    # ------------------------------------------------------------------ internals

    def _insert_points(self, node_id: str, weight: int) -> None:
        total = weight * self._vn
        for i in range(total):
            h = _hash(f"{node_id}:vn{i}")
            pos = bisect.bisect_left(self._ring, h)
            self._ring.insert(pos, h)
            self._node_for_hash[h] = node_id

    def _remove_point(self, h: int) -> None:
        idx = bisect.bisect_left(self._ring, h)
        if idx < len(self._ring) and self._ring[idx] == h:
            self._ring.pop(idx)
