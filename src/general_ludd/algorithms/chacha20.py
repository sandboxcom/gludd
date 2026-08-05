"""ChaCha20 stream cipher, Poly1305 MAC, and AEAD encrypt/decrypt.

Pure-Python, stdlib only.  Implements RFC 8439 (ChaCha20-Poly1305).
"""

from __future__ import annotations

import secrets
import struct


class ChaCha20Poly1305Error(Exception):
    """Base exception for ChaCha20-Poly1305 operations."""


def _rotl32(v: int, c: int) -> int:
    return ((v << c) | (v >> (32 - c))) & 0xFFFFFFFF


def quarter_round(a: int, b: int, c: int, d: int) -> tuple[int, int, int, int]:
    a = (a + b) & 0xFFFFFFFF
    d = _rotl32(d ^ a, 16)
    c = (c + d) & 0xFFFFFFFF
    b = _rotl32(b ^ c, 12)
    a = (a + b) & 0xFFFFFFFF
    d = _rotl32(d ^ a, 8)
    c = (c + d) & 0xFFFFFFFF
    b = _rotl32(b ^ c, 7)
    return a, b, c, d


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    """Produce a single 64-byte ChaCha20 block (RFC 8439, 32-bit counter)."""
    if len(key) != 32:
        raise ChaCha20Poly1305Error(f"Key must be 32 bytes, got {len(key)}")
    if len(nonce) != 12:
        raise ChaCha20Poly1305Error(f"Nonce must be 12 bytes, got {len(nonce)}")

    ctr = counter & 0xFFFFFFFF
    n0, n1, n2 = struct.unpack("<III", nonce)
    k = list(struct.unpack("<IIIIIIII", key))

    state = [
        0x61707865,
        0x3320646E,
        0x79622D32,
        0x6B206574,  # "expand 32-byte k"
        k[0],
        k[1],
        k[2],
        k[3],
        k[4],
        k[5],
        k[6],
        k[7],
        ctr,
        n0,
        n1,
        n2,
    ]
    working = list(state)
    for _ in range(10):
        working[0], working[4], working[8], working[12] = quarter_round(working[0], working[4], working[8], working[12])
        working[1], working[5], working[9], working[13] = quarter_round(working[1], working[5], working[9], working[13])
        working[2], working[6], working[10], working[14] = quarter_round(
            working[2], working[6], working[10], working[14]
        )
        working[3], working[7], working[11], working[15] = quarter_round(
            working[3], working[7], working[11], working[15]
        )
        working[0], working[5], working[10], working[15] = quarter_round(
            working[0], working[5], working[10], working[15]
        )
        working[1], working[6], working[11], working[12] = quarter_round(
            working[1], working[6], working[11], working[12]
        )
        working[2], working[7], working[8], working[13] = quarter_round(working[2], working[7], working[8], working[13])
        working[3], working[4], working[9], working[14] = quarter_round(working[3], working[4], working[9], working[14])

    out = bytearray(64)
    for i in range(16):
        val = (working[i] + state[i]) & 0xFFFFFFFF
        struct.pack_into("<I", out, i * 4, val)
    return bytes(out)


def chacha20_stream(key: bytes, counter: int, nonce: bytes, length: int) -> bytes:
    """Generate `length` bytes of ChaCha20 keystream starting at `counter`."""
    result = bytearray()
    block_count = (length + 63) // 64
    for i in range(block_count):
        block = _chacha20_block(key, counter + i, nonce)
        result.extend(block)
    return bytes(result[:length])


def chacha20_encrypt(key: bytes, counter: int, nonce: bytes, plaintext: bytes) -> bytes:
    """XOR `plaintext` with ChaCha20 keystream."""
    if len(plaintext) == 0:
        return b""
    keystream = chacha20_stream(key, counter, nonce, len(plaintext))
    return bytes(a ^ b for a, b in zip(plaintext, keystream, strict=False))


chacha20_decrypt = chacha20_encrypt


def _clamp(r: bytes) -> int:
    return int.from_bytes(r, "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF


def _poly1305_pad(n: int) -> bytes:
    return b"\x00" * ((16 - (n % 16)) % 16)


def poly1305_mac(message: bytes, key: bytes) -> bytes:
    """Compute the Poly1305 MAC of `message` under 32-byte `key`."""
    if len(key) != 32:
        raise ChaCha20Poly1305Error(f"Poly1305 key must be 32 bytes, got {len(key)}")

    r = _clamp(key[:16])
    s = int.from_bytes(key[16:], "little")

    accumulator = 0
    for i in range(0, len(message), 16):
        chunk = message[i : i + 16]
        n = int.from_bytes(chunk + b"\x01", "little")
        accumulator = (accumulator + n) % (2**130 - 5)
        accumulator = (accumulator * r) % (2**130 - 5)

    accumulator = (accumulator + s) & ((1 << 128) - 1)
    return accumulator.to_bytes(16, "little")


def _poly1305_key_gen(key: bytes, nonce: bytes) -> bytes:
    block = _chacha20_block(key, 0, nonce)
    return block[:32]


def _pad16(data: bytes) -> bytes:
    return _poly1305_pad(len(data))


def _aead_mac_input(ad: bytes, ciphertext: bytes) -> bytes:
    return ad + _pad16(ad) + ciphertext + _pad16(ciphertext) + struct.pack("<QQ", len(ad), len(ciphertext))


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

    ad = associated_data or b""
    otk = _poly1305_key_gen(key, nonce)

    ciphertext = chacha20_encrypt(key, 1, nonce, plaintext)

    mac_input = _aead_mac_input(ad, ciphertext)
    tag = poly1305_mac(mac_input, otk)

    return ciphertext + tag


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

    ad = associated_data or b""
    ciphertext = ciphertext_tag[:-16]
    received_tag = ciphertext_tag[-16:]

    otk = _poly1305_key_gen(key, nonce)
    mac_input = _aead_mac_input(ad, ciphertext)
    expected_tag = poly1305_mac(mac_input, otk)

    if not _constant_time_compare(expected_tag, received_tag):
        raise ChaCha20Poly1305Error("Authentication failed: tag mismatch")

    return chacha20_encrypt(key, 1, nonce, ciphertext)


def _constant_time_compare(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b, strict=False):
        result |= x ^ y
    return result == 0


def generate_nonce() -> bytes:
    return secrets.token_bytes(12)


def generate_key() -> bytes:
    return secrets.token_bytes(32)
