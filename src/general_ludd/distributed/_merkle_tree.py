"""Merkle-tree comparison and anti-entropy synchronization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from general_ludd.distributed.vector_clock import VectorClock


class DataStoreLike(Protocol):
    """Provide the store operations required by anti-entropy sync."""

    def list_keys(self) -> list[str]: ...

    def get(self, key: str) -> Any: ...

    def put(self, key: str, value: Any, version: VectorClock | None = None) -> VectorClock: ...


def _hash_data(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(slots=True)
class MerkleNode:
    """Store one Merkle digest and its covered key range."""

    hash: str
    left: MerkleNode | None = None
    right: MerkleNode | None = None
    key_range: tuple[str, str] | None = None


class MerkleTree:
    """Build a binary Merkle tree over key/value/vector-clock tuples.

    Leaves hash ``(key, value, version)`` and internal nodes hash their child
    digests. Comparing roots and then mismatched subtrees identifies only the
    keys that need anti-entropy synchronization.
    """

    def __init__(self, data: list[tuple[str, Any, VectorClock]]) -> None:
        if not data:
            self._leaves: list[tuple[str, str]] = []
            self.root: MerkleNode | None = None
            return
        self._leaves = [
            (_hash_data(f"{key}:{value!r}:{clock._counters!r}".encode()), key)
            for key, value, clock in data
        ]
        self.root = self._build_tree(self._leaves, 0, len(self._leaves) - 1)

    def _build_tree(
        self,
        leaves: list[tuple[str, str]],
        left: int,
        right: int,
    ) -> MerkleNode | None:
        if left > right:
            return None
        if left == right:
            digest, key = leaves[left]
            return MerkleNode(hash=digest, key_range=(key, key))
        middle = (left + right) // 2
        left_child = self._build_tree(leaves, left, middle)
        right_child = self._build_tree(leaves, middle + 1, right)
        left_hash = left_child.hash if left_child else ""
        right_hash = right_child.hash if right_child else ""
        return MerkleNode(
            hash=_hash_data(f"{left_hash}{right_hash}".encode()),
            left=left_child,
            right=right_child,
            key_range=(leaves[left][1], leaves[right][1]),
        )

    def root_hash(self) -> str:
        return self.root.hash if self.root else ""

    def compare(self, other: MerkleTree) -> set[str]:
        if self.root is None and other.root is None:
            return set()
        if self.root is None:
            return {key for _, key in other._leaves}
        if other.root is None:
            return {key for _, key in self._leaves}
        mismatches: set[str] = set()
        self._diff_nodes(self.root, other.root, mismatches)
        return mismatches

    def _diff_nodes(
        self,
        left: MerkleNode | None,
        right: MerkleNode | None,
        result: set[str],
    ) -> None:
        if left is None and right is None:
            return
        if left is None and right is not None:
            self._collect_keys(right, result)
            return
        if right is None and left is not None:
            self._collect_keys(left, result)
            return
        if left is None or right is None or left.hash == right.hash:
            return
        left_leaf = left.left is None and left.right is None
        right_leaf = right.left is None and right.right is None
        if left_leaf or right_leaf:
            if left_leaf and left.key_range:
                result.add(left.key_range[0])
            if right_leaf and right.key_range:
                result.add(right.key_range[0])
            return
        self._diff_nodes(left.left, right.left, result)
        self._diff_nodes(left.right, right.right, result)

    def _collect_keys(self, node: MerkleNode, result: set[str]) -> None:
        if node.left is None and node.right is None:
            if node.key_range:
                result.add(node.key_range[0])
            return
        if node.left:
            self._collect_keys(node.left, result)
        if node.right:
            self._collect_keys(node.right, result)


def _build_for(store: DataStoreLike, keys: list[str]) -> MerkleTree:
    items: list[tuple[str, Any, VectorClock]] = []
    for key in keys:
        value = store.get(key)
        if value is not None:
            items.append((key, value.value, value.version))
        else:
            items.append((key, None, VectorClock()))
    return MerkleTree(items)


def merkle_sync(
    store_a: DataStoreLike,
    store_b: DataStoreLike,
) -> dict[str, tuple[str, str]]:
    """Exchange only values whose Merkle leaves differ.

    Return ``{key: (action_a, action_b)}``, where each action is ``pull`` or
    ``equal`` from that store's perspective.
    """
    keys = sorted(set(store_a.list_keys()) | set(store_b.list_keys()))
    divergent = _build_for(store_a, keys).compare(_build_for(store_b, keys))
    actions: dict[str, tuple[str, str]] = {}
    for key in divergent:
        value_a = store_a.get(key)
        value_b = store_b.get(key)
        if value_a is None and value_b is not None:
            store_a.put(key, value_b.value, value_b.version)
            actions[key] = ("pull", "equal")
        elif value_b is None and value_a is not None:
            store_b.put(key, value_a.value, value_a.version)
            actions[key] = ("equal", "pull")
        elif value_a is not None and value_b is not None:
            if value_a.version < value_b.version:
                store_a.put(key, value_b.value, value_b.version)
                actions[key] = ("pull", "equal")
            elif value_b.version < value_a.version:
                store_b.put(key, value_a.value, value_a.version)
                actions[key] = ("equal", "pull")
            else:
                merged = value_a.version.merge(value_b.version)
                store_a.put(key, value_a.value, merged)
                store_b.put(key, value_b.value, merged)
                actions[key] = ("equal", "equal")
    return actions


__all__ = ("MerkleNode", "MerkleTree", "merkle_sync")
