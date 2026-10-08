"""Cryptographic accumulators: Merkle tree and RSA universal accumulator.

Merkle tree — binary hash tree with inclusion/exclusion proof generation
and verification.  RSA universal accumulator — set-membership accumulator
with public-key setup, witnesses, and non-membership proofs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from general_ludd.algorithms.rsa_accumulator import (
    AccumulatorError,
    RSAConfig,
    RSAUniversalAccumulator,
)

# ---------------------------------------------------------------------------
# Merkle tree
# ---------------------------------------------------------------------------


class MerkleProofError(ValueError):
    """Raised when a Merkle proof fails verification."""


def _default_hash(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


_LEAF_PREFIX = b"\x00"
_INNER_PREFIX = b"\x01"


def _leaf_hash(data: bytes) -> bytes:
    return _default_hash(_LEAF_PREFIX + data)


def _pair_hash(left: bytes, right: bytes) -> bytes:
    if left <= right:
        return _default_hash(_INNER_PREFIX + left + right)
    return _default_hash(_INNER_PREFIX + right + left)


class MerkleTree:
    """Binary Merkle tree over byte-string leaves.

    Builds the tree from left to right; the root is the top hash.
    Supports inclusion proofs (single and batch) and exclusion proofs
    via sibling adjacency.
    """

    def __init__(
        self,
        leaves: list[bytes],
        *,
        hash_leaf: Callable[[bytes], bytes] | None = None,
        hash_pair: Callable[[bytes, bytes], bytes] | None = None,
    ) -> None:
        """Build a tree from immutable byte-string leaves and hash functions."""
        self._hash_leaf = hash_leaf or _leaf_hash
        self._hash_pair = hash_pair or _pair_hash
        self._leaves = list(leaves)
        self._leaf_hashes = [self._hash_leaf(leaf) for leaf in self._leaves]
        self._layers = self._build_layers(self._leaf_hashes)

    # -- properties -------------------------------------------------------

    @property
    def root(self) -> bytes:
        """Return the current Merkle root, or empty bytes for an empty tree."""
        return self._layers[-1][0] if self._layers else b""

    @property
    def leaves(self) -> list[bytes]:
        """Return a defensive copy of the raw leaves."""
        return list(self._leaves)

    @property
    def leaf_hashes(self) -> list[bytes]:
        """Return a defensive copy of the hashed leaves."""
        return list(self._leaf_hashes)

    @property
    def leaf_count(self) -> int:
        """Return the number of leaves in the tree."""
        return len(self._leaves)

    # -- build ------------------------------------------------------------

    def _build_layers(self, hashes: list[bytes]) -> list[list[bytes]]:
        if not hashes:
            return []
        layers: list[list[bytes]] = [hashes]
        while len(layers[-1]) > 1:
            layer = layers[-1]
            next_layer: list[bytes] = []
            for i in range(0, len(layer), 2):
                left = layer[i]
                right = layer[i + 1] if i + 1 < len(layer) else left
                next_layer.append(self._hash_pair(left, right))
            layers.append(next_layer)
        return layers

    # -- single inclusion proof -------------------------------------------

    def inclusion_proof(self, index: int) -> list[tuple[bytes, bool]]:
        """Return sibling path from leaf at *index* up to root.

        Each entry is (hash, is_right) where *is_right*==True means the
        sibling is the right child in the pair.
        """
        if not 0 <= index < len(self._leaf_hashes):
            raise IndexError(f"leaf index {index} out of range [0, {len(self._leaf_hashes)})")
        proof: list[tuple[bytes, bool]] = []
        idx = index
        for layer in self._layers[:-1]:  # skip root layer
            if idx % 2 == 0:  # left child
                sibling = layer[idx + 1] if idx + 1 < len(layer) else layer[idx]
                proof.append((sibling, True))
            else:  # right child
                proof.append((layer[idx - 1], False))
            idx //= 2
        return proof

    @staticmethod
    def verify_inclusion(
        leaf_hash: bytes,
        index: int,
        proof: list[tuple[bytes, bool]],
        root: bytes,
        *,
        hash_pair: Callable[[bytes, bytes], bytes] | None = None,
    ) -> bool:
        """Verify an inclusion proof without constructing the full tree."""
        pair = hash_pair or _pair_hash
        current = leaf_hash
        idx = index
        for sibling, is_right in proof:
            expected_is_right = idx % 2 == 0
            if is_right != expected_is_right:
                return False
            current = pair(current, sibling) if is_right else pair(sibling, current)
            idx //= 2
        return current == root

    # -- batch inclusion proof --------------------------------------------

    def inclusion_proof_batch(self, indices: list[int]) -> list[tuple[bytes, bool]]:
        """Return a compact multi-leaf inclusion proof (audit path).

        Only includes siblings needed to recompute the root for ALL
        requested indices.
        """
        if not indices:
            return []
        tree_height = len(self._layers) - 1
        needed: set[int] = set()

        for idx in indices:
            pos = idx
            for level in range(tree_height):
                if pos % 2 == 0:
                    sibling = pos + 1
                    if sibling < len(self._layers[level]) and sibling not in indices:
                        needed.add((level + 1) << 20 | sibling)
                else:
                    sibling = pos - 1
                    if sibling not in indices:
                        needed.add((level + 1) << 20 | sibling)
                pos //= 2

        return [self._decode_proof_entry(e) for e in sorted(needed)]

    def _decode_proof_entry(self, packed: int) -> tuple[bytes, bool]:
        level = packed >> 20
        index = packed & 0xFFFFF
        index + 1 if index % 2 == 0 else index - 1
        is_right = index % 2 == 0
        return (self._layers[level - 1][index], is_right)

    # -- exclusion proof (by sorted-insertion adjacency) ------------------

    def exclusion_proof(self, target: bytes) -> tuple[int, bytes, bytes, bytes] | None:
        """Return the insertion index and hashes proving target exclusion.

        Returns (index, left_hash, right_hash) where:
        - *index* is the sorted-insertion point
        - *left_hash* / *right_hash* are the neighbouring leaf hashes
          (or b"" if there is no neighbour on that side)

        Returns None when the tree is empty.
        """
        if not self._leaf_hashes:
            return None
        target_h = self._hash_leaf(target)
        hashes_sorted = sorted(enumerate(self._leaves), key=lambda x: x[1])
        sorted_raw = [self._leaves[i] for i, _ in hashes_sorted]
        sorted_leaf_hashes = [self._leaf_hashes[i] for i, _ in hashes_sorted]
        lo, hi = 0, len(sorted_raw)
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_raw[mid] == target:
                return None
            if sorted_raw[mid] < target:
                lo = mid + 1
            else:
                hi = mid
        index = lo
        left = sorted_leaf_hashes[index - 1] if index > 0 else b""
        right = sorted_leaf_hashes[index] if index < len(sorted_leaf_hashes) else b""
        return (index, left, right, target_h)

    # -- batch exclusion check -------------------------------------------

    def exclude_batch(self, targets: list[bytes]) -> list[bool]:
        """Return whether each target is definitely absent.

        ``False`` means the target may be present but inclusion was not proved.
        """
        present = set(self._leaf_hashes)
        return [self._hash_leaf(t) not in present for t in targets]

    # -- representation --------------------------------------------------

    def __len__(self) -> int:
        """Return the number of leaves."""
        return len(self._leaves)

    def __repr__(self) -> str:
        """Return a compact tree summary with a truncated root digest."""
        return f"MerkleTree(leaves={len(self._leaves)}, root={self.root.hex()[:12]}...)"


# ---------------------------------------------------------------------------
# Exported primitives for direct import
# ---------------------------------------------------------------------------

__all__ = [
    "AccumulatorError",
    "MerkleProofError",
    "MerkleTree",
    "RSAConfig",
    "RSAUniversalAccumulator",
    "_leaf_hash",
    "_pair_hash",
]
