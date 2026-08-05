"""Streaming AEAD via the `cryptography` library.

Stateful STREAM encryptor / decryptor using ChaCha20-Poly1305 (RFC 8439).
Each chunk is individually tagged; chunk ordering, truncation, and finality
are authenticated through per-chunk associated data.
"""

from __future__ import annotations

import secrets

from cryptography.hazmat.primitives.ciphers.aead import (
    ChaCha20Poly1305 as _ChaCha20Poly1305,
)

_KEY_LEN: int = 32
_NONCE_LEN: int = 12
_TAG_LEN: int = 16  # Poly1305 authentication tag


class StreamAEADError(Exception):
    """Raised on authentication failure, misuse, or protocol violation."""


def _build_ad(
    associated_data: bytes | None,
    chunk_idx: int,
    is_final: bool,
) -> bytes:
    ad = b""
    if associated_data is not None:
        ad += associated_data + b"\x00"
    ad += chunk_idx.to_bytes(4, "big")
    ad += b"\x01" if is_final else b"\x00"
    return ad


def _nonce_for_chunk(master_nonce: bytes, chunk_idx: int) -> bytes:
    idx_bytes = chunk_idx.to_bytes(_NONCE_LEN, "big")
    return bytes(a ^ b for a, b in zip(master_nonce, idx_bytes, strict=False))


class StreamAEADEncryptor:
    """Stateful streaming AEAD encryptor.

    Encrypts plaintext chunks one at a time.  Each chunk's ciphertext
    includes a 16-byte Poly1305 tag.  The caller signals the final chunk
    by passing ``is_final=True``; afterward the encryptor is exhausted
    and further calls raise ``StreamAEADError``.
    """

    def __init__(
        self,
        key: bytes,
        nonce: bytes,
        *,
        associated_data: bytes | None = None,
    ) -> None:
        if len(key) != _KEY_LEN:
            raise StreamAEADError(f"Key must be {_KEY_LEN} bytes, got {len(key)}")
        if len(nonce) != _NONCE_LEN:
            raise StreamAEADError(f"Nonce must be {_NONCE_LEN} bytes, got {len(nonce)}")
        self._aead = _ChaCha20Poly1305(key)
        self._master_nonce = nonce
        self._associated_data = associated_data
        self._chunk_idx: int = 0
        self._finished: bool = False

    def encrypt(self, plaintext: bytes, *, is_final: bool = False) -> bytes:
        if self._finished:
            raise StreamAEADError("Encryptor already finalized")
        ad = _build_ad(self._associated_data, self._chunk_idx, is_final)
        chunk_nonce = _nonce_for_chunk(self._master_nonce, self._chunk_idx)
        result = self._aead.encrypt(chunk_nonce, plaintext, ad)
        self._chunk_idx += 1
        if is_final:
            self._finished = True
        return result


class StreamAEADDecryptor:
    """Stateful streaming AEAD decryptor.

    Decrypts ciphertext chunks one at a time.  Each chunk is authenticated
    before the plaintext is released.  The caller signals the final chunk
    by passing ``is_final=True``; afterward the decryptor is exhausted
    and further calls (including a missing final marker) raise
    ``StreamAEADError``.
    """

    def __init__(
        self,
        key: bytes,
        nonce: bytes,
        *,
        associated_data: bytes | None = None,
    ) -> None:
        if len(key) != _KEY_LEN:
            raise StreamAEADError(f"Key must be {_KEY_LEN} bytes, got {len(key)}")
        if len(nonce) != _NONCE_LEN:
            raise StreamAEADError(f"Nonce must be {_NONCE_LEN} bytes, got {len(nonce)}")
        self._aead = _ChaCha20Poly1305(key)
        self._master_nonce = nonce
        self._associated_data = associated_data
        self._chunk_idx: int = 0
        self._finished: bool = False

    def decrypt(self, ciphertext: bytes, *, is_final: bool = False) -> bytes:
        if self._finished:
            raise StreamAEADError("Decryptor already finalized")
        if len(ciphertext) < _TAG_LEN:
            raise StreamAEADError(f"Ciphertext too short for tag: {len(ciphertext)} bytes")
        ad = _build_ad(self._associated_data, self._chunk_idx, is_final)
        chunk_nonce = _nonce_for_chunk(self._master_nonce, self._chunk_idx)
        try:
            plaintext = self._aead.decrypt(chunk_nonce, ciphertext, ad)
        except Exception:
            raise StreamAEADError(f"Chunk {self._chunk_idx}: authentication failed") from None
        self._chunk_idx += 1
        if is_final:
            self._finished = True
        return plaintext


def generate_key() -> bytes:
    return secrets.token_bytes(_KEY_LEN)


def generate_nonce() -> bytes:
    return secrets.token_bytes(_NONCE_LEN)
