"""XMSS (RFC 8391) — Stateful Hash-Based Signature Scheme.

Implements the eXtended Merkle Signature Scheme using standard hash
primitives (hashlib/hmac). Self-contained — the ``cryptography`` library's
XMSS module is not available on macOS ARM64 (BoringSSL omits it).

XMSS is **stateful**: each keypair signs exactly ``2^height`` messages.
Reusing a leaf index irreversibly compromises security.

Provides
--------
* :func:`generate_xmss_keypair` — fresh XMSS keypair.
* :func:`xmss_sign` — sign a message, consuming one leaf index.
* :func:`xmss_verify` — verify a signature against a public key.
* :func:`xmss_signature_count` — number of signatures made.
* :func:`xmss_remaining_signatures` — how many remain.
* :func:`serialize_private_key` / :func:`deserialize_private_key`
* :func:`serialize_public_key` / :func:`deserialize_public_key`
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
from typing import Final

DEFAULT_HEIGHT: Final[int] = 10
DEFAULT_DIGEST: Final[str] = "SHA256"
_VALID_DIGESTS: Final[tuple[str, ...]] = ("SHA256", "SHA512", "SHAKE256", "SHAKE512")
_MIN_HEIGHT: Final[int] = 4
_MAX_HEIGHT: Final[int] = 20

_W: Final[int] = 16
_LOGW: Final[int] = 4


class XMSSError(Exception):
    """Error in XMSS operations."""


def _n(digest_name: str) -> int:
    return 32 if digest_name in ("SHA256", "SHAKE256") else 64


def _prf(seed: bytes, data: bytes, digest_name: str) -> bytes:
    n_out = _n(digest_name)
    result = hmac.new(seed, data, hashlib.sha256).digest()
    if n_out <= 32:
        return result[:n_out]
    return result + hmac.new(seed, result + data + b"\x00", hashlib.sha256).digest()[: n_out - 32]


def _h(seed: bytes, data: bytes, digest_name: str) -> bytes:
    n_out = _n(digest_name)
    h1 = hashlib.sha256(seed + data).digest()
    if n_out <= 32:
        return h1[:n_out]
    return h1 + hashlib.sha256(seed + data + b"\x01").digest()[: n_out - 32]


def _msg_hash(r: bytes, root: bytes, index: int, message: bytes, digest_name: str) -> bytes:
    n_out = _n(digest_name)
    idx_bytes = struct.pack(">Q", index)
    h_val = hashlib.sha256(r + root + idx_bytes + message).digest()
    if n_out <= 32:
        return h_val[:n_out]
    return h_val + hashlib.sha256(r + root + idx_bytes + message + b"\x01").digest()[: n_out - 32]


def _wots_len(n_out: int) -> int:
    import math

    l1 = (8 * n_out) // _LOGW
    log_val = math.log2(l1 * (_W - 1))
    l2 = math.floor(log_val / _LOGW) + 1
    return l1 + l2


def _coef(hash_val: bytes, i: int) -> int:
    shift = (i * _LOGW) % 8
    byte_idx = (i * _LOGW) // 8
    needed = bytearray(hash_val[byte_idx : byte_idx + 3])
    if len(needed) < 3:
        needed.extend(b"\x00" * (3 - len(needed)))
    val = int.from_bytes(bytes(needed), "big")
    mask = (1 << _LOGW) - 1
    return (val >> (24 - shift - _LOGW)) & mask


def _base_w(hash_val: bytes, out_len: int) -> list[int]:
    return [_coef(hash_val, i) for i in range(out_len)]


def _checksum(coefs: list[int], n_out: int) -> list[int]:
    l1 = (8 * n_out) // _LOGW
    l2 = _wots_len(n_out) - l1
    csum = sum((_W - 1) - v for v in coefs)
    csum_bytes = csum.to_bytes((l2 * _LOGW + 7) // 8, "big")
    return _base_w(csum_bytes, l2)


def _chain(seed: bytes, x: bytes, start: int, steps: int, adrs: bytes, digest_name: str) -> bytes:
    tmp = x
    for j in range(start, start + steps):
        cadrs = bytearray(adrs)
        cadrs[20:24] = struct.pack(">I", j)
        tmp = _h(seed, bytes(cadrs) + tmp, digest_name)
    return tmp


def _wots_sk(seed: bytes, adrs: bytes, n_out: int, digest_name: str) -> list[bytes]:
    length = _wots_len(n_out)
    sk: list[bytes] = []
    for i in range(length):
        sadrs = bytearray(adrs)
        sadrs[16:20] = struct.pack(">I", i)
        sk.append(_prf(seed, b"\x00" + bytes(sadrs), digest_name)[:n_out])
    return sk


def _wots_pk(seed: bytes, sk: list[bytes], adrs: bytes, digest_name: str) -> list[bytes]:
    pk: list[bytes] = []
    for i, ski in enumerate(sk):
        cadrs = bytearray(adrs)
        cadrs[16:20] = struct.pack(">I", i)
        pk.append(_chain(seed, ski, 0, _W - 1, bytes(cadrs), digest_name))
    return pk


def _wots_sign_msg(msg: bytes, seed: bytes, sk: list[bytes], adrs: bytes, digest_name: str) -> list[bytes]:
    n_out = _n(digest_name)
    l1 = (8 * n_out) // _LOGW
    length = _wots_len(n_out)

    h_val = _h(seed, adrs + msg, digest_name)
    msg_coefs = _base_w(h_val, l1)
    csum_coefs = _checksum(msg_coefs, n_out)
    coefs = msg_coefs + csum_coefs

    sig: list[bytes] = []
    for i in range(length):
        cadrs = bytearray(adrs)
        cadrs[16:20] = struct.pack(">I", i)
        sig.append(_chain(seed, sk[i], 0, coefs[i], bytes(cadrs), digest_name))
    return sig


def _wots_pk_from_msg(sig: list[bytes], msg: bytes, seed: bytes, adrs: bytes, digest_name: str) -> list[bytes]:
    n_out = _n(digest_name)
    l1 = (8 * n_out) // _LOGW
    length = _wots_len(n_out)

    h_val = _h(seed, adrs + msg, digest_name)
    msg_coefs = _base_w(h_val, l1)
    csum_coefs = _checksum(msg_coefs, n_out)
    coefs = msg_coefs + csum_coefs

    pk: list[bytes] = []
    for i in range(length):
        cadrs = bytearray(adrs)
        cadrs[16:20] = struct.pack(">I", i)
        pk.append(_chain(seed, sig[i], coefs[i], _W - 1 - coefs[i], bytes(cadrs), digest_name))
    return pk


def _ltree(leaves: list[bytes], seed: bytes, adrs: bytes, digest_name: str) -> bytes:
    nodes = list(leaves)
    idx = 0
    while len(nodes) > 1:
        nxt: list[bytes] = []
        for j in range(0, len(nodes), 2):
            tadrs = bytearray(adrs)
            tadrs[24:28] = struct.pack(">I", idx)
            val = _h(seed, bytes(tadrs) + nodes[j] + nodes[j + 1], digest_name) if j + 1 < len(nodes) else nodes[j]
            nxt.append(val)
            idx += 1
        nodes = nxt
    return nodes[0]


def _treehash(seed: bytes, start_idx: int, target_idx: int, adrs: bytes, digest_name: str) -> bytes:
    """RFC 8391 Algorithm 7: compute root of subtree spanning [start_idx, target_idx]."""
    stack: list[tuple[int, bytes]] = []
    n_out = _n(digest_name)
    for leaf_idx in range(start_idx, target_idx + 1):
        ots_adrs = bytearray(adrs)
        ots_adrs[16:20] = struct.pack(">I", leaf_idx)
        sk = _wots_sk(seed, bytes(ots_adrs), n_out, digest_name)
        pk = _wots_pk(seed, sk, bytes(ots_adrs), digest_name)
        node = _ltree(pk, seed, bytes(ots_adrs), digest_name)

        tadrs = bytearray(adrs)
        tadrs[24:28] = struct.pack(">I", leaf_idx)
        node = _h(seed, bytes(tadrs) + node, digest_name)

        node_height = 0
        while stack and stack[-1][0] == node_height:
            _rh, right = stack.pop()
            tadrs2 = bytearray(adrs)
            tadrs2[24:28] = struct.pack(">I", leaf_idx)
            node = _h(seed, bytes(tadrs2) + right + node, digest_name)
            node_height += 1
        stack.append((node_height, node))
    while len(stack) > 1:
        h1, n1 = stack.pop(0)
        h2, n2 = stack.pop(0)
        merged = _h(seed, adrs + n2 + n1, digest_name)
        stack.insert(0, (max(h1, h2) + 1, merged))
    return stack[0][1]


def _compute_root(seed: bytes, adrs: bytes, height: int, digest_name: str) -> bytes:
    return _treehash(seed, 0, (1 << height) - 1, adrs, digest_name)


def _build_auth_path(
    seed: bytes,
    adrs: bytes,
    height: int,
    leaf_idx: int,
    digest_name: str,
) -> list[bytes]:
    """Compute authentication path for leaf_idx."""
    auth: list[bytes] = []
    for k in range(height):
        sib = leaf_idx ^ (1 << k)
        s_adrs = bytearray(adrs)
        s_adrs[20:24] = struct.pack(">I", k)
        s_adrs[24:28] = struct.pack(">I", sib)
        auth.append(_treehash(seed, sib, sib, bytes(s_adrs), digest_name))
    return auth


def _verify_auth(
    wots_pk: list[bytes],
    seed: bytes,
    adrs: bytes,
    height: int,
    leaf_idx: int,
    auth: list[bytes],
    root: bytes,
    digest_name: str,
) -> bool:
    ots_adrs = bytearray(adrs)
    ots_adrs[16:20] = struct.pack(">I", leaf_idx)
    node = _ltree(wots_pk, seed, bytes(ots_adrs), digest_name)

    tadrs = bytearray(adrs)
    tadrs[24:28] = struct.pack(">I", leaf_idx)
    node = _h(seed, bytes(tadrs) + node, digest_name)

    pos = leaf_idx
    for k in range(height):
        t_adrs = bytearray(adrs)
        t_adrs[20:24] = struct.pack(">I", k)
        t_adrs[24:28] = struct.pack(">I", pos)
        if pos & 1:
            node = _h(seed, bytes(t_adrs) + auth[k] + node, digest_name)
        else:
            node = _h(seed, bytes(t_adrs) + node + auth[k], digest_name)
        pos >>= 1

    return node == root


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

_ENCODE_VERSION: Final[int] = 1


def _encode_private(seed: bytes, index: int, height: int, digest_name: str, pub_root: bytes) -> bytes:
    did = _VALID_DIGESTS.index(digest_name)
    header = struct.pack(">BBII", _ENCODE_VERSION, did, height, index)
    return header + seed + pub_root


def _decode_private(data: bytes) -> tuple[bytes, int, int, str, bytes]:
    if len(data) < 14:
        raise XMSSError(f"Private key too short: {len(data)} bytes")
    ver, did, height, index = struct.unpack(">BBII", data[:10])
    if ver != 1:
        raise XMSSError(f"Unknown private key version: {ver}")
    if did >= len(_VALID_DIGESTS):
        raise XMSSError(f"Unknown digest id: {did}")
    digest_name = _VALID_DIGESTS[did]
    n_out = _n(digest_name)
    seed = data[10 : 10 + n_out]
    pub_root = data[10 + n_out : 10 + n_out + n_out]
    return seed, index, height, digest_name, pub_root


def _encode_public(seed: bytes, pub_root: bytes, height: int, digest_name: str) -> bytes:
    did = _VALID_DIGESTS.index(digest_name)
    header = struct.pack(">BBH", _ENCODE_VERSION, did, height)
    return header + seed + pub_root


def _decode_public(data: bytes) -> tuple[bytes, bytes, int, str]:
    if len(data) < 9:
        raise XMSSError(f"Public key too short: {len(data)} bytes")
    ver, did, height = struct.unpack(">BBH", data[:4])
    if ver != 1:
        raise XMSSError(f"Unknown public key version: {ver}")
    if did >= len(_VALID_DIGESTS):
        raise XMSSError(f"Unknown digest id: {did}")
    digest_name = _VALID_DIGESTS[did]
    n_out = _n(digest_name)
    seed = data[4 : 4 + n_out]
    pub_root = data[4 + n_out : 4 + n_out + n_out]
    return seed, pub_root, height, digest_name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_xmss_keypair(
    height: int = DEFAULT_HEIGHT,
    digest_algorithm: str = DEFAULT_DIGEST,
) -> tuple[bytes, bytes]:
    """Generate a fresh XMSS keypair.

    Args:
        height: Tree height (4-20). Total signatures = ``2^height``.
        digest_algorithm: ``"SHA256"``, ``"SHA512"``, ``"SHAKE256"``, or ``"SHAKE512"``.

    Returns:
        ``(private_key_bytes, public_key_bytes)``.
    """
    if digest_algorithm not in _VALID_DIGESTS:
        raise XMSSError(f"Invalid digest_algorithm '{digest_algorithm}'. Must be one of {_VALID_DIGESTS}")
    if not (_MIN_HEIGHT <= height <= _MAX_HEIGHT):
        raise XMSSError(f"Height must be between {_MIN_HEIGHT} and {_MAX_HEIGHT}, got {height}")

    n_out = _n(digest_algorithm)
    seed = os.urandom(n_out)
    adrs = bytes(32)
    pub_root = _compute_root(seed, adrs, height, digest_algorithm)

    priv = _encode_private(seed, 0, height, digest_algorithm, pub_root)
    pub = _encode_public(seed, pub_root, height, digest_algorithm)
    return priv, pub


def xmss_sign(private_key_bytes: bytes, message: bytes | str) -> tuple[bytes, bytes]:
    """Sign *message* consuming one leaf index.

    Returns ``(signature, updated_private_key_bytes)``. The returned key
    must replace the old — the old key has been consumed.
    """
    if isinstance(message, str):
        message = message.encode()

    seed, index, height, digest_name, pub_root = _decode_private(private_key_bytes)
    n_out = _n(digest_name)
    max_sigs = 1 << height

    if index >= max_sigs:
        raise XMSSError(f"XMSS key exhausted: {index} of {max_sigs} signatures used")

    r = _prf(seed, struct.pack(">I", index) + message, digest_name)[:n_out]
    msg_digest = _msg_hash(r, pub_root, index, message, digest_name)

    ots_adrs = bytearray(32)
    ots_adrs[16:20] = struct.pack(">I", index)
    sk = _wots_sk(seed, bytes(ots_adrs), n_out, digest_name)
    wots_sig = _wots_sign_msg(msg_digest, seed, sk, bytes(ots_adrs), digest_name)

    adrs = bytes(32)
    auth = _build_auth_path(seed, adrs, height, index, digest_name)

    sig_parts = [struct.pack(">I", index), r]
    for s in wots_sig:
        sig_parts.append(struct.pack(">H", len(s)))
        sig_parts.append(s)
    sig_parts.extend(auth)
    signature = b"".join(sig_parts)

    updated = _encode_private(seed, index + 1, height, digest_name, pub_root)
    return signature, updated


def xmss_verify(public_key_bytes: bytes, message: bytes | str, signature: bytes) -> bool:
    """Verify an XMSS *signature* for *message*."""
    if isinstance(message, str):
        message = message.encode()

    try:
        seed, pub_root, height, digest_name = _decode_public(public_key_bytes)
    except XMSSError:
        return False

    n_out = _n(digest_name)
    wots_len = _wots_len(n_out)

    pos = 0
    if len(signature) < 4 + n_out:
        return False
    index = struct.unpack(">I", signature[pos : pos + 4])[0]
    pos += 4
    r = signature[pos : pos + n_out]
    pos += n_out

    if index >= (1 << height):
        return False

    wots_sig: list[bytes] = []
    for _i in range(wots_len):
        if pos + 2 > len(signature):
            return False
        elen = struct.unpack(">H", signature[pos : pos + 2])[0]
        pos += 2
        if pos + elen > len(signature):
            return False
        wots_sig.append(signature[pos : pos + elen])
        pos += elen

    if pos + height * n_out != len(signature):
        return False
    auth: list[bytes] = []
    for _k in range(height):
        auth.append(signature[pos : pos + n_out])
        pos += n_out

    msg_digest = _msg_hash(r, pub_root, index, message, digest_name)

    ots_adrs = bytearray(32)
    ots_adrs[16:20] = struct.pack(">I", index)
    pk_from_sig = _wots_pk_from_msg(wots_sig, msg_digest, seed, bytes(ots_adrs), digest_name)

    adrs = bytes(32)
    return _verify_auth(pk_from_sig, seed, adrs, height, index, auth, pub_root, digest_name)


def xmss_signature_count(private_key_bytes: bytes) -> int:
    """Return the number of signatures made so far."""
    _, index, _, _, _ = _decode_private(private_key_bytes)
    return index


def xmss_remaining_signatures(private_key_bytes: bytes, height: int) -> int:
    """Return how many signatures remain for this keypair."""
    index = xmss_signature_count(private_key_bytes)
    return (1 << height) - index


def serialize_private_key(private_key_bytes: bytes) -> bytes:
    """Identity serialisation. Provided for API symmetry."""
    return private_key_bytes


def deserialize_private_key(data: bytes) -> bytes:
    """Validate and return private key bytes."""
    _decode_private(data)
    return data


def serialize_public_key(public_key_bytes: bytes) -> bytes:
    """Identity serialisation. Provided for API symmetry."""
    return public_key_bytes


def deserialize_public_key(data: bytes) -> bytes:
    """Validate and return public key bytes."""
    _decode_public(data)
    return data
