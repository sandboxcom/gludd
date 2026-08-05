"""HKDF, HMAC-based KDF, and PBKDF2 — RFC 5869, RFC 2104, RFC 8018.

Uses stdlib `hmac` for HMAC operations and `cryptography` for HKDF and PBKDF2.
"""

from __future__ import annotations

import hmac as _stdlib_hmac
from typing import Final

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF as _CryptoHKDF
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand as _CryptoHKDFExpand
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as _CryptoPBKDF2

HASH_SHA256: Final[str] = "sha256"
HASH_SHA512: Final[str] = "sha512"
HASHLEN: Final[dict[str, int]] = {"sha256": 32, "sha512": 64}
MAX_HKDF_OUTPUT: Final[int] = 255 * 32


class HKDFError(ValueError):
    """Base exception for HKDF / KDF operations."""


def _hash_alg(hash_name: str) -> hashes.HashAlgorithm:
    """Convert string hash name to cryptography :class:`~cryptography.hazmat.primitives.hashes.HashAlgorithm`."""
    if hash_name == "sha256":
        return hashes.SHA256()
    if hash_name == "sha512":
        return hashes.SHA512()
    raise HKDFError(f"Unsupported hash: {hash_name}")


def hkdf_extract(salt: bytes, ikm: bytes, hash_name: str = HASH_SHA256) -> bytes:
    """HKDF-Extract (RFC 5869 §2.2): PRK = HMAC-Hash(salt, IKM).

    If salt is empty, use a string of HashLen zeros.
    """
    if hash_name not in HASHLEN:
        raise HKDFError(f"Unsupported hash: {hash_name}")
    if not salt:
        salt = b"\x00" * HASHLEN[hash_name]
    return _stdlib_hmac.new(salt, ikm, hash_name).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int, hash_name: str = HASH_SHA256) -> bytes:
    """HKDF-Expand (RFC 5869 §2.3). Uses cryptography's HKDFExpand.

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

    return _CryptoHKDFExpand(
        algorithm=_hash_alg(hash_name),
        length=length,
        info=info,
    ).derive(prk)


def hkdf(
    ikm: bytes,
    length: int,
    salt: bytes = b"",
    info: bytes = b"",
    hash_name: str = HASH_SHA256,
) -> bytes:
    """HKDF (RFC 5869 §3): Extract-then-Expand. Uses cryptography's HKDF."""
    return _CryptoHKDF(
        algorithm=_hash_alg(hash_name),
        length=length,
        salt=salt,
        info=info,
    ).derive(ikm)


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
    okm = b""
    counter = 1
    while len(okm) < length:
        i_bytes = counter.to_bytes(counter_width, "big")
        l_bytes = (length * 8).to_bytes(counter_width, "big")
        ki = i_bytes + label + b"\x00" + context + l_bytes
        okm += _stdlib_hmac.new(key, ki, hash_name).digest()
        counter += 1
    return okm[:length]


def pbkdf2(
    password: bytes,
    salt: bytes,
    iterations: int,
    dklen: int,
    hash_name: str = HASH_SHA256,
) -> bytes:
    """PBKDF2-HMAC (RFC 8018 §5.2). Uses cryptography's PBKDF2HMAC.

    Args:
        password: Master password (bytes).
        salt: Cryptographic salt (bytes).
        iterations: Count (>= 1; >= 100_000 recommended for production).
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
        raise HKDFError(f"iterations must be >= 1, got {iterations}")
    if dklen == 0:
        return b""

    return _CryptoPBKDF2(
        algorithm=_hash_alg(hash_name),
        length=dklen,
        salt=salt,
        iterations=iterations,
    ).derive(password)
