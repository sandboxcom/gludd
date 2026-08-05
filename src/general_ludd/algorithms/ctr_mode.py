"""CTR mode block cipher using the cryptography library's built-in
CTR implementation.

CTR mode converts a block cipher into a stream cipher by encrypting
successive counter values and XORing the resulting keystream with
plaintext.  Uses an 8-byte nonce padded to 16 bytes for the initial
counter block.  Encryption and decryption are the same operation.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_NONCE_BYTES = 8
_BLOCK_BYTES = 16
_VALID_KEY_SIZES = frozenset({16, 24, 32})


class CTRModeError(Exception):
    """Base exception for CTR mode operations."""


def _validate_inputs(key: bytes, nonce: bytes) -> None:
    if len(key) not in _VALID_KEY_SIZES:
        raise CTRModeError(f"Key must be 16, 24, or 32 bytes, got {len(key)}")
    if len(nonce) != _NONCE_BYTES:
        raise CTRModeError(f"Nonce must be {_NONCE_BYTES} bytes, got {len(nonce)}")


def ctr_encrypt(key: bytes, plaintext: bytes, nonce: bytes) -> bytes:
    _validate_inputs(key, nonce)
    initial_value = nonce + b"\x00" * (_BLOCK_BYTES - _NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(key), modes.CTR(initial_value)).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def ctr_decrypt(key: bytes, ciphertext: bytes, nonce: bytes) -> bytes:
    return ctr_encrypt(key, ciphertext, nonce)
