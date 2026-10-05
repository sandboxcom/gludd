"""Authenticated CBC encryption with PKCS#7 padding.

The versioned frame uses encrypt-then-MAC and verifies its HMAC-SHA256 tag
before CBC decryption.  This makes modified ciphertext fail independently of
whether the modified plaintext happens to contain valid PKCS#7 padding.
"""

from __future__ import annotations

import secrets

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives import padding as _padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class CBCError(ValueError):
    """Base exception for CBC operations."""


_BLOCK_SIZE: int = 16
_FRAME_HEADER: bytes = b"GLCBC\x01"
_TAG_SIZE: int = 32
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


def _derive_keys(key: bytes) -> tuple[bytes, bytes]:
    """Derive domain-separated encryption and authentication keys."""
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=len(key) + _TAG_SIZE,
        salt=_FRAME_HEADER,
        info=b"general-ludd/authenticated-cbc",
    ).derive(key)
    return material[: len(key)], material[len(key) :]


def _authenticate(mac_key: bytes, framed_ciphertext: bytes) -> bytes:
    """Return an HMAC-SHA256 tag for the versioned ciphertext frame."""
    authenticator = hmac.HMAC(mac_key, hashes.SHA256())
    authenticator.update(framed_ciphertext)
    return authenticator.finalize()


def _verify_authentication(mac_key: bytes, framed_ciphertext: bytes, tag: bytes) -> None:
    """Verify an HMAC tag before any attacker-controlled CBC decryption."""
    authenticator = hmac.HMAC(mac_key, hashes.SHA256())
    authenticator.update(framed_ciphertext)
    try:
        authenticator.verify(tag)
    except InvalidSignature as exc:
        raise CBCError("Integrity verification failed") from exc


def generate_iv() -> bytes:
    """Generate a fresh AES block-sized initialization vector."""
    return secrets.token_bytes(_BLOCK_SIZE)


def encrypt(key: bytes, plaintext: bytes, iv: bytes | None = None) -> bytes:
    """Encrypt and authenticate plaintext in the versioned CBC frame."""
    if len(key) not in _VALID_KEY_SIZES:
        raise CBCError(f"Invalid key size: {len(key)} bytes (must be 16, 24, or 32)")
    if iv is None:
        iv = generate_iv()
    if len(iv) != _BLOCK_SIZE:
        raise CBCError(f"IV must be {_BLOCK_SIZE} bytes, got {len(iv)}")

    encryption_key, mac_key = _derive_keys(key)
    padded = _pkcs7_pad(plaintext, _BLOCK_SIZE)
    cipher = Cipher(algorithms.AES(encryption_key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    framed_ciphertext = _FRAME_HEADER + iv + ciphertext
    return framed_ciphertext + _authenticate(mac_key, framed_ciphertext)


def decrypt(
    key: bytes,
    ciphertext_with_iv: bytes,
    *,
    constant_time: bool = True,
) -> bytes:
    """Authenticate and decrypt a versioned CBC frame."""
    if len(key) not in _VALID_KEY_SIZES:
        raise CBCError(f"Invalid key size: {len(key)} bytes (must be 16, 24, or 32)")
    minimum_size = len(_FRAME_HEADER) + (_BLOCK_SIZE * 2) + _TAG_SIZE
    if len(ciphertext_with_iv) < minimum_size:
        raise CBCError("Integrity verification failed")
    if not ciphertext_with_iv.startswith(_FRAME_HEADER):
        raise CBCError("Integrity verification failed")

    framed_ciphertext = ciphertext_with_iv[:-_TAG_SIZE]
    tag = ciphertext_with_iv[-_TAG_SIZE:]
    encryption_key, mac_key = _derive_keys(key)
    _verify_authentication(mac_key, framed_ciphertext, tag)

    iv_start = len(_FRAME_HEADER)
    iv = framed_ciphertext[iv_start : iv_start + _BLOCK_SIZE]
    ciphertext = framed_ciphertext[iv_start + _BLOCK_SIZE :]
    if len(ciphertext) % _BLOCK_SIZE != 0:
        raise CBCError("Integrity verification failed")

    cipher = Cipher(algorithms.AES(encryption_key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()

    if constant_time:
        return _constant_time_unpad(padded, _BLOCK_SIZE)
    return _pkcs7_unpad(padded, _BLOCK_SIZE)


def is_valid_padding(key: bytes, ciphertext_with_iv: bytes) -> bool:
    """Validate authenticated ciphertext without exposing padding information.

    Authentication is checked before CBC decryption, so callers receive only a
    boolean integrity result and never a padding-validity oracle.
    """
    try:
        decrypt(key, ciphertext_with_iv, constant_time=True)
        return True
    except CBCError:
        return False
