"""ChaCha20-Poly1305 AEAD encrypt/decrypt via the `cryptography` library."""

from __future__ import annotations

import secrets

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305 as _CryptographyChaCha20Poly1305


class ChaCha20Poly1305Error(Exception):
    """Base exception for ChaCha20-Poly1305 operations."""


def chacha20_aead_encrypt(
    key: bytes,
    nonce: bytes,
    plaintext: bytes,
    *,
    associated_data: bytes | None = None,
) -> bytes:
    """ChaCha20-Poly1305 AEAD encrypt (RFC 8439).  Returns ciphertext || tag."""
    if len(key) != 32:
        raise ChaCha20Poly1305Error(f"Key must be 32 bytes, got {len(key)}")
    if len(nonce) != 12:
        raise ChaCha20Poly1305Error(f"Nonce must be 12 bytes, got {len(nonce)}")
    aead = _CryptographyChaCha20Poly1305(key)
    return aead.encrypt(nonce, plaintext, associated_data)


def chacha20_aead_decrypt(
    key: bytes,
    nonce: bytes,
    ciphertext_tag: bytes,
    *,
    associated_data: bytes | None = None,
) -> bytes:
    """ChaCha20-Poly1305 AEAD decrypt (RFC 8439).  Raises on tag mismatch."""
    if len(key) != 32:
        raise ChaCha20Poly1305Error(f"Key must be 32 bytes, got {len(key)}")
    if len(nonce) != 12:
        raise ChaCha20Poly1305Error(f"Nonce must be 12 bytes, got {len(nonce)}")
    if len(ciphertext_tag) < 16:
        raise ChaCha20Poly1305Error(f"Ciphertext too short for tag: {len(ciphertext_tag)} bytes")
    aead = _CryptographyChaCha20Poly1305(key)
    try:
        return aead.decrypt(nonce, ciphertext_tag, associated_data)
    except Exception:
        raise ChaCha20Poly1305Error("Authentication failed: tag mismatch") from None


def generate_nonce() -> bytes:
    return secrets.token_bytes(12)


def generate_key() -> bytes:
    return secrets.token_bytes(32)
