"""Salsa20 / XSalsa20 stream cipher.

Pure-Python, stdlib only.  Implements quarter-round, the Salsa20 hash (block),
stream encrypt/decrypt, and XSalsa20 (24-byte nonce via HSalsa20).

Reference: "Salsa20 specification" — Daniel J. Bernstein, 2005-03-14
           "Extending the Salsa20 nonce" — Daniel J. Bernstein, 2011-08-29
"""

from __future__ import annotations

import struct

_LEFT_ROTATION: list[int] = [7, 9, 13, 18]
_SIGMA: list[bytes] = [
    b"expand 32-byte k",
    b"expand 16-byte k",
]

_ROUND_COUNT: int = 20


class Salsa20Error(ValueError):
    """Base exception for Salsa20 operations."""


def _bytes_to_words(data: bytes) -> list[int]:
    n = len(data) // 4
    return list(struct.unpack(f"<{n}I", data[: n * 4]))


def _words_to_bytes(words: list[int]) -> bytes:
    return struct.pack(f"<{len(words)}I", *words)


def quarter_round(y: list[int], a: int, b: int, c: int, d: int) -> None:
    """Apply the Salsa20 quarter-round to (y[a], y[b], y[c], y[d]) in-place."""
    y[b] ^= _rotl(y[a] + y[d], 7)
    y[c] ^= _rotl(y[b] + y[a], 9)
    y[d] ^= _rotl(y[c] + y[b], 13)
    y[a] ^= _rotl(y[d] + y[c], 18)


def _rotl(v: int, s: int) -> int:
    return ((v << s) | (v >> (32 - s))) & 0xFFFFFFFF


def _double_round(x: list[int]) -> None:
    qr = quarter_round
    qr(x, 0, 4, 8, 12)
    qr(x, 5, 9, 13, 1)
    qr(x, 10, 14, 2, 6)
    qr(x, 15, 3, 7, 11)
    qr(x, 0, 1, 2, 3)
    qr(x, 5, 6, 7, 4)
    qr(x, 10, 11, 8, 9)
    qr(x, 15, 12, 13, 14)


def _salsa20_state(key: bytes, nonce: bytes, block_counter: int) -> list[int]:
    if len(key) not in (16, 32):
        raise Salsa20Error(f"Key must be 16 or 32 bytes, got {len(key)}")
    if len(nonce) != 8:
        raise Salsa20Error(f"Nonce must be 8 bytes, got {len(nonce)}")

    if len(key) == 32:
        constants = _SIGMA[0]
        k0, k1 = key[:16], key[16:]
    else:
        constants = _SIGMA[1]
        k0, k1 = key, key

    c0, c1, c2, c3 = _bytes_to_words(constants)
    n0, n1 = _bytes_to_words(nonce)
    bc0, bc1 = block_counter & 0xFFFFFFFF, (block_counter >> 32) & 0xFFFFFFFF
    k0_words = _bytes_to_words(k0)
    k1_words = _bytes_to_words(k1)

    return [
        c0,
        k0_words[0],
        k0_words[1],
        k0_words[2],
        k0_words[3],
        c1,
        n0,
        n1,
        bc0,
        bc1,
        c2,
        k1_words[0],
        k1_words[1],
        k1_words[2],
        k1_words[3],
        c3,
    ]


def _salsa20_core(x: list[int], rounds: int = _ROUND_COUNT) -> list[int]:
    state = x[:]
    for _ in range(rounds // 2):
        _double_round(state)
    return [(state[i] + x[i]) & 0xFFFFFFFF for i in range(16)]


def salsa20_block(key: bytes, nonce: bytes, block_counter: int) -> bytes:
    """Generate one 64-byte Salsa20 keystream block."""
    x = _salsa20_state(key, nonce, block_counter)
    out = _salsa20_core(x)
    return _words_to_bytes(out)


def stream_encrypt(
    data: bytes,
    key: bytes,
    nonce: bytes,
    counter: int = 0,
) -> bytes:
    """Encrypt or decrypt *data* with Salsa20 (symmetric XOR with keystream)."""
    result = bytearray()
    block_idx = counter

    for offset in range(0, len(data), 64):
        block = salsa20_block(key, nonce, block_idx)
        chunk = data[offset : offset + 64]
        result.extend(_xor(chunk, block))
        block_idx += 1

    return bytes(result)


stream_decrypt = stream_encrypt


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b[: len(a)], strict=False))


def hsalsa20(key: bytes, nonce: bytes) -> bytes:
    """HSalsa20 / XSalsa20 subkey derivation from 16-byte nonce prefix.

    Runs the Salsa20 core on the standard state but returns the first 4 words
    and the last 4 words of the diagonal (bytes 0-15 and 32-47) as a 32-byte key.
    """
    if len(key) != 32:
        raise Salsa20Error(f"HSalsa20 requires 32-byte key, got {len(key)}")
    if len(nonce) != 16:
        raise Salsa20Error(f"HSalsa20 requires 16-byte nonce prefix, got {len(nonce)}")

    n0, n1, n2, n3 = _bytes_to_words(nonce)
    k0 = _bytes_to_words(key[:16])
    k1 = _bytes_to_words(key[16:])
    c = _bytes_to_words(_SIGMA[0])

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
