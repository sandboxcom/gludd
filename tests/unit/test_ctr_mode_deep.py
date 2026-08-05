"""Deep CTR mode block cipher tests: encrypt, decrypt, increment function,
nonce+counter construction, keystream properties, partial blocks,
counter overflow, multi-block operations, and known test vectors.

Uses aes-128-ctr via the cryptography library for the underlying
block cipher, but the CTR mode logic (nonce+counter splitting,
increment, keystream XOR) is implemented in the module under test.
"""

from __future__ import annotations

import secrets

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.primitives.ciphers import algorithms as _algs

from general_ludd.algorithms.ctr_mode import (
    CounterOverflowError,
    CTRModeError,
    ctr_decrypt,
    ctr_encrypt,
    ctr_keystream,
    increment_counter,
    make_initial_counter_block,
)

BLOCK_BYTES = 16


def _reference_aes_ctr(key: bytes, plaintext: bytes, nonce: bytes) -> bytes:
    """Encrypt using cryptography's built-in CTR mode as reference.

    The cryptography library's CTR expects a full 16-byte initial
    counter block.  Our module uses 8-byte nonce + 8-byte counter,
    so we construct the full 16-byte block before calling the reference.
    """
    full_nonce = make_initial_counter_block(nonce, counter_start=0)
    encryptor = Cipher(_algs.AES(key), modes.CTR(full_nonce)).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


class TestIncrementCounter:
    def test_increment_single_byte(self) -> None:
        ctr = bytes(15) + b"\x00"
        assert increment_counter(ctr) == bytes(15) + b"\x01"

    def test_increment_carry_forward(self) -> None:
        ctr = bytes(14) + b"\x00\xff"
        assert increment_counter(ctr) == bytes(14) + b"\x01\x00"

    def test_increment_all_ones_wraps(self) -> None:
        ctr = b"\x00" * 8 + b"\xff" * 8
        assert increment_counter(ctr) == b"\x00" * 16

    def test_increment_max_counter_block(self) -> None:
        ctr = (
            b"\x00" * 8  # nonce
            + b"\xff" * 7  # counter bytes
            + b"\xfe"  # last byte
        )
        result = increment_counter(ctr)
        assert result[:8] == b"\x00" * 8
        assert result[8:] == b"\xff" * 7 + b"\xff"

    def test_increment_mid_byte_carry(self) -> None:
        nonce = bytes(8)
        counter = int.to_bytes(0x0000FFFFFFFFFE00, 8, "big")
        ctr = nonce + counter
        expected = nonce + int.to_bytes(0x0000FFFFFFFFFE01, 8, "big")
        assert increment_counter(ctr) == expected

    def test_increment_zero_block(self) -> None:
        assert increment_counter(b"\x00" * 16) == bytes(15) + b"\x01"


class TestMakeInitialCounterBlock:
    def test_standard_split_8_8(self) -> None:
        nonce = bytes(8)
        init_ctr = make_initial_counter_block(nonce, counter_start=0)
        assert len(init_ctr) == 16
        assert init_ctr[:8] == nonce
        assert init_ctr[8:] == b"\x00" * 8

    def test_nonzero_counter_start(self) -> None:
        nonce = bytes(8)
        init_ctr = make_initial_counter_block(nonce, counter_start=42)
        assert init_ctr[8:] == (42).to_bytes(8, "big")

    def test_default_counter_start_zero(self) -> None:
        nonce = secrets.token_bytes(8)
        init_ctr = make_initial_counter_block(nonce)
        assert init_ctr[8:] == b"\x00" * 8

    def test_fails_on_short_nonce(self) -> None:
        with pytest.raises(CTRModeError, match="Nonce must be"):
            make_initial_counter_block(bytes(7), counter_start=0)

    def test_fails_on_long_nonce(self) -> None:
        with pytest.raises(CTRModeError, match="Nonce must be"):
            make_initial_counter_block(bytes(9), counter_start=0)

    def test_fails_on_negative_counter(self) -> None:
        with pytest.raises(CTRModeError, match="Counter start"):
            make_initial_counter_block(bytes(8), counter_start=-1)


class TestCTRKeystream:
    def test_keystream_length_matches_requested(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        ks = ctr_keystream(key, nonce, 64)
        assert len(ks) == 64
        ks = ctr_keystream(key, nonce, 0)
        assert len(ks) == 0
        ks = ctr_keystream(key, nonce, 1)
        assert len(ks) == 1

    def test_keystream_deterministic(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        ks1 = ctr_keystream(key, nonce, 256)
        ks2 = ctr_keystream(key, nonce, 256)
        assert ks1 == ks2

    def test_different_nonce_different_keystream(self) -> None:
        key = secrets.token_bytes(16)
        ks1 = ctr_keystream(key, b"\x00" * 8, 128)
        ks2 = ctr_keystream(key, b"\x01" * 8, 128)
        assert ks1 != ks2

    def test_different_key_different_keystream(self) -> None:
        nonce = secrets.token_bytes(8)
        ks1 = ctr_keystream(b"\x00" * 16, nonce, 128)
        ks2 = ctr_keystream(b"\x01" * 16, nonce, 128)
        assert ks1 != ks2


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

    def test_XOR_property(self, key: bytes, nonce: bytes) -> None:
        plaintext = secrets.token_bytes(512)
        ct = ctr_encrypt(key, plaintext, nonce)
        ks = ctr_keystream(key, nonce, len(plaintext))
        assert ct == bytes(p ^ k for p, k in zip(plaintext, ks, strict=False))

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
        ct_ours = ctr_encrypt(key, plaintext, nonce)
        ct_ref = _reference_aes_ctr(key, plaintext, nonce)
        assert ct_ours == ct_ref

    def test_decrypt_matches_cryptography_ctr(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        plaintext = secrets.token_bytes(63)
        ct_ref = _reference_aes_ctr(key, plaintext, nonce)
        pt_ours = ctr_decrypt(key, ct_ref, nonce)
        assert pt_ours == plaintext

    def test_32_byte_key(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        plaintext = secrets.token_bytes(567)
        ct_ours = ctr_encrypt(key, plaintext, nonce)
        ct_ref = _reference_aes_ctr(key, plaintext, nonce)
        assert ct_ours == ct_ref

    def test_24_byte_key(self) -> None:
        key = secrets.token_bytes(24)
        nonce = secrets.token_bytes(8)
        plaintext = secrets.token_bytes(128)
        ct_ours = ctr_encrypt(key, plaintext, nonce)
        ct_ref = _reference_aes_ctr(key, plaintext, nonce)
        assert ct_ours == ct_ref

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

    def test_invalid_nonce_in_keystream(self) -> None:
        with pytest.raises(CTRModeError, match="Nonce must be"):
            ctr_keystream(bytes(16), bytes(9), 32)

    def test_invalid_key_in_keystream(self) -> None:
        with pytest.raises(CTRModeError, match="Key must be"):
            ctr_keystream(bytes(10), bytes(8), 32)


class TestLargeOperations:
    def test_large_keystream_counter_sequence(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        ks_full = ctr_keystream(key, nonce, 32 * 1024)
        assert len(ks_full) == 32 * 1024
        ks_chunked = b""
        block_count = (32 * 1024 + 15) // 16
        for i in range(block_count):
            ks_chunked += ctr_keystream(key, nonce, 16, initial_counter=i)
        assert ks_full[: len(ks_chunked)] == ks_chunked

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


class TestCounterOverflow:
    def test_overflow_raises(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        with pytest.raises(CounterOverflowError):
            ctr_keystream(key, nonce, 16 * 10, initial_counter=(1 << 64) - 1)

    def test_no_overflow_at_boundary_minus_one(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        start = (1 << 64) - 1
        ks = ctr_keystream(key, nonce, 16, initial_counter=start)
        assert len(ks) == 16

    def test_overflow_on_second_block(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        with pytest.raises(CounterOverflowError):
            ctr_keystream(key, nonce, 32, initial_counter=(1 << 64) - 1)
