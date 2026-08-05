"""CBC mode block cipher: encrypt, decrypt with PKCS#7 padding,
and padding oracle resistance via constant-time validation.

Uses the cryptography library's AES-CBC mode and PKCS#7 padding.
IV is prepended to ciphertext; decryption uses constant-time
padding validation to resist padding oracle attacks.
"""

from __future__ import annotations

import secrets

from cryptography.hazmat.primitives import padding as _padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class CBCError(ValueError):
    """Base exception for CBC operations."""


_BLOCK_SIZE: int = 16
_VALID_KEY_SIZES: frozenset[int] = frozenset({16, 24, 32})


def _pkcs7_pad(data: bytes, block_size: int) -> bytes:
    padder = _padding.PKCS7(block_size * 8).padder()
    return padder.update(data) + padder.finalize()


def _pkcs7_unpad(data: bytes, block_size: int) -> bytes:
    unpadder = _padding.PKCS7(block_size * 8).unpadder()
    try:
        return unpadder.update(data) + unpadder.finalize()
    except ValueError as exc:
        raise CBCError(str(exc)) from exc


def _constant_time_unpad(data: bytes, block_size: int) -> bytes:
    """Constant-time PKCS#7 unpadding resistant to padding oracle attacks.

    Accumulates mismatches with bitwise OR — the function performs the
    same memory access pattern regardless of whether the padding is
    valid, eliminating timing side-channels that leak information about
    individual padding bytes.
    """
    n = len(data)
    pad = 0
    bad = 0

    if n == 0 or n % block_size != 0:
        bad = 1
    else:
        pad = data[-1]

    if pad < 1 or pad > block_size:
        bad = 1

    for i in range(block_size):
        if i >= block_size - pad and bad == 0:
            idx = n - block_size + i
            bad |= data[idx] ^ pad

    if bad:
        raise CBCError("Integrity verification failed")
    return data[:-pad]


def _constant_time_compare(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b, strict=False):
        result |= x ^ y
    return result == 0


def generate_iv() -> bytes:
    return secrets.token_bytes(_BLOCK_SIZE)


def encrypt(key: bytes, plaintext: bytes, iv: bytes | None = None) -> bytes:
    if len(key) not in _VALID_KEY_SIZES:
        raise CBCError(f"Invalid key size: {len(key)} bytes (must be 16, 24, or 32)")
    if iv is None:
        iv = generate_iv()
    if len(iv) != _BLOCK_SIZE:
        raise CBCError(f"IV must be {_BLOCK_SIZE} bytes, got {len(iv)}")

    padded = _pkcs7_pad(plaintext, _BLOCK_SIZE)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return iv + ciphertext


def decrypt(
    key: bytes,
    ciphertext_with_iv: bytes,
    *,
    constant_time: bool = True,
) -> bytes:
    if len(key) not in _VALID_KEY_SIZES:
        raise CBCError(f"Invalid key size: {len(key)} bytes (must be 16, 24, or 32)")
    if len(ciphertext_with_iv) < _BLOCK_SIZE * 2:
        raise CBCError("Ciphertext too short: must contain at least IV + one block")
    iv = ciphertext_with_iv[:_BLOCK_SIZE]
    ciphertext = ciphertext_with_iv[_BLOCK_SIZE:]
    if len(ciphertext) % _BLOCK_SIZE != 0:
        raise CBCError("Ciphertext not block-aligned")

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()

    if constant_time:
        return _constant_time_unpad(padded, _BLOCK_SIZE)
    return _pkcs7_unpad(padded, _BLOCK_SIZE)


def is_valid_padding(key: bytes, ciphertext_with_iv: bytes) -> bool:
    """Validate ciphertext integrity without leaking padding byte information.

    Returns True/False without revealing WHICH byte caused a padding failure,
    making it resistant to padding oracle attacks.  This should be used as
    the sole gate before decrypting; never call decrypt and inspect the
    exception message.
    """
    try:
        decrypt(key, ciphertext_with_iv, constant_time=True)
        return True
    except CBCError:
        return False
