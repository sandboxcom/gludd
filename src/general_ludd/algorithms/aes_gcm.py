"""AES-GCM authenticated encryption: encrypt, decrypt, tag verification,
nonce generation, and key derivation.

Pure-Python wrapper around the cryptography library's AEAD AESGCM.
"""

from __future__ import annotations

import hashlib
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM


class AESGCMError(Exception):
    """Base exception for AES-GCM operations."""


_VALID_KEY_SIZES = frozenset({16, 24, 32})
_NONCE_BYTES = 12


def generate_nonce() -> bytes:
    return secrets.token_bytes(_NONCE_BYTES)


def encrypt(
    key: bytes,
    plaintext: bytes,
    nonce: bytes,
    *,
    associated_data: bytes | None = None,
) -> bytes:
    if len(key) not in _VALID_KEY_SIZES:
        raise AESGCMError(f"Invalid key size: {len(key)} bytes (must be 16, 24, or 32)")
    if len(nonce) != _NONCE_BYTES:
        raise AESGCMError(f"Invalid nonce size: {len(nonce)} bytes (must be {_NONCE_BYTES})")
    aead = _AESGCM(key)
    return aead.encrypt(nonce, plaintext, associated_data)


def decrypt(
    key: bytes,
    ciphertext: bytes,
    nonce: bytes,
    *,
    associated_data: bytes | None = None,
) -> bytes:
    if len(key) not in _VALID_KEY_SIZES:
        raise AESGCMError(f"Invalid key size: {len(key)} bytes (must be 16, 24, or 32)")
    if len(nonce) != _NONCE_BYTES:
        raise AESGCMError(f"Invalid nonce size: {len(nonce)} bytes (must be {_NONCE_BYTES})")
    aead = _AESGCM(key)
    try:
        return aead.decrypt(nonce, ciphertext, associated_data)
    except Exception as exc:
        raise AESGCMError(f"Decryption failed: {exc}") from exc


def hash_key(password: bytes, salt: bytes) -> bytes:
    derived = hashlib.pbkdf2_hmac("sha256", password, salt, 600_000, dklen=32)
    return derived
