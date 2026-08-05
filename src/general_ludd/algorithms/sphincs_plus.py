"""
Experimental SPHINCS+ (FIPS 205 / SLH-DSA) — stateless hash-based signatures.

Pure-Python, stdlib-only reference implementation of the SPHINCS+ construction:
WOTS+ chains → XMSS trees → hypertree → FORS → SPHINCS+ sign/verify.

Uses SHAKE-256 as the underlying extendable-output hash function.
Implements configurable parameter sets.

DO NOT USE IN PRODUCTION — this is experimental/educational code.
"""

from __future__ import annotations

import hashlib
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass

# ── SPHINCS+ parameters ────────────────────────────────────────────────
# n  = security parameter in bytes
# w  = Winternitz parameter
# h_prime = total hypertree height
# d  = number of hypertree layers
# a  = FORS number of sub-trees
# k  = FORS number of leaves per tree
# h  = h_prime / d  (height of each subtree)
# t  = 2 ** a  (number of FORS leaves per sub-tree)


@dataclass(slots=True, frozen=True)
class SphincsParams:
    n: int
    w: int
    h_prime: int
    d: int
    a: int
    k: int

    @property
    def h(self) -> int:
        return self.h_prime // self.d

    @property
    def wots_len1(self) -> int:
        return (self.n * 8) // (self.w.bit_length() - 1)

    @property
    def wots_len2(self) -> int:
        return 3

    @property
    def wots_len(self) -> int:
        return self.wots_len1 + self.wots_len2

    @property
    def tree_bits(self) -> int:
        return 2**self.a  # type: ignore[no-any-return]

    @property
    def pk_bytes(self) -> int:
        return 2 * self.n

    @property
    def sk_bytes(self) -> int:
        return 4 * self.n

    @property
    def sig_bytes(self) -> int:
        c_sig = self.k * (self.a + 1) * self.n
        wots_sig = self.wots_len * self.n
        xmss_sig = self.h * self.n + self.d * wots_sig
        return self.n + c_sig + xmss_sig


_PARAMS_SLH_DSA_SHAKE_256s = SphincsParams(n=16, w=16, h_prime=63, d=7, a=12, k=14)

_PARAMS_SMALL = SphincsParams(n=16, w=16, h_prime=6, d=2, a=2, k=2)


class SphincsPlusError(Exception):
    """Base exception for SPHINCS+ operations."""


# ── Address scheme ─────────────────────────────────────────────────────


def _addr_increment(addr: bytearray) -> None:
    addr[28] = (addr[28] + 1) & 0xFF
    if addr[28] == 0:
        addr[29] = (addr[29] + 1) & 0xFF


def _make_addr(layer: int = 0, tree_idx: int = 0, addr_type: int = 0, leaf_idx: int = 0) -> bytearray:
    addr = bytearray(32)
    struct.pack_into(">I", addr, 0, layer)
    struct.pack_into(">Q", addr, 4, tree_idx)
    addr[12:16] = b"\x00" * 4
    struct.pack_into(">I", addr, 16, addr_type)
    struct.pack_into(">I", addr, 20, leaf_idx)
    return addr


_WOTS_HASH, _WOTS_PK, _WOTS_KEY = 0, 2, 3
_FORS_TREE, _FORS_ROOTS, _FORS_KEY = 0, 2, 3
_XMSS = 4
_SPX_TREE = 4


def _addr_set_type(addr: bytearray, typ: int) -> None:
    struct.pack_into(">I", addr, 16, typ)


def _addr_set_leaf(addr: bytearray, leaf: int) -> None:
    struct.pack_into(">I", addr, 20, leaf)


def _addr_set_chain(addr: bytearray, chain: int) -> None:
    struct.pack_into(">I", addr, 24, chain)


def _addr_set_hash(addr: bytearray, hsh: int) -> None:
    struct.pack_into(">I", addr, 28, hsh)


def _addr_set_tree_height(addr: bytearray, height: int) -> None:
    struct.pack_into(">I", addr, 24, height)


def _addr_set_tree_index(addr: bytearray, index: int) -> None:
    struct.pack_into(">I", addr, 28, index)


# ── SHAKE-256 based hash engine ────────────────────────────────────────


def _shake(n_out: int, *parts: bytes) -> bytes:
    return hashlib.shake_256(b"".join(parts)).digest(n_out)


def _prf(seed: bytes, addr: bytearray) -> bytes:
    return hashlib.shake_256(seed + bytes(addr)).digest(len(seed))


def _prf_msg(sk_prf: bytes, opt_rand: bytes, msg: bytes, n_out: int | None = None) -> bytes:
    if n_out is None:
        n_out = len(sk_prf)
    return hashlib.shake_256(sk_prf + opt_rand + msg).digest(n_out)


def _hash_msg(r: bytes, pk_seed: bytes, pk_root: bytes, msg: bytes) -> bytes:
    return hashlib.shake_256(r + pk_seed + pk_root + msg).digest(len(r))


def _thash_f(n: int, pk_seed: bytes, addr: bytearray, *inputs: bytes) -> bytes:
    concat = b"".join(inputs)
    return hashlib.shake_256(pk_seed + bytes(addr) + concat).digest(n)


def _thash_h(n: int, pk_seed: bytes, addr: bytearray, left: bytes, right: bytes) -> bytes:
    return hashlib.shake_256(pk_seed + bytes(addr) + left + right).digest(n)


# ── WOTS+ chain ────────────────────────────────────────────────────────


def wots_chain(n: int, w: int, pk_seed: bytes, addr: bytearray, start: bytes, steps: int, chain_addr: int) -> bytes:
    val = bytes(start)
    _addr_set_hash(addr, chain_addr)
    for i in range(steps):
        _addr_set_chain(addr, chain_addr + i)
        val = hashlib.shake_256(pk_seed + bytes(addr) + val).digest(n)
    return val


def _compute_base_w(w: int, val: bytes, out_len: int) -> list[int]:
    coeffs: list[int] = []
    w.bit_length() - 1  # log2(w)
    mask = w - 1
    consumed = 0
    for byte_val in val:
        for shift in (0, 4):
            if consumed >= out_len:
                return coeffs
            coeffs.append((byte_val >> shift) & mask)
            consumed += 1
    return coeffs


def _wots_checksum(chains: list[int], w: int, len2: int) -> list[int]:
    max_val = w - 1
    s = sum(max_val - v for v in chains)
    s <<= 4
    result: list[int] = []
    for _ in range(len2):
        result.append(s & (w - 1))
        s >>= (w - 1).bit_length() - 1
    return result


def wots_gen_pk(n: int, w: int, sk_seed: bytes, pk_seed: bytes, addr: bytearray, params: SphincsParams) -> bytes:
    max_step = w - 1
    sk_chains = _compose_sk(n, sk_seed, addr, params)
    pk: list[bytes] = []
    for i in range(params.wots_len):
        pk.append(wots_chain(n, w, pk_seed, addr, sk_chains[i], max_step, i * max_step))
    addr_pk = bytearray(addr)
    _addr_set_type(addr_pk, _WOTS_PK)
    _addr_set_leaf(addr_pk, struct.unpack_from(">I", addr, 20)[0])
    _addr_set_chain(addr_pk, 0)
    _addr_set_hash(addr_pk, 0)
    return hashlib.shake_256(pk_seed + bytes(addr_pk) + b"".join(pk)).digest(n)


def _compose_sk(n: int, sk_seed: bytes, addr: bytearray, params: SphincsParams) -> list[bytes]:
    sk_chains: list[bytes] = []
    for i in range(params.wots_len):
        addr_key = bytearray(addr)
        struct.pack_into(">I", addr_key, 16, _WOTS_KEY)
        struct.pack_into(">I", addr_key, 20, i)
        sk_chains.append(_prf(sk_seed, addr_key))
    return sk_chains


def wots_sign(
    n: int, w: int, sk_seed: bytes, pk_seed: bytes, addr: bytearray, msg_digest: bytes, params: SphincsParams
) -> list[bytes]:
    sk_chains = _compose_sk(n, sk_seed, addr, params)
    coeffs = _compute_base_w(w, msg_digest, params.wots_len1)
    checksum = _wots_checksum(coeffs, w, params.wots_len2)
    all_coeffs = coeffs + checksum
    sig: list[bytes] = []
    max_step = w - 1
    for i, v in enumerate(all_coeffs):
        base_addr = i * max_step
        sig.append(wots_chain(n, w, pk_seed, addr, sk_chains[i], v, base_addr))
    return sig


def wots_pk_from_sig(
    n: int, w: int, pk_seed: bytes, addr: bytearray, sig: list[bytes], msg_digest: bytes, params: SphincsParams
) -> bytes:
    coeffs = _compute_base_w(w, msg_digest, params.wots_len1)
    checksum = _wots_checksum(coeffs, w, params.wots_len2)
    all_coeffs = coeffs + checksum
    max_step = w - 1
    pk: list[bytes] = []
    for i, v in enumerate(all_coeffs):
        base_addr = i * max_step
        pk.append(wots_chain(n, w, pk_seed, addr, sig[i], max_step - v, base_addr + v))
    addr_pk = bytearray(addr)
    _addr_set_type(addr_pk, _WOTS_PK)
    _addr_set_hash(addr_pk, 0)
    _addr_set_chain(addr_pk, 0)
    return hashlib.shake_256(pk_seed + bytes(addr_pk) + b"".join(pk)).digest(n)


# ── XMSS Merkle tree ───────────────────────────────────────────────────


def _xmss_node(
    n: int,
    h: int,
    sk_seed: bytes,
    pk_seed: bytes,
    addr: bytearray,
    node_idx: int,
    node_height: int,
    params: SphincsParams,
) -> bytes:
    if node_height == 0:
        addr_leaf = bytearray(addr)
        _addr_set_leaf(addr_leaf, node_idx)
        return wots_gen_pk(n, params.w, sk_seed, pk_seed, addr_leaf, params)
    left = _xmss_node(n, h, sk_seed, pk_seed, addr, 2 * node_idx, node_height - 1, params)
    right = _xmss_node(n, h, sk_seed, pk_seed, addr, 2 * node_idx + 1, node_height - 1, params)
    addr_node = bytearray(addr)
    _addr_set_tree_height(addr_node, node_height)
    _addr_set_tree_index(addr_node, node_idx)
    return _thash_h(n, pk_seed, addr_node, left, right)


def xmss_gen_pk(n: int, h: int, sk_seed: bytes, pk_seed: bytes, addr: bytearray, params: SphincsParams) -> bytes:
    return _xmss_node(n, h, sk_seed, pk_seed, addr, 0, h, params)


def xmss_sign(
    n: int,
    h: int,
    sk_seed: bytes,
    pk_seed: bytes,
    addr: bytearray,
    msg_digest: bytes,
    leaf_idx: int,
    params: SphincsParams,
) -> tuple[list[bytes], list[bytes]]:
    auth: list[bytes] = []
    for j in range(h):
        sibling = leaf_idx ^ (1 << j)
        auth.append(_xmss_node(n, h, sk_seed, pk_seed, addr, sibling, j, params))
    addr_leaf = bytearray(addr)
    _addr_set_leaf(addr_leaf, leaf_idx)
    wots_sig = wots_sign(n, params.w, sk_seed, pk_seed, addr_leaf, msg_digest, params)
    return wots_sig, auth


def xmss_pk_from_sig(
    n: int,
    h: int,
    leaf_idx: int,
    wots_sig: list[bytes],
    auth: list[bytes],
    pk_seed: bytes,
    addr: bytearray,
    msg_digest: bytes,
    params: SphincsParams,
    leaf_mid_fn: Callable[[bytes, bytearray], bytes] | None = None,
) -> bytes:
    addr_leaf = bytearray(addr)
    _addr_set_leaf(addr_leaf, leaf_idx)
    node = wots_pk_from_sig(n, params.w, pk_seed, addr_leaf, wots_sig, msg_digest, params)
    if leaf_mid_fn is not None:
        node = leaf_mid_fn(node, bytearray(addr_leaf))
    for j in range(h):
        addr_node = bytearray(addr)
        _addr_set_tree_height(addr_node, j)
        sibling_idx = leaf_idx ^ (1 << j)
        if (leaf_idx >> j) & 1:
            left, right = auth[j], node
        else:
            left, right = node, auth[j]
        _addr_set_tree_index(addr_node, sibling_idx >> (j + 1) if j + 1 < h else 0)
        node = _thash_h(n, pk_seed, addr_node, left, right)
    return node


# ── FORS ────────────────────────────────────────────────────────────────


def _fors_sk_addr(addr: bytearray, i: int) -> bytes:
    addr_key = bytearray(addr)
    _addr_set_type(addr_key, _FORS_KEY)
    struct.pack_into(">I", addr_key, 20, i)
    return bytes(addr_key)


def _fors_leaf(n: int, sk_seed: bytes, pk_seed: bytes, addr: bytearray, i: int, a_val: int) -> bytes:
    key = _prf(sk_seed, bytearray(_fors_sk_addr(addr, i)))
    addr_tree = bytearray(addr)
    _addr_set_type(addr_tree, _FORS_TREE)
    struct.pack_into(">I", addr_tree, 20, i // (2**a_val))
    return _thash_f(n, pk_seed, addr_tree, key)


def fors_sign(
    n: int, params: SphincsParams, sk_seed: bytes, pk_seed: bytes, addr: bytearray, msg_digest: bytes
) -> list[bytes]:
    idx_bytes = _prf_msg(sk_seed[:n], b"", msg_digest)
    sig: list[bytes] = []
    for i in range(params.k):
        offset = i * 2
        idx = struct.unpack_from(">H", idx_bytes, offset)[0] & ((1 << params.a) - 1)
        tree_base = i * (2**params.a)
        sk_addr = _fors_sk_addr(addr, tree_base + idx)
        sig.append(_prf(sk_seed, bytearray(sk_addr)))
        tree_addr = bytearray(addr)
        _addr_set_type(tree_addr, _FORS_TREE)
        struct.pack_into(">I", tree_addr, 20, i)
        auth: list[bytes] = []
        node_idx = tree_base + idx
        for j in range(params.a):
            sibling = node_idx ^ (1 << j)
            auth.append(_fors_leaf(n, sk_seed, pk_seed, tree_addr, sibling, params.a))
        sig.extend(auth)
    return sig


def fors_pk_from_sig(
    n: int, params: SphincsParams, pk_seed: bytes, addr: bytearray, sig: list[bytes], msg_digest: bytes
) -> bytes:
    idx_bytes = _prf_msg(pk_seed[:n], b"", msg_digest)
    roots: list[bytes] = []
    pos = 0
    for i in range(params.k):
        offset = i * 2
        idx = struct.unpack_from(">H", idx_bytes, offset)[0] & ((1 << params.a) - 1)
        sk_value = sig[pos]
        pos += 1
        tree_addr = bytearray(addr)
        _addr_set_type(tree_addr, _FORS_TREE)
        struct.pack_into(">I", tree_addr, 20, i)
        node = _thash_f(n, pk_seed, tree_addr, sk_value)
        for j in range(params.a):
            auth_val = sig[pos]
            pos += 1
            addr_node = bytearray(addr)
            _addr_set_type(addr_node, _FORS_TREE)
            struct.pack_into(">I", addr_node, 20, i)
            _addr_set_tree_height(addr_node, j)
            node_idx = ((2**params.a) * i) + idx
            if (node_idx >> j) & 1:
                left, right = auth_val, node
            else:
                left, right = node, auth_val
            _addr_set_tree_index(addr_node, (node_idx >> (j + 1)) if j + 1 < params.a else 0)
            node = _thash_h(n, pk_seed, addr_node, left, right)
        roots.append(node)
    addr_roots = bytearray(addr)
    _addr_set_type(addr_roots, _FORS_ROOTS)
    return _thash_h(n, pk_seed, addr_roots, b"".join(roots), bytes(len(roots)))


# ── Hypertree ──────────────────────────────────────────────────────────


def ht_sign(n: int, params: SphincsParams, sk_seed: bytes, pk_seed: bytes, leaf_idx: int, msg_digest: bytes) -> bytes:
    h = params.h
    d = params.d
    sig_parts: list[bytes] = []
    cur_msg = bytes(msg_digest)
    tree_bytes = 8 * n
    tree_idx_bytes = leaf_idx.to_bytes(tree_bytes, "big")
    for j in range(d):
        layer = d - 1 - j
        tree_big = int.from_bytes(tree_idx_bytes[(d - 1 - j) * n : (d - j) * n], "big")
        if j == 0:
            tree = tree_big & ((1 << h) - 1)
            tree_big >> h
        else:
            tree = tree_big & ((1 << h) - 1)
            tree_big >> h
        addr = _make_addr(layer=layer, tree_idx=tree, addr_type=_SPX_TREE)
        leaf = leaf_idx & ((1 << h) - 1)
        wots_sig, auth = xmss_sign(n, h, sk_seed, pk_seed, addr, cur_msg, leaf, params)
        for ws in wots_sig:
            sig_parts.append(ws)
        for a in auth:
            sig_parts.append(a)
        if j < d - 1:
            pk_from = xmss_pk_from_sig(n, h, leaf, wots_sig, auth, pk_seed, addr, cur_msg, params)
            cur_msg = pk_from
    return b"".join(sig_parts)


# ── SPHINCS+ top-level ─────────────────────────────────────────────────


def _hash_pk(pk_seed: bytes, root: bytes) -> bytes:
    return hashlib.shake_256(pk_seed + root).digest(len(pk_seed))


def slh_keygen(params: SphincsParams | None = None) -> tuple[bytes, bytes]:
    if params is None:
        params = _PARAMS_SLH_DSA_SHAKE_256s
    n = params.n
    sk_seed = os.urandom(n)
    sk_prf = os.urandom(n)
    pk_seed = os.urandom(n)
    root_addr = _make_addr(addr_type=_SPX_TREE, tree_idx=0)
    root = xmss_gen_pk(n, params.h, sk_seed, pk_seed, root_addr, params)
    pk = pk_seed + root
    sk = sk_seed + sk_prf + pk_seed + root
    return pk, sk


def slh_sign(msg: bytes, sk: bytes, params: SphincsParams | None = None) -> bytes:
    if params is None:
        params = _PARAMS_SLH_DSA_SHAKE_256s
    n = params.n
    sk_seed = sk[:n]
    sk_prf = sk[n : 2 * n]
    pk_seed = sk[2 * n : 3 * n]
    pk_root = sk[3 * n : 4 * n]
    opt_rand = pk_seed  # deterministic mode
    r = _prf_msg(sk_prf, opt_rand, msg)
    msg_hash = _hash_msg(r, pk_seed, pk_root, msg)
    md = msg_hash[:n]
    idx_bytes = msg_hash[16:24] if len(msg_hash) >= 24 else msg_hash[n : 2 * n]
    tree_idx = int.from_bytes(idx_bytes, "big") & ((1 << (params.h_prime - params.h)) - 1)
    leaf_idx = (
        int.from_bytes(msg_hash[n * 3 : n * 4], "big")
        if len(msg_hash) >= 4 * n
        else int.from_bytes(idx_bytes, "big") & ((1 << params.h) - 1)
    )
    addr = _make_addr(addr_type=_FORS_TREE)
    fors_sig = fors_sign(n, params, sk_seed, pk_seed, addr, md)
    fors_pk_bytes = fors_pk_from_sig(n, params, pk_seed, addr, fors_sig, md)
    ht_addr = _make_addr(addr_type=_SPX_TREE, tree_idx=tree_idx)

    def _leaf_mid(node: bytes, _a: bytearray) -> bytes:
        return _thash_h(n, pk_seed, _a, node, fors_pk_bytes)

    wots_sig, auth = xmss_sign(n, params.h, sk_seed, pk_seed, ht_addr, md, leaf_idx, params)
    xmss_pk_from_sig(n, params.h, leaf_idx, wots_sig, auth, pk_seed, ht_addr, md, params, _leaf_mid)
    ht_sig_parts: list[bytes] = [w for w in wots_sig] + [a for a in auth]
    sig_parts: list[bytes] = [r]
    sig_parts.extend(fors_sig)
    sig_parts.extend(ht_sig_parts)
    return b"".join(sig_parts)


def slh_verify(msg: bytes, sig: bytes, pk: bytes, params: SphincsParams | None = None) -> bool:
    if params is None:
        params = _PARAMS_SLH_DSA_SHAKE_256s
    n = params.n
    if len(sig) < n:
        return False
    if len(pk) < 2 * n:
        return False
    r = sig[:n]
    pk_seed = pk[:n]
    pk_root = pk[n:]
    msg_hash = _hash_msg(r, pk_seed, pk_root, msg)
    md = msg_hash[:n]
    fors_sig_len = params.k * (params.a + 1) * n
    if len(sig) < n + fors_sig_len:
        return False
    fors_bytes = sig[n : n + fors_sig_len]
    ht_bytes = sig[n + fors_sig_len :]
    fors_sig_list: list[bytes] = []
    offset = 0
    for _ in range(params.k * (params.a + 1)):
        fors_sig_list.append(fors_bytes[offset : offset + n])
        offset += n
    addr = _make_addr(addr_type=_FORS_TREE)
    try:
        fors_pk_bytes = fors_pk_from_sig(n, params, pk_seed, addr, fors_sig_list, md)
    except (IndexError, struct.error):
        return False
    msg_hash2 = _hash_msg(r, pk_seed, pk_root, msg)
    idx_bytes = msg_hash2[16:24] if len(msg_hash2) >= 24 else msg_hash2[n : 2 * n]
    tree_idx = int.from_bytes(idx_bytes, "big") & ((1 << (params.h_prime - params.h)) - 1)
    leaf_idx = (
        int.from_bytes(msg_hash2[n * 3 : n * 4], "big")
        if len(msg_hash2) >= 4 * n
        else int.from_bytes(idx_bytes, "big") & ((1 << params.h) - 1)
    )
    wots_sig_len = params.wots_len * n
    auth_len = params.h * n
    ht_sig_len = wots_sig_len + auth_len
    if len(ht_bytes) < ht_sig_len:
        return False
    wots_sig_list: list[bytes] = []
    for i in range(params.wots_len):
        wots_sig_list.append(ht_bytes[i * n : (i + 1) * n])
    auth_list: list[bytes] = []
    auth_start = wots_sig_len
    for j in range(params.h):
        auth_list.append(ht_bytes[auth_start + j * n : auth_start + (j + 1) * n])
    ht_addr = _make_addr(addr_type=_SPX_TREE, tree_idx=tree_idx)

    def _leaf_mid(node: bytes, _a: bytearray) -> bytes:
        return _thash_h(n, pk_seed, _a, node, fors_pk_bytes)

    try:
        xmss_pk_bytes = xmss_pk_from_sig(
            n, params.h, leaf_idx, wots_sig_list, auth_list, pk_seed, ht_addr, md, params, _leaf_mid
        )
    except (IndexError, struct.error):
        return False
    return xmss_pk_bytes == pk_root


# ── Parameter-specific convenience functions ───────────────────────────


def keygen_small() -> tuple[bytes, bytes]:
    return slh_keygen(_PARAMS_SMALL)


def sign_small(msg: bytes, sk: bytes) -> bytes:
    return slh_sign(msg, sk, _PARAMS_SMALL)


def verify_small(msg: bytes, sig: bytes, pk: bytes) -> bool:
    return slh_verify(msg, sig, pk, _PARAMS_SMALL)
