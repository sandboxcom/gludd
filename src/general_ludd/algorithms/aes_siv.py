"""AES-GCM-SIV and AES-SIV authenticated encryption: encrypt, decrypt,
tag verification, nonce generation, and key derivation.

AES-GCM-SIV (RFC 8452) — misuse-resistant nonce-based AEAD.
AES-SIV (RFC 5297)  — deterministic AEAD (no nonce).

Pure-Python wrappers around the cryptography library's AEAD AESGCMSIV and AESSIV.
"""

from __future__ import annotations

import hashlib
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV as _AESGCMSIV
from cryptography.hazmat.primitives.ciphers.aead import AESSIV as _AESSIV


class AESSIVError(Exception):
    """Base exception for AES-SIV / AES-GCM-SIV operations."""


_VALID_KEY_SIZES_GCMSIV = frozenset({16, 32})
_VALID_KEY_SIZES_SIV = frozenset({32, 48, 64})
_GCMSIV_NONCE_BYTES = 12


def generate_nonce_gcm_siv() -> bytes:
    return secrets.token_bytes(_GCMSIV_NONCE_BYTES)


# ---------------------------------------------------------------------------
# AES-GCM-SIV  (RFC 8452)
# ---------------------------------------------------------------------------


def encrypt_gcm_siv(
    key: bytes,
    plaintext: bytes,
    nonce: bytes,
    *,
    associated_data: bytes | None = None,
) -> bytes:
    if len(key) not in _VALID_KEY_SIZES_GCMSIV:
        raise AESSIVError(f"Invalid key size: {len(key)} bytes (must be 16 or 32)")
    if len(nonce) != _GCMSIV_NONCE_BYTES:
        raise AESSIVError(f"Invalid nonce size: {len(nonce)} bytes (must be {_GCMSIV_NONCE_BYTES})")
    aead = _AESGCMSIV(key)
    return aead.encrypt(nonce, plaintext, associated_data)


def decrypt_gcm_siv(
    key: bytes,
    ciphertext: bytes,
    nonce: bytes,
    *,
    associated_data: bytes | None = None,
) -> bytes:
    if len(key) not in _VALID_KEY_SIZES_GCMSIV:
        raise AESSIVError(f"Invalid key size: {len(key)} bytes (must be 16 or 32)")
    if len(nonce) != _GCMSIV_NONCE_BYTES:
        raise AESSIVError(f"Invalid nonce size: {len(nonce)} bytes (must be {_GCMSIV_NONCE_BYTES})")
    aead = _AESGCMSIV(key)
    try:
        return aead.decrypt(nonce, ciphertext, associated_data)
    except Exception as exc:
        raise AESSIVError(f"Decryption failed: {exc}") from exc


# ---------------------------------------------------------------------------
# AES-SIV  (RFC 5297)
# ---------------------------------------------------------------------------


def encrypt_siv(
    key: bytes,
    plaintext: bytes,
    *,
    associated_data: list[bytes] | None = None,
) -> bytes:
    if len(key) not in _VALID_KEY_SIZES_SIV:
        raise AESSIVError(f"Invalid key size: {len(key)} bytes (must be 32, 48, or 64)")
    aead = _AESSIV(key)
    return aead.encrypt(plaintext, associated_data)


def decrypt_siv(
    key: bytes,
    ciphertext: bytes,
    *,
    associated_data: list[bytes] | None = None,
) -> bytes:
    if len(key) not in _VALID_KEY_SIZES_SIV:
        raise AESSIVError(f"Invalid key size: {len(key)} bytes (must be 32, 48, or 64)")
    aead = _AESSIV(key)
    try:
        return aead.decrypt(ciphertext, associated_data)
    except Exception as exc:
        raise AESSIVError(f"Decryption failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def hash_key(password: bytes, salt: bytes) -> bytes:
    derived = hashlib.pbkdf2_hmac("sha256", password, salt, 600_000, dklen=32)
    return derived
