"""MLS Ratchet Tree (RFC 9420 §5) — left-balanced binary tree for group key management.

The tree uses standard binary-heap indexing: root at 0, left child at 2x+1,
right child at 2x+2.  For N leaves the tree has 2N-1 nodes; leaves occupy
indices N-1 ... 2N-2.
"""

from __future__ import annotations


class MLSTreeError(ValueError):
    """Base exception for MLS tree operations."""


def _parent(x: int) -> int:
    if x == 0:
        raise MLSTreeError("root has no parent")
    return (x - 1) // 2


def _left(x: int) -> int:
    return 2 * x + 1


def _right(x: int) -> int:
    return 2 * x + 2


def _level(x: int) -> int:
    return (x + 1).bit_length() - 1


def _is_leaf(idx: int, tree_size: int) -> bool:
    """Return True if *idx* is a leaf in a tree of *tree_size* nodes."""
    if tree_size == 0:
        return False
    return idx >= (tree_size - 1) // 2


def _num_nodes(n_leaves: int) -> int:
    """Total nodes needed for a tree with *n_leaves* leaves."""
    if n_leaves == 0:
        return 0
    return 2 * n_leaves - 1


class LeafNode:
    """A leaf node: one group member's key material."""

    __slots__ = ("credential", "index", "public_key", "signature_key")

    def __init__(
        self,
        index: int,
        public_key: bytes,
        credential: bytes = b"",
        signature_key: bytes = b"",
    ) -> None:
        self.index = index
        self.public_key = public_key
        self.credential = credential
        self.signature_key = signature_key

    def __repr__(self) -> str:
        return f"LeafNode(index={self.index}, pk={self.public_key[:6].hex()}...)"


class ParentNode:
    """An internal node holding a derived HPKE public key, or blank."""

    __slots__ = ("public_key",)

    def __init__(self, public_key: bytes | None = None) -> None:
        self.public_key = public_key

    def __repr__(self) -> str:
        if self.public_key is None:
            return "ParentNode(blank)"
        return f"ParentNode(pk={self.public_key[:6].hex()}...)"


Node = LeafNode | ParentNode | None


class RatchetTree:
    """Left-balanced binary tree for MLS (RFC 9420 §5.2).

    Nodes stored in a flat list with binary-heap indexing.
    Index 0 is the root; leaves occupy the suffix of the array.
    """

    def __init__(self) -> None:
        self.nodes: list[Node] = []

    # ── sizing ────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self.nodes)

    @property
    def leaf_count(self) -> int:
        return _num_nodes_inv(len(self.nodes))

    @property
    def leaf_nodes(self) -> list[int]:
        """Flat (node) indices of all leaves."""
        n = self.leaf_count
        if n == 0:
            return []
        return list(range(n - 1, self.size))

    def leaf_at(self, leaf_index: int) -> LeafNode:
        """Return the :class:`LeafNode` at the given *leaf index* (0-based)."""
        n = self.leaf_count
        if leaf_index < 0 or leaf_index >= n:
            raise MLSTreeError(f"leaf index {leaf_index} out of range (have {n} leaves)")
        node_idx = self.leaf_nodes[leaf_index]
        node = self.nodes[node_idx]
        if not isinstance(node, LeafNode):
            raise MLSTreeError(f"expected LeafNode at index {node_idx}, got {type(node).__name__}")
        return node

    # ── tree math (static helpers) ─────────────────────────────────────────

    @staticmethod
    def sibling(x: int) -> int:
        p = _parent(x)
        lc = _left(p)
        rc = _right(p)
        return rc if x == lc else lc

    @staticmethod
    def direct_path(leaf_idx: int) -> list[int]:
        """Nodes on the path from *leaf_idx* to the root, excluding the leaf."""
        path: list[int] = []
        x = leaf_idx
        while x > 0:
            x = _parent(x)
            path.append(x)
        return path

    @staticmethod
    def copath(leaf_idx: int) -> list[int]:
        """Siblings of non-root nodes on the direct path of *leaf_idx*."""
        dp = RatchetTree.direct_path(leaf_idx)
        return [RatchetTree.sibling(n) for n in dp if n != 0]

    # ── resolution ────────────────────────────────────────────────────────

    def resolution(self, idx: int) -> list[int]:
        """Non-blank nodes covering the subtree rooted at *idx*."""
        if idx >= self.size:
            return []
        node = self.nodes[idx]
        if node is not None:
            return [idx]
        lc = _left(idx)
        rc = _right(idx)
        result: list[int] = []
        if lc < self.size:
            result.extend(self.resolution(lc))
        if rc < self.size:
            result.extend(self.resolution(rc))
        return result

    def filtered_direct_path(self, leaf_idx: int) -> list[int]:
        """Direct-path nodes whose resolution is non-empty."""
        dp = self.direct_path(leaf_idx)
        return [idx for idx in dp if self.resolution(idx)]

    # ── mutation helpers ───────────────────────────────────────────────────

    def _expand(self) -> None:
        """Grow the tree by one leaf, repositioning existing leaves."""
        old_leaves: list[LeafNode] = []
        for leaf_idx in range(self.leaf_count):
            ni = self.leaf_nodes[leaf_idx]
            node = self.nodes[ni]
            if isinstance(node, LeafNode):
                old_leaves.append(node)

        new_n = len(old_leaves) + 1
        new_size = _num_nodes(new_n)
        self.nodes = [None] * new_size
        first_leaf = new_n - 1
        for i, leaf_node in enumerate(old_leaves):
            leaf_node.index = i
            self.nodes[first_leaf + i] = leaf_node

    # ── public API ─────────────────────────────────────────────────────────

    def add_leaf(self, leaf: LeafNode) -> int:
        """Add a member at the rightmost leaf. Returns the leaf index (0-based)."""
        self._expand()
        new_count = self.leaf_count
        leaf_idx = new_count - 1
        leaf.index = leaf_idx
        self.nodes[self.leaf_nodes[leaf_idx]] = leaf
        return leaf_idx

    def remove_leaf(self, leaf_index: int) -> None:
        """Remove a member by blanking its leaf and direct path."""
        n = self.leaf_count
        if leaf_index < 0 or leaf_index >= n:
            raise MLSTreeError(f"leaf index {leaf_index} out of range")
        node_idx = self.leaf_nodes[leaf_index]
        self._blank_path(node_idx)

    def update_leaf(self, leaf_index: int, public_key: bytes) -> LeafNode:
        """Update a leaf's HPKE public key. Returns the updated LeafNode."""
        node = self.leaf_at(leaf_index)
        node.public_key = public_key
        return node

    def blank_node(self, idx: int) -> None:
        """Set a node to blank (used after removal / key rotation)."""
        if idx < 0 or idx >= self.size:
            raise MLSTreeError(f"node index {idx} out of range")
        self.nodes[idx] = None

    def set_parent_hash(self, idx: int, public_key: bytes) -> None:
        """Assign a derived parent public key (HPKE) at internal node *idx*."""
        if _is_leaf(idx, self.size):
            raise MLSTreeError(f"node {idx} is a leaf — cannot set parent hash there")
        self.nodes[idx] = ParentNode(public_key)

    def is_blank(self, idx: int) -> bool:
        return self.nodes[idx] is None

    # ── internals ──────────────────────────────────────────────────────────

    def _blank_path(self, leaf_idx: int) -> None:
        """Blank the leaf and all nodes on its direct path."""
        self.nodes[leaf_idx] = None
        for idx in self.direct_path(leaf_idx):
            if idx < self.size:
                self.nodes[idx] = None


def _num_nodes_inv(node_count: int) -> int:
    """Number of leaves in a tree with *node_count* nodes."""
    return (node_count + 1) // 2
