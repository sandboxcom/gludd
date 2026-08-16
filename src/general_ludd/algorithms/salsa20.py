"""Salsa20 / XSalsa20 stream cipher — backed by PyCryptodome.

Stream encrypt/decrypt and block generation delegate to
``Crypto.Cipher.Salsa20`` from the MAINTAINED PyCryptodome distribution
(bandit's B413 blacklists the same legacy ``Crypto`` namespace used by the
unmaintained pycrypto; the SAST allowlist documents that this import is the
maintained fork). HSalsa20 subkey derivation is kept locally since
PyCryptodome does not expose the raw Salsa20 core.

Reference: "Salsa20 specification" — Daniel J. Bernstein, 2005-03-14
           "Extending the Salsa20 nonce" — Daniel J. Bernstein, 2011-08-29
"""

from __future__ import annotations

import struct
from typing import Protocol

from Crypto.Cipher import Salsa20 as _PyCryptodomeSalsa20

_SIGMA_32: bytes = b"expand 32-byte k"


class Salsa20Error(ValueError):
    """Base exception for Salsa20 operations."""


# ---------------------------------------------------------------------------
# HSalsa20 helpers (kept locally — PyCryptodome does not expose the raw core)
# ---------------------------------------------------------------------------


def _bytes_to_words(data: bytes) -> list[int]:
    n = len(data) // 4
    return list(struct.unpack(f"<{n}I", data[: n * 4]))


def _words_to_bytes(words: list[int]) -> bytes:
    return struct.pack(f"<{len(words)}I", *words)


def _rotl(v: int, s: int) -> int:
    return ((v << s) | (v >> (32 - s))) & 0xFFFFFFFF


def _quarter_round(y: list[int], a: int, b: int, c: int, d: int) -> None:
    y[b] ^= _rotl(y[a] + y[d], 7)
    y[c] ^= _rotl(y[b] + y[a], 9)
    y[d] ^= _rotl(y[c] + y[b], 13)
    y[a] ^= _rotl(y[d] + y[c], 18)


def _double_round(x: list[int]) -> None:
    _quarter_round(x, 0, 4, 8, 12)
    _quarter_round(x, 5, 9, 13, 1)
    _quarter_round(x, 10, 14, 2, 6)
    _quarter_round(x, 15, 3, 7, 11)
    _quarter_round(x, 0, 1, 2, 3)
    _quarter_round(x, 5, 6, 7, 4)
    _quarter_round(x, 10, 11, 8, 9)
    _quarter_round(x, 15, 12, 13, 14)


def _salsa20_core(x: list[int], rounds: int = 20) -> list[int]:
    state = x[:]
    for _ in range(rounds // 2):
        _double_round(state)
    return [(state[i] + x[i]) & 0xFFFFFFFF for i in range(16)]


# ---------------------------------------------------------------------------
# Salsa20 stream cipher — PyCryptodome
# ---------------------------------------------------------------------------

_ADVANCE_CHUNK = b"\x00" * (64 * 1024)


class _Salsa20Cipher(Protocol):
    def encrypt(self, plaintext: bytes) -> bytes: ...


def _advance_cipher(cipher: _Salsa20Cipher, block_counter: int) -> None:
    if block_counter < 0:
        raise Salsa20Error("Block counter must be non-negative")
    remaining = block_counter * 64
    while remaining:
        chunk_size = min(remaining, len(_ADVANCE_CHUNK))
        cipher.encrypt(_ADVANCE_CHUNK[:chunk_size])
        remaining -= chunk_size


def salsa20_block(key: bytes, nonce: bytes, block_counter: int) -> bytes:
    """Generate one 64-byte Salsa20 keystream block."""
    if len(key) not in (16, 32):
        raise Salsa20Error(f"Key must be 16 or 32 bytes, got {len(key)}")
    if len(nonce) != 8:
        raise Salsa20Error(f"Nonce must be 8 bytes, got {len(nonce)}")

    cipher = _PyCryptodomeSalsa20.new(key=key, nonce=nonce)
    _advance_cipher(cipher, block_counter)
    return cipher.encrypt(b"\x00" * 64)


def stream_encrypt(
    data: bytes,
    key: bytes,
    nonce: bytes,
    counter: int = 0,
) -> bytes:
    """Encrypt or decrypt *data* with Salsa20 (symmetric XOR with keystream)."""
    cipher = _PyCryptodomeSalsa20.new(key=key, nonce=nonce)
    _advance_cipher(cipher, counter)
    return cipher.encrypt(data)


stream_decrypt = stream_encrypt


# ---------------------------------------------------------------------------
# HSalsa20 / XSalsa20
# ---------------------------------------------------------------------------


def hsalsa20(key: bytes, nonce: bytes) -> bytes:
    """HSalsa20 / XSalsa20 subkey derivation from 16-byte nonce prefix.

    Runs the Salsa20 core on the standard state but returns a 32-byte subkey
    from the diagonal words.
    """
    if len(key) != 32:
        raise Salsa20Error(f"HSalsa20 requires 32-byte key, got {len(key)}")
    if len(nonce) != 16:
        raise Salsa20Error(f"HSalsa20 requires 16-byte nonce prefix, got {len(nonce)}")

    n0, n1, n2, n3 = _bytes_to_words(nonce)
    k0 = _bytes_to_words(key[:16])
    k1 = _bytes_to_words(key[16:])
    c = _bytes_to_words(_SIGMA_32)

    state = [
        c[0],
        k0[0],
        k0[1],
        k0[2],
        k0[3],
        c[1],
        n0,
        n1,
        n2,
        n3,
        c[2],
        k1[0],
        k1[1],
        k1[2],
        k1[3],
        c[3],
    ]

    out = _salsa20_core(state)
    return _words_to_bytes([out[0], out[5], out[10], out[15], out[6], out[7], out[8], out[9]])


def xsalsa20_encrypt(
    data: bytes,
    key: bytes,
    nonce: bytes,
    counter: int = 0,
) -> bytes:
    """Encrypt or decrypt *data* with XSalsa20 (24-byte nonce)."""
    if len(key) != 32:
        raise Salsa20Error(f"XSalsa20 requires 32-byte key, got {len(key)}")
    if len(nonce) != 24:
        raise Salsa20Error(f"XSalsa20 requires 24-byte nonce, got {len(nonce)}")

    subkey = hsalsa20(key, nonce[:16])
    return stream_encrypt(data, subkey, nonce[16:], counter)


xsalsa20_decrypt = xsalsa20_encrypt
