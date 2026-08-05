"""CTR mode block cipher tests using cryptography's built-in CTR.

Tests: encrypt, decrypt, validation, multi-block, large operations,
and cross-validation against reference implementation.
"""

from __future__ import annotations

import secrets

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from general_ludd.algorithms.ctr_mode import (
    CTRModeError,
    ctr_decrypt,
    ctr_encrypt,
)


def _ref_ctr(key: bytes, data: bytes, nonce: bytes) -> bytes:
    initial_value = nonce + b"\x00" * 8
    encryptor = Cipher(algorithms.AES(key), modes.CTR(initial_value)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


class TestCTREncryptDecrypt:
    @pytest.fixture
    def key(self) -> bytes:
        return secrets.token_bytes(16)

    @pytest.fixture
    def nonce(self) -> bytes:
        return secrets.token_bytes(8)

    def test_encrypt_decrypt_round_trip(self, key: bytes, nonce: bytes) -> None:
        plaintext = b"Hello, CTR mode block cipher!"
        ct = ctr_encrypt(key, plaintext, nonce)
        pt = ctr_decrypt(key, ct, nonce)
        assert pt == plaintext

    def test_multi_block_round_trip(self, key: bytes, nonce: bytes) -> None:
        plaintext = secrets.token_bytes(1027)
        ct = ctr_encrypt(key, plaintext, nonce)
        pt = ctr_decrypt(key, ct, nonce)
        assert pt == plaintext

    def test_single_byte(self, key: bytes, nonce: bytes) -> None:
        ct = ctr_encrypt(key, b"A", nonce)
        assert len(ct) == 1
        assert ctr_decrypt(key, ct, nonce) == b"A"

    def test_empty_plaintext(self, key: bytes, nonce: bytes) -> None:
        ct = ctr_encrypt(key, b"", nonce)
        assert ct == b""
        assert ctr_decrypt(key, ct, nonce) == b""

    def test_one_full_block(self, key: bytes, nonce: bytes) -> None:
        plaintext = bytes(range(16))
        ct = ctr_encrypt(key, plaintext, nonce)
        assert len(ct) == 16
        assert ctr_decrypt(key, ct, nonce) == plaintext

    def test_encrypt_twice_same_result(self, key: bytes, nonce: bytes) -> None:
        plaintext = b"deterministic stream"
        ct1 = ctr_encrypt(key, plaintext, nonce)
        ct2 = ctr_encrypt(key, plaintext, nonce)
        assert ct1 == ct2

    def test_nonce_reuse_detection(self, key: bytes, nonce: bytes) -> None:
        p1 = b"first message"
        p2 = b"second message"
        ct1 = ctr_encrypt(key, p1, nonce)
        ct2 = ctr_encrypt(key, p2, nonce)
        assert ct1 != ct2
        assert len(ct1) == len(p1)
        assert len(ct2) == len(p2)


class TestCTREncryptAgainstReference:
    def test_encrypt_matches_cryptography_ctr(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        plaintext = secrets.token_bytes(1234)
        assert ctr_encrypt(key, plaintext, nonce) == _ref_ctr(key, plaintext, nonce)

    def test_decrypt_matches_cryptography_ctr(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        plaintext = secrets.token_bytes(63)
        ct_ref = _ref_ctr(key, plaintext, nonce)
        assert ctr_decrypt(key, ct_ref, nonce) == plaintext

    def test_32_byte_key(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        plaintext = secrets.token_bytes(567)
        assert ctr_encrypt(key, plaintext, nonce) == _ref_ctr(key, plaintext, nonce)

    def test_24_byte_key(self) -> None:
        key = secrets.token_bytes(24)
        nonce = secrets.token_bytes(8)
        plaintext = secrets.token_bytes(128)
        assert ctr_encrypt(key, plaintext, nonce) == _ref_ctr(key, plaintext, nonce)

    def test_preserves_length(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        for length in [0, 1, 15, 16, 17, 31, 32, 100, 1024]:
            plaintext = secrets.token_bytes(length)
            ct = ctr_encrypt(key, plaintext, nonce)
            assert len(ct) == length


class TestCTRValidation:
    def test_invalid_key_size(self) -> None:
        with pytest.raises(CTRModeError, match="Key must be"):
            ctr_encrypt(bytes(20), b"data", bytes(8))

    def test_invalid_nonce_size(self) -> None:
        with pytest.raises(CTRModeError, match="Nonce must be"):
            ctr_encrypt(bytes(16), b"data", bytes(6))


class TestLargeOperations:
    def test_megabyte_round_trip(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        plaintext = secrets.token_bytes(1_048_576)
        ct = ctr_encrypt(key, plaintext, nonce)
        pt = ctr_decrypt(key, ct, nonce)
        assert pt == plaintext

    def test_counter_block_alignment(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        for length in [15, 16, 17, 31, 32, 33, 47, 48, 49]:
            plaintext = bytes(length)
            ct = ctr_encrypt(key, plaintext, nonce)
            pt = ctr_decrypt(key, ct, nonce)
            assert pt == plaintext
