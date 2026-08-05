"""HKDF, HMAC-based KDF, and PBKDF2 — RFC 5869, RFC 2104, RFC 8018.

Pure-Python, stdlib only.  Provides HKDF-Extract, HKDF-Expand, a combined
HKDF interface, HMAC-KB (NIST SP 800-108 counter mode), and PBKDF2-HMAC-SHA256.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Final

HASH_SHA256: Final[str] = "sha256"
HASH_SHA512: Final[str] = "sha512"
HASHLEN: Final[dict[str, int]] = {"sha256": 32, "sha512": 64}
HMAC_BLOCK_SIZE: Final[dict[str, int]] = {"sha256": 64, "sha512": 128}
MAX_HKDF_OUTPUT: Final[int] = 255 * 32


class HKDFError(ValueError):
    """Base exception for HKDF / KDF operations."""


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=False))


def _hmac_digest(key: bytes, data: bytes, hash_name: str) -> bytes:
    """HMAC (RFC 2104) computed manually — no type: ignore needed."""
    block_size = HMAC_BLOCK_SIZE[hash_name]
    if len(key) > block_size:
        key = hashlib.new(hash_name, key).digest()
    if len(key) < block_size:
        key = key.ljust(block_size, b"\x00")
    o_key_pad = _xor_bytes(key, b"\x5c" * block_size)
    i_key_pad = _xor_bytes(key, b"\x36" * block_size)
    inner = hashlib.new(hash_name, i_key_pad + data).digest()
    return hashlib.new(hash_name, o_key_pad + inner).digest()


# ── HKDF-Extract ─────────────────────────────────────────────────────────


def hkdf_extract(salt: bytes, ikm: bytes, hash_name: str = HASH_SHA256) -> bytes:
    """HKDF-Extract (RFC 5869 §2.2): PRK = HMAC-Hash(salt, IKM).

    If salt is empty, use a string of HashLen zeros.
    """
    if hash_name not in HASHLEN:
        raise HKDFError(f"Unsupported hash: {hash_name}")
    if not salt:
        salt = b"\x00" * HASHLEN[hash_name]
    return _hmac_digest(salt, ikm, hash_name)


# ── HKDF-Expand ──────────────────────────────────────────────────────────


def hkdf_expand(prk: bytes, info: bytes, length: int, hash_name: str = HASH_SHA256) -> bytes:
    """HKDF-Expand (RFC 5869 §2.3): OKM = T(1) || T(2) || ... || T(N).

    PRK must be at least HashLen bytes.  Output length must not exceed
    255 * HashLen.
    """
    if hash_name not in HASHLEN:
        raise HKDFError(f"Unsupported hash: {hash_name}")
    hl = HASHLEN[hash_name]
    if len(prk) < hl:
        raise HKDFError(f"PRK too short: {len(prk)} < {hl}")
    if length > 255 * hl:
        raise HKDFError(f"Requested length {length} exceeds max {255 * hl}")

    okm = b""
    t_prev = b""
    for i in range(1, (length + hl - 1) // hl + 1):
        t_prev = _hmac_digest(prk, t_prev + info + bytes([i]), hash_name)
        okm += t_prev
    return okm[:length]


# ── Combined HKDF ────────────────────────────────────────────────────────


def hkdf(
    ikm: bytes,
    length: int,
    salt: bytes = b"",
    info: bytes = b"",
    hash_name: str = HASH_SHA256,
) -> bytes:
    """HKDF (RFC 5869 §3): Extract-then-Expand."""
    prk = hkdf_extract(salt, ikm, hash_name)
    return hkdf_expand(prk, info, length, hash_name)


# ── HMAC-based KDF (NIST SP 800-108 counter mode) ────────────────────────


def hmac_kb_kdf(
    key: bytes,
    label: bytes,
    context: bytes,
    length: int,
    hash_name: str = HASH_SHA256,
    counter_width: int = 4,
) -> bytes:
    """HMAC-based KB-KDF in counter mode (NIST SP 800-108 §5.1).

    K(i) = HMAC(KI, [i]_{cw} || Label || 0x00 || Context || [L]_{cw})
    """
    if hash_name not in HASHLEN:
        raise HKDFError(f"Unsupported hash: {hash_name}")
    if length == 0:
        return b""
    HASHLEN[hash_name]
    okm = b""
    counter = 1
    while len(okm) < length:
        i_bytes = counter.to_bytes(counter_width, "big")
        l_bytes = (length * 8).to_bytes(counter_width, "big")
        ki = i_bytes + label + b"\x00" + context + l_bytes
        okm += _hmac_digest(key, ki, hash_name)
        counter += 1
    return okm[:length]


# ── PBKDF2 ───────────────────────────────────────────────────────────────


def pbkdf2(
    password: bytes,
    salt: bytes,
    iterations: int,
    dklen: int,
    hash_name: str = HASH_SHA256,
) -> bytes:
    """PBKDF2-HMAC (RFC 8018 §5.2).

    Args:
        password: Master password (bytes).
        salt: Cryptographic salt (bytes).
        iterations: Count (≥ 1; ≥ 100_000 recommended for production).
        dklen: Derived key length in bytes.
        hash_name: "sha256" or "sha512".

    Returns:
        Derived key of `dklen` bytes.

    Raises:
        HKDFError: If iterations < 1 or dklen too large.
    """
    if hash_name not in HASHLEN:
        raise HKDFError(f"Unsupported hash: {hash_name}")
    if iterations < 1:
        raise HKDFError(f"iterations must be ≥ 1, got {iterations}")
    if dklen == 0:
        return b""

    hl = HASHLEN[hash_name]
    n_blocks = (dklen + hl - 1) // hl
    if n_blocks > 2**32 - 1:
        raise HKDFError(f"dklen {dklen} too large for {hash_name}")

    dk = b""
    for i in range(1, n_blocks + 1):
        u = _hmac_digest(password, salt + struct.pack(">I", i), hash_name)
        t = u
        for _ in range(1, iterations):
            u = _hmac_digest(password, u, hash_name)
            t = _xor_bytes(t, u)
        dk += t
    return dk[:dklen]
