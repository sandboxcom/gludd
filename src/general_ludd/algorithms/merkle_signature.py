"""Merkle signature schemes: LMS/HSS with Winternitz OTS (RFC 8554).

Pure-Python, stdlib only. Uses SHA-256 as the underlying hash function.

Implements:
  - Winternitz one-time signatures
  - LMS single-tree signatures
  - HSS multi-tree signatures
  - Merkle tree generation and verification
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field


class MerkleSignatureError(Exception):
    pass


class MerkleKeyExhaustedError(MerkleSignatureError):
    pass


class MerkleVerificationError(MerkleSignatureError):
    pass


# ---------------------------------------------------------------------------
# hash helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _int_to_bytes_be(n: int, length: int) -> bytes:
    return n.to_bytes(length, "big")


# ---------------------------------------------------------------------------
# Winternitz OTS
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WinternitzConfig:
    w: int = 4
    ls: int = 16

    def __post_init__(self) -> None:
        if self.w not in (1, 2, 4, 8):
            raise ValueError(f"w must be 1, 2, 4, or 8, got {self.w}")


def _winternitz_params(w: int, ls: int) -> tuple[int, int, int, int]:
    u = 8 * ls // w
    v = 8 * ls // w
    max_val = (1 << w) - 1
    total = u + v
    return u, v, max_val, total


def _digest_to_values(digest: bytes, w: int, u: int) -> list[int]:
    values: list[int] = []
    max_val = (1 << w) - 1
    if w == 8:
        for i in range(min(u, len(digest))):
            values.append(digest[i])
        while len(values) < u:
            values.append(0)
    elif w == 4:
        for i in range(len(digest)):
            b = digest[i]
            values.append((b >> 4) & max_val)
            values.append(b & max_val)
        while len(values) < u:
            values.append(0)
        values = values[:u]
    elif w == 2:
        for i in range(len(digest)):
            b = digest[i]
            for shift in (6, 4, 2, 0):
                values.append((b >> shift) & max_val)
        while len(values) < u:
            values.append(0)
        values = values[:u]
    else:  # w == 1
        for i in range(len(digest)):
            b = digest[i]
            for shift in range(7, -1, -1):
                values.append((b >> shift) & max_val)
        while len(values) < u:
            values.append(0)
        values = values[:u]
    return values


def _values_to_bytes(values: list[int], w: int) -> bytes:
    result = bytearray()
    max_val = (1 << w) - 1
    if w == 8:
        for v in values:
            result.append(v & max_val)
    elif w == 4:
        for i in range(0, len(values), 2):
            hi = (values[i] & max_val) << 4
            lo = values[i + 1] & max_val if i + 1 < len(values) else 0
            result.append(hi | lo)
    elif w == 2:
        for i in range(0, len(values), 4):
            b = 0
            for j in range(4):
                idx = i + j
                v = values[idx] & max_val if idx < len(values) else 0
                b = (b << 2) | v
            result.append(b)
    else:  # w == 1
        for i in range(0, len(values), 8):
            b = 0
            for j in range(8):
                idx = i + j
                v = values[idx] & max_val if idx < len(values) else 0
                b = (b << 1) | v
            result.append(b)
    return bytes(result)


def _checksum(digest: bytes, w: int, ls: int) -> bytes:
    u, v, max_val, _total = _winternitz_params(w, ls)
    vals = _digest_to_values(digest, w, u)
    csum = 0
    for val in vals:
        csum += max_val - val
    csum_bytes = csum.to_bytes((csum.bit_length() + 7) // 8 or 1, "big")
    if len(csum_bytes) < v:
        csum_bytes = b"\x00" * (v - len(csum_bytes)) + csum_bytes
    return csum_bytes[:v]


def _chain(i: int, s: int, x: bytes, seed: bytes, w: int, start: int = 0) -> bytes:
    n = len(x)
    if s == 0:
        return x
    tmp = x
    for r in range(start + 1, start + s + 1):
        tmp = _sha256(_int_to_bytes_be(i, 4) + _int_to_bytes_be(r, 4) + seed + tmp)[:n]
    return tmp


def _winternitz_keygen(w: int, ls: int, seed: bytes) -> tuple[list[bytes], list[bytes]]:
    n = 32
    _u, _v, max_val, total = _winternitz_params(w, ls)
    sk = [_sha256(seed + _int_to_bytes_be(i, 4))[:n] for i in range(total)]
    pk = [_chain(i, max_val, s, seed, w) for i, s in enumerate(sk)]
    return sk, pk


def _winternitz_sign(message: bytes, sk: list[bytes], seed: bytes, w: int, ls: int) -> list[bytes]:
    if not sk:
        raise MerkleKeyExhaustedError("Winternitz private key exhausted")
    hashed = _sha256(message)
    n = len(hashed)
    u, v, max_val, total = _winternitz_params(w, ls)
    digest = hashed[: min(ls, n)]
    if len(digest) < ls:
        digest = digest + b"\x00" * (ls - len(digest))
    msg_vals = _digest_to_values(digest, w, u)
    csum = 0
    for val in msg_vals:
        csum += max_val - val
    csum_bytes = csum.to_bytes((csum.bit_length() + 7) // 8 or 1, "big")
    if len(csum_bytes) < v:
        csum_bytes = b"\x00" * (v - len(csum_bytes)) + csum_bytes
    csum_bytes = csum_bytes[:v]
    csum_vals = _digest_to_values(csum_bytes, w, v)
    all_vals = msg_vals + csum_vals
    sig: list[bytes] = []
    for i in range(total):
        sig.append(_chain(i, all_vals[i], sk[i], seed, w))
    return sig


def _winternitz_verify(message: bytes, sig: list[bytes], pk: list[bytes], seed: bytes, w: int, ls: int) -> bool:
    u, v, max_val, total = _winternitz_params(w, ls)
    if len(sig) != len(pk):
        return False
    hashed = _sha256(message)
    n = len(hashed)
    digest = hashed[: min(ls, n)]
    if len(digest) < ls:
        digest = digest + b"\x00" * (ls - len(digest))
    msg_vals = _digest_to_values(digest, w, u)
    csum = 0
    for val in msg_vals:
        csum += max_val - val
    csum_bytes = csum.to_bytes((csum.bit_length() + 7) // 8 or 1, "big")
    if len(csum_bytes) < v:
        csum_bytes = b"\x00" * (v - len(csum_bytes)) + csum_bytes
    csum_bytes = csum_bytes[:v]
    csum_vals = _digest_to_values(csum_bytes, w, v)
    all_vals = msg_vals + csum_vals
    for i in range(total):
        val = all_vals[i]
        if val == max_val:
            if sig[i] != pk[i]:
                return False
        else:
            derived = _chain(i, max_val - val, sig[i], seed, w, start=val)
            if derived != pk[i]:
                return False
    return True


# ---------------------------------------------------------------------------
# LMS — Leighton-Micali Signatures (single tree)
# ---------------------------------------------------------------------------

HASH_SIZE = 32


def _build_merkle_tree(seed: bytes, h: int, leaf_keys: list[bytes]) -> tuple[dict[int, bytes], bytes]:
    leaf_start = 1 << h
    nodes: dict[int, bytes] = {}
    for i, v in enumerate(leaf_keys):
        nodes[leaf_start + i] = v
    if h == 0:
        root = nodes.get(leaf_start, b"\x00" * HASH_SIZE)
        return nodes, root
    for level in range(h - 1, -1, -1):
        start = 1 << level
        for idx in range(start, start + (1 << level)):
            left = nodes.get(2 * idx, b"\x00" * HASH_SIZE)
            right = nodes.get(2 * idx + 1, b"\x00" * HASH_SIZE)
            nodes[idx] = _sha256(seed[:1] + _int_to_bytes_be(idx, 4) + left + right)
    root = nodes.get(1, b"\x00" * HASH_SIZE)
    return nodes, root


def _merkle_proof(nodes: dict[int, bytes], leaf_index: int, h: int) -> list[bytes]:
    proof: list[bytes] = []
    idx = leaf_index + (1 << h)
    for _ in range(h):
        sibling = idx ^ 1
        proof.append(nodes.get(sibling, b"\x00" * HASH_SIZE))
        idx //= 2
    return proof


def _verify_merkle_proof(root: bytes, leaf: bytes, leaf_index: int, proof: list[bytes], seed: bytes, h: int) -> bool:
    current = leaf
    idx = leaf_index + (1 << h)
    for sibling_hash in proof:
        combined = sibling_hash + current if idx & 1 else current + sibling_hash
        idx //= 2
        current = _sha256(seed[:1] + _int_to_bytes_be(idx, 4) + combined)
    return current == root


@dataclass
class LMSParams:
    h: int
    m: int

    def __post_init__(self) -> None:
        if self.h < 0 or self.h > 20:
            raise ValueError(f"h must be 0-20, got {self.h}")
        if self.m < 2 or self.m > 20:
            raise ValueError(f"m must be 2-20, got {self.m}")

    @property
    def leaf_count(self) -> int:
        return 1 << self.h


@dataclass
class LMSKeyPair:
    params: LMSParams
    seed: bytes
    root: bytes
    leaf_keys: list[bytes]
    leaf_secrets: list[list[bytes]]
    nodes: dict[int, bytes]
    used: int = 0
    _w_config: WinternitzConfig = field(default_factory=WinternitzConfig)

    @classmethod
    def generate(cls, h: int = 4, m: int = 16, w: int = 4) -> LMSKeyPair:
        params = LMSParams(h=h, m=m)
        seed = secrets.token_bytes(32)
        n_leaves = params.leaf_count
        w_config = WinternitzConfig(w=w, ls=32)
        leaf_secrets: list[list[bytes]] = []
        leaf_keys: list[bytes] = []
        for i in range(n_leaves):
            leaf_seed = _sha256(seed + _int_to_bytes_be(i, 4))
            sk, pk = _winternitz_keygen(w, 32, leaf_seed)
            leaf_secrets.append(sk)
            combined = b"".join(pk)
            leaf_key_hash = _sha256(combined)[:HASH_SIZE]
            leaf_keys.append(leaf_key_hash)
        nodes, root = _build_merkle_tree(seed, h, leaf_keys)
        return cls(
            params=params,
            seed=seed,
            root=root,
            leaf_keys=leaf_keys,
            leaf_secrets=leaf_secrets,
            nodes=nodes,
            _w_config=w_config,
        )

    def sign(self, message: bytes) -> LMSSignature:
        if self.used >= self.params.leaf_count:
            raise MerkleKeyExhaustedError(f"LMS key exhausted: {self.used}/{self.params.leaf_count}")
        idx = self.used
        w = self._w_config.w
        ls_val = 32
        leaf_seed = _sha256(self.seed + _int_to_bytes_be(idx, 4))
        sig_elements = _winternitz_sign(message, self.leaf_secrets[idx], leaf_seed, w, ls_val)
        self.used += 1
        proof = _merkle_proof(self.nodes, idx, self.params.h)
        return LMSSignature(q=idx, ots_signature=sig_elements, path=proof, params=self.params)

    def public_key_bytes(self) -> bytes:
        return self.root


@dataclass(frozen=True, slots=True)
class LMSSignature:
    q: int
    ots_signature: list[bytes]
    path: list[bytes]
    params: LMSParams

    def verify(self, message: bytes, root: bytes, seed: bytes, w: int = 4) -> bool:
        h = self.params.h
        leaf_seed = _sha256(seed + _int_to_bytes_be(self.q, 4))
        ls_val = 32
        u, v, max_val, total = _winternitz_params(w, ls_val)
        hashed = _sha256(message)
        n = len(hashed)
        digest = hashed[: min(ls_val, n)]
        if len(digest) < ls_val:
            digest = digest + b"\x00" * (ls_val - len(digest))
        msg_vals = _digest_to_values(digest, w, u)
        csum = 0
        for val in msg_vals:
            csum += max_val - val
        csum_bytes = csum.to_bytes((csum.bit_length() + 7) // 8 or 1, "big")
        if len(csum_bytes) < v:
            csum_bytes = b"\x00" * (v - len(csum_bytes)) + csum_bytes
        csum_bytes = csum_bytes[:v]
        csum_vals = _digest_to_values(csum_bytes, w, v)
        all_vals = msg_vals + csum_vals
        pk_candidate: list[bytes] = []
        for i in range(total):
            val = all_vals[i]
            pk_candidate.append(_chain(i, max_val - val, self.ots_signature[i], leaf_seed, w, start=val))
        combined_pk = b"".join(pk_candidate)
        leaf = _sha256(combined_pk)[:HASH_SIZE]
        return _verify_merkle_proof(root, leaf, self.q, self.path, seed, h)


# ---------------------------------------------------------------------------
# HSS — Hierarchical Signature System (multi-tree)
# ---------------------------------------------------------------------------


@dataclass
class HSSParams:
    levels: int
    tree_heights: list[int]
    lm_ots_params: list[int]

    def __post_init__(self) -> None:
        if self.levels < 1 or self.levels > 8:
            raise ValueError(f"levels must be 1-8, got {self.levels}")
        if len(self.tree_heights) != self.levels:
            raise ValueError(f"tree_heights length ({len(self.tree_heights)}) != levels ({self.levels})")
        if len(self.lm_ots_params) != self.levels:
            raise ValueError(f"lm_ots_params length ({len(self.lm_ots_params)}) != levels ({self.levels})")


@dataclass
class HSSKeyPair:
    params: HSSParams
    seed: bytes
    lms_keys: list[LMSKeyPair]
    root: bytes

    @classmethod
    def generate(cls, levels: int = 2, tree_heights: list[int] | None = None) -> HSSKeyPair:
        if tree_heights is None:
            tree_heights = [4] * levels
        params = HSSParams(
            levels=levels,
            tree_heights=tree_heights,
            lm_ots_params=[16] * levels,
        )
        seed = secrets.token_bytes(32)
        lms_keys: list[LMSKeyPair] = []
        for level in range(levels):
            level_seed = _sha256(seed + _int_to_bytes_be(level, 4))
            kp = LMSKeyPair.generate(h=tree_heights[level], m=16, w=4)
            kp.seed = level_seed
            lms_keys.append(kp)
        top_root = lms_keys[-1].root if lms_keys else b"\x00" * HASH_SIZE
        return cls(params=params, seed=seed, lms_keys=lms_keys, root=top_root)

    def public_key_bytes(self) -> bytes:
        return self.root


# ---------------------------------------------------------------------------
# Merkle tree utilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MerkleNodeData:
    index: int
    hash_value: bytes

    @property
    def hex(self) -> str:
        return self.hash_value.hex()


@dataclass
class MerkleTree:
    leaves: list[bytes]
    root: bytes
    height: int
    nodes: dict[int, bytes] = field(default_factory=dict)

    @classmethod
    def from_leaves(cls, leaves: list[bytes]) -> MerkleTree:
        if not leaves:
            raise ValueError("empty leaf list")
        n = len(leaves)
        height = 0
        while (1 << height) < n:
            height += 1
        padded = list(leaves) + [b"\x00" * HASH_SIZE] * ((1 << height) - n)
        leaf_start = 1 << height
        nodes: dict[int, bytes] = {}
        for i, v in enumerate(padded):
            nodes[leaf_start + i] = v
        for level in range(height - 1, -1, -1):
            start = 1 << level
            for idx in range(start, start + (1 << level)):
                left = nodes.get(2 * idx, b"\x00" * HASH_SIZE)
                right = nodes.get(2 * idx + 1, b"\x00" * HASH_SIZE)
                nodes[idx] = _sha256(left + right)
        root = nodes.get(1, b"\x00" * HASH_SIZE) if height > 0 else nodes.get(1, padded[0])
        return cls(leaves=leaves, root=root, height=height, nodes=nodes)

    def proof(self, leaf_index: int) -> list[bytes]:
        if leaf_index < 0 or leaf_index >= len(self.leaves):
            raise IndexError(f"leaf index {leaf_index} out of range")
        proof: list[bytes] = []
        idx = leaf_index + (1 << self.height)
        for _ in range(self.height):
            sibling = idx ^ 1
            proof.append(self.nodes.get(sibling, b"\x00" * HASH_SIZE))
            idx //= 2
        return proof

    def verify_proof(self, leaf: bytes, leaf_index: int, proof: list[bytes]) -> bool:
        if len(proof) != self.height:
            return False
        current = leaf
        idx = leaf_index + (1 << self.height)
        for sibling_hash in proof:
            combined = sibling_hash + current if idx & 1 else current + sibling_hash
            idx //= 2
            current = _sha256(combined)
        return current == self.root

    def diff(self, other: MerkleTree) -> list[int]:
        diff_leaves: list[int] = []
        max_len = max(len(self.leaves), len(other.leaves))
        for i in range(max_len):
            a = self.leaves[i] if i < len(self.leaves) else b""
            b_val = other.leaves[i] if i < len(other.leaves) else b""
            if a != b_val:
                diff_leaves.append(i)
        return diff_leaves

    def to_list(self) -> list[MerkleNodeData]:
        result: list[MerkleNodeData] = []
        for idx in sorted(self.nodes):
            result.append(MerkleNodeData(index=idx, hash_value=self.nodes[idx]))
        return result
