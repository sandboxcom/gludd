"""Cryptographic accumulators: Merkle tree and RSA universal accumulator.

Merkle tree — binary hash tree with inclusion/exclusion proof generation
and verification.  RSA universal accumulator — set-membership accumulator
with public-key setup, witnesses, and non-membership proofs.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable

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
        self._hash_leaf = hash_leaf or _leaf_hash
        self._hash_pair = hash_pair or _pair_hash
        self._leaves = list(leaves)
        self._leaf_hashes = [self._hash_leaf(leaf) for leaf in self._leaves]
        self._layers = self._build_layers(self._leaf_hashes)

    # -- properties -------------------------------------------------------

    @property
    def root(self) -> bytes:
        return self._layers[-1][0] if self._layers else b""

    @property
    def leaves(self) -> list[bytes]:
        return list(self._leaves)

    @property
    def leaf_hashes(self) -> list[bytes]:
        return list(self._leaf_hashes)

    @property
    def leaf_count(self) -> int:
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
        """Return the insertion index and adjacent hashes that prove *target*
        is NOT in the leaf-set.

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
        sorted_leaf_hashes = [self._leaf_hashes[i] for i, _ in hashes_sorted]
        lo, hi = 0, len(sorted_leaf_hashes)
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_leaf_hashes[mid] == target_h:
                return None
            if sorted_leaf_hashes[mid] < target_h:
                lo = mid + 1
            else:
                hi = mid
        index = lo
        left = sorted_leaf_hashes[index - 1] if index > 0 else b""
        right = sorted_leaf_hashes[index] if index < len(sorted_leaf_hashes) else b""
        return (index, left, right, target_h)

    # -- batch exclusion check -------------------------------------------

    def exclude_batch(self, targets: list[bytes]) -> list[bool]:
        """Return a boolean per target: True if DEFINITELY absent, False if
        possibly present (inclusion not verified, but may be here)."""
        present = set(self._leaf_hashes)
        return [self._hash_leaf(t) not in present for t in targets]

    # -- representation --------------------------------------------------

    def __len__(self) -> int:
        return len(self._leaves)

    def __repr__(self) -> str:
        return f"MerkleTree(leaves={len(self._leaves)}, root={self.root.hex()[:12]}...)"


# ---------------------------------------------------------------------------
# RSA universal accumulator
# ---------------------------------------------------------------------------


class AccumulatorError(ValueError):
    """Raised when accumulator verification fails."""


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    """Miller-Rabin primality test."""
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0:
        return False
    d = n - 1
    s = 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int) -> int:
    """Generate a random *bits*-length prime."""
    while True:
        n = secrets.randbits(bits)
        n |= (1 << (bits - 1)) | 1
        if _is_probable_prime(n):
            return n


class RSAConfig:
    """RSA modulus N and generator G for the accumulator."""

    def __init__(self, bits: int = 1024) -> None:
        if bits < 64:
            raise AccumulatorError("bits must be >= 64")
        self.bits = bits
        half = bits // 2
        self._p = _random_prime(half)
        self._q = _random_prime(bits - half)
        self.N = self._p * self._q
        self.G = secrets.randbelow(self.N - 2) + 2

    @property
    def p(self) -> int:
        return self._p

    @property
    def q(self) -> int:
        return self._q


def _hash_to_prime(value: bytes, bits: int = 256) -> int:
    """Hash *value* with SHA-256, convert to odd integer, and step to next
    probable prime."""
    h = hashlib.sha256(value).digest()
    n = int.from_bytes(h, "big") % (1 << bits)
    n |= 1
    step = 0
    while not _is_probable_prime(n, rounds=25):
        n += 2
        step += 1
        if step > 10000:
            raise AccumulatorError("failed to find prime for " + repr(value))
    return n


class RSAUniversalAccumulator:
    """RSA-based universal set-membership accumulator.

    Stores accumulated prime representatives of set elements.
    Supports add, remove, membership witnesses, and non-membership proofs.
    """

    def __init__(
        self,
        config: RSAConfig,
        initial_elements: list[bytes] | None = None,
    ) -> None:
        self.config = config
        self._elements: set[int] = set()
        for elem in initial_elements or []:
            self._elements.add(_hash_to_prime(elem))
        self.value = self._compute_value()

    # -- core state -------------------------------------------------------

    def _compute_value(self) -> int:
        if not self._elements:
            return self.config.G
        product = 1
        for p in self._elements:
            product = (product * p) % ((self.config.p - 1) * (self.config.q - 1))
        return pow(self.config.G, product, self.config.N)

    def element_count(self) -> int:
        return len(self._elements)

    @property
    def elements(self) -> set[int]:
        return set(self._elements)

    # -- add / remove -----------------------------------------------------

    def add(self, element: bytes) -> None:
        p = _hash_to_prime(element)
        if p in self._elements:
            return
        self._elements.add(p)
        self.value = pow(self.value, p, self.config.N)

    def remove(self, element: bytes) -> None:
        p = _hash_to_prime(element)
        if p not in self._elements:
            raise AccumulatorError("element not in accumulator")
        if len(self._elements) == 1:
            self._elements.discard(p)
            self.value = self._compute_value()
            return
        self._elements.discard(p)
        phi = (self.config.p - 1) * (self.config.q - 1)
        d_inv = pow(p, -1, phi)
        self.value = pow(self.value, d_inv, self.config.N)

    # -- membership witness ------------------------------------------------

    def witness(self, element: bytes) -> int:
        """Return {G}^{∏_{q∈S\\{p}} q} mod N."""
        p = _hash_to_prime(element)
        if p not in self._elements:
            raise AccumulatorError("element not in accumulator")
        if len(self._elements) == 1:
            return self.config.G
        phi = (self.config.p - 1) * (self.config.q - 1)
        d_inv = pow(p, -1, phi)
        return pow(self.value, d_inv, self.config.N)

    def verify_witness(self, element: bytes, witness: int) -> bool:
        p = _hash_to_prime(element)
        return pow(witness, p, self.config.N) == self.value

    # -- non-membership proof ---------------------------------------------

    def non_membership_proof(self, element: bytes) -> tuple[int, int] | None:
        """Return (G^a, b) s.t. (G^a)^p * A^b == G mod N, proving
        element (with prime repr p) is NOT in the accumulator."""
        p = _hash_to_prime(element)
        if p in self._elements:
            return None
        product = 1
        for q in self._elements:
            product = (product * q) % ((self.config.p - 1) * (self.config.q - 1))
        a, b = _extended_gcd(p, product)
        a %= product
        b %= p
        return (pow(self.config.G, a, self.config.N), b)

    def verify_non_membership(self, element: bytes, proof: tuple[int, int]) -> bool:
        g_a, b = proof
        p = _hash_to_prime(element)
        lhs = (pow(g_a, p, self.config.N) * pow(self.value, b, self.config.N)) % self.config.N
        return lhs == self.config.G


def _extended_gcd(a: int, b: int) -> tuple[int, int]:
    """Return (x, y) such that a*x + b*y == gcd(a, b)."""
    if b == 0:
        return (1, 0)
    x1, y1 = _extended_gcd(b, a % b)
    return (y1, x1 - (a // b) * y1)


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
