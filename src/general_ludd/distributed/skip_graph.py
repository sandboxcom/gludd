"""Skip graph: distributed overlay with O(log N) routing.

A skip graph generalizes skip lists for peer-to-peer networks. Each node
maintains neighbor tables at multiple levels, enabling greedy forwarding
that completes in O(log N) hops in expectation.

Routing:
    At each hop, the node forwards to the highest-level neighbor whose ID
    is closest to (but does not overshoot) the target key in the circular
    ID space.
"""

from __future__ import annotations

import bisect
import random
from dataclasses import dataclass
from typing import Any


@dataclass
class LeftRight:
    left: str | None
    right: str | None


class SkipGraphNode:
    """A single node in a skip graph with multi-level neighbor tables.

    Each node draws a random routing level (0..max_level-1) and populates
    neighbors at each level ≤ its routing level.
    """

    def __init__(self, node_id: str, max_level: int) -> None:
        self.node_id = node_id
        self.max_level = max_level
        self._routing_level: int = 0
        self.neighbors: dict[int, LeftRight] = {}

    def left_neighbor(self, level: int) -> str | None:
        entry = self.neighbors.get(level)
        return entry.left if entry else None

    def right_neighbor(self, level: int) -> str | None:
        entry = self.neighbors.get(level)
        return entry.right if entry else None

    def routing_level(self) -> int:
        return max(self.neighbors) if self.neighbors else 0

    def __repr__(self) -> str:
        return f"SkipGraphNode(id={self.node_id!r}, routing_level={self.routing_level()}, levels={len(self.neighbors)})"


class SkipGraphOverlay:
    """A skip-graph overlay network with key-value storage.

    Nodes are ordered by their string IDs. The overlay supports insert,
    remove, route (greedy forwarding), put, and lookup operations.

    Storage is decentralized: ``put`` stores a value on the first node whose
    ID is >= the hashed key (wrapping around).
    """

    def __init__(self, max_level: int = 4) -> None:
        if max_level < 1:
            raise ValueError("max_level must be >= 1")
        self.max_level = max_level
        self.nodes: dict[str, SkipGraphNode] = {}
        self._store: dict[str, tuple[Any, str]] = {}
        self._order: list[str] = []

    def __len__(self) -> int:
        return len(self.nodes)

    # ------------------------------------------------------------------ mutate

    def insert(self, node_id: str) -> SkipGraphNode:
        if node_id in self.nodes:
            return self.nodes[node_id]

        rlevel = self._draw_routing_level()
        node = SkipGraphNode(node_id, self.max_level)
        node._routing_level = rlevel

        for level in range(rlevel + 1):
            node.neighbors[level] = LeftRight(left=None, right=None)

        if self._order:
            idx = bisect.bisect_left(self._order, node_id)
            self._order.insert(idx, node_id)
            self.nodes[node_id] = node
            self._reconnect_all()
        else:
            self._order.append(node_id)
            self.nodes[node_id] = node

        return node

    def remove(self, node_id: str) -> bool:
        if node_id not in self.nodes:
            return False

        idx = bisect.bisect_left(self._order, node_id)
        if idx < len(self._order) and self._order[idx] == node_id:
            self._order.pop(idx)

        del self.nodes[node_id]

        keys_to_remove = [k for k, (_, owner) in self._store.items() if owner == node_id]
        for k in keys_to_remove:
            del self._store[k]

        if self.nodes:
            self._reconnect_all()

        return True

    # ------------------------------------------------------------------ data

    def put(self, key: str, value: Any, owner: str | None = None) -> None:
        if owner is None:
            owner = self._owner_for_key(key)
        self._store[key] = (value, owner)

    def lookup(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        return entry[0]

    # ------------------------------------------------------------------ routing

    def route(self, from_id: str, to_id: str) -> list[str]:
        """Greedy-forward from *from_id* to *to_id*.  Returns the hop list."""
        if from_id not in self.nodes or to_id not in self.nodes:
            return []

        if from_id == to_id:
            return [from_id]

        path: list[str] = [from_id]
        current = from_id
        visited: set[str] = {from_id}

        while current != to_id:
            next_hop = self._forward(current, to_id)
            if next_hop is None or next_hop in visited:
                break
            visited.add(next_hop)
            path.append(next_hop)
            current = next_hop

        if path[-1] != to_id and self._are_adjacent(current, to_id):
            path.append(to_id)

        return path

    # ------------------------------------------------------------------ internals

    def _draw_routing_level(self) -> int:
        """Geometric distribution capped at max_level-1."""
        level = 0
        rng = random.Random()
        while level < self.max_level - 1 and rng.random() < 0.5:
            level += 1
        return level

    def _node_at(self, idx: int) -> str:
        return self._order[idx % len(self._order)]

    def _owner_for_key(self, key: str) -> str:
        if not self._order:
            raise RuntimeError("no nodes in overlay")
        h = hash(key)
        idx = h % len(self._order)
        return self._order[idx]

    def _reconnect_all(self) -> None:
        n = len(self._order)
        if n == 0:
            return
        for node in self.nodes.values():
            node.neighbors.clear()
        for i, nid in enumerate(self._order):
            node = self.nodes[nid]
            rl = node._routing_level
            for level in range(rl + 1):
                step = 1 << level
                left_idx = (i - step) % n
                right_idx = (i + step) % n
                left_nid = (None if n == 1 else self._order[(i - 1) % n]) if i == left_idx else self._order[left_idx]
                right_nid = (None if n == 1 else self._order[(i + 1) % n]) if i == right_idx else self._order[right_idx]
                node.neighbors[level] = LeftRight(left=left_nid, right=right_nid)

    def _forward(self, current: str, target: str) -> str | None:
        node = self.nodes[current]
        best: str | None = None
        best_dist: float = float("inf")

        target_idx = bisect.bisect_left(self._order, target)
        current_idx = bisect.bisect_left(self._order, current)

        n = len(self._order)
        forward_dist = (target_idx - current_idx) % n
        backward_dist = (current_idx - target_idx) % n

        go_forward = forward_dist <= backward_dist

        for level in sorted(node.neighbors, reverse=True):
            entry = node.neighbors[level]
            for candidate in (entry.right, entry.left):
                if candidate is None or candidate == current:
                    continue
                c_idx = bisect.bisect_left(self._order, candidate)
                dist = (target_idx - c_idx) % n if go_forward else (c_idx - target_idx) % n
                if dist < best_dist:
                    best_dist = dist
                    best = candidate

        return best

    def _are_adjacent(self, a: str, b: str) -> bool:
        idx_a = bisect.bisect_left(self._order, a)
        idx_b = bisect.bisect_left(self._order, b)
        n = len(self._order)
        return (idx_a + 1) % n == idx_b or (idx_b + 1) % n == idx_a
