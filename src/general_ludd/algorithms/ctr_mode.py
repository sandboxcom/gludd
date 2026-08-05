"""CTR mode block cipher: encrypt, decrypt, keystream generation,
counter increment, and nonce+counter construction.

CTR mode converts a block cipher into a stream cipher by encrypting
successive counter values and XORing the resulting keystream with
plaintext.  The counter block is 16 bytes: 8-byte nonce || 8-byte
big-endian counter.  Encryption and decryption are the same operation.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.primitives.ciphers import algorithms as _algs

_NONCE_BYTES = 8
_COUNTER_BYTES = 8
_BLOCK_BYTES = _NONCE_BYTES + _COUNTER_BYTES
_VALID_KEY_SIZES = frozenset({16, 24, 32})
_COUNTER_MAX = (1 << (_COUNTER_BYTES * 8)) - 1


class CTRModeError(Exception):
    """Base exception for CTR mode operations."""


class CounterOverflowError(CTRModeError):
    """Counter has wrapped past its maximum value."""


def _validate_inputs(key: bytes, nonce: bytes) -> None:
    if len(key) not in _VALID_KEY_SIZES:
        raise CTRModeError(f"Key must be 16, 24, or 32 bytes, got {len(key)}")
    if len(nonce) != _NONCE_BYTES:
        raise CTRModeError(f"Nonce must be {_NONCE_BYTES} bytes, got {len(nonce)}")


def _aes_block_encrypt(key: bytes, block: bytes) -> bytes:
    """Encrypt a single 16-byte block with AES (ECB primitive)."""
    encryptor = Cipher(_algs.AES(key), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


def make_initial_counter_block(
    nonce: bytes,
    counter_start: int = 0,
) -> bytes:
    if len(nonce) != _NONCE_BYTES:
        raise CTRModeError(f"Nonce must be {_NONCE_BYTES} bytes, got {len(nonce)}")
    if counter_start < 0:
        raise CTRModeError(f"Counter start must be >= 0, got {counter_start}")
    return nonce + counter_start.to_bytes(_COUNTER_BYTES, "big")


def increment_counter(counter_block: bytes) -> bytes:
    """Increment the 8-byte counter portion (last 8 bytes) big-endian."""
    nonce = counter_block[:_NONCE_BYTES]
    counter = (int.from_bytes(counter_block[_NONCE_BYTES:], "big") + 1) & _COUNTER_MAX
    return nonce + counter.to_bytes(_COUNTER_BYTES, "big")


def ctr_keystream(
    key: bytes,
    nonce: bytes,
    length: int,
    *,
    initial_counter: int = 0,
) -> bytes:
    _validate_inputs(key, nonce)
    if length == 0:
        return b""

    counter_block = make_initial_counter_block(nonce, initial_counter)
    keystream = bytearray()

    while len(keystream) < length:
        ks_block = _aes_block_encrypt(key, counter_block)
        keystream.extend(ks_block)
        if len(keystream) >= length:
            break
        if counter_block[_NONCE_BYTES:] == b"\xff" * _COUNTER_BYTES:
            raise CounterOverflowError(f"Counter overflow at block starting with counter={initial_counter}")
        counter_block = increment_counter(counter_block)

    return bytes(keystream[:length])


def ctr_encrypt(key: bytes, plaintext: bytes, nonce: bytes) -> bytes:
    ks = ctr_keystream(key, nonce, len(plaintext))
    return bytes(p ^ k for p, k in zip(plaintext, ks, strict=False))


def ctr_decrypt(key: bytes, ciphertext: bytes, nonce: bytes) -> bytes:
    return ctr_encrypt(key, ciphertext, nonce)
