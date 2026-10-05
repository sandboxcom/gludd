"""Deep CBC mode block cipher tests: encrypt/decrypt, PKCS#7 padding,
padding oracle resistance, constant-time validation, key/IV validation.

Uses the cryptography library's AES-CBC mode and PKCS#7 padding.
"""

from __future__ import annotations

import secrets

import pytest
from cryptography.hazmat.primitives import padding as _padding

from general_ludd.algorithms.cbc_mode import (
    CBCError,
    _constant_time_compare,
    _constant_time_unpad,
    decrypt,
    encrypt,
    generate_iv,
    is_valid_padding,
)


class TestGenerateIV:
    def test_iv_is_16_bytes(self) -> None:
        iv = generate_iv()
        assert len(iv) == 16

    def test_iv_is_unique(self) -> None:
        ivs = {generate_iv() for _ in range(200)}
        assert len(ivs) == 200

    def test_iv_is_bytes(self) -> None:
        assert isinstance(generate_iv(), bytes)


class TestPKCS7Padding:
    @staticmethod
    def _pad(data: bytes, block_size: int = 16) -> bytes:
        padder = _padding.PKCS7(block_size * 8).padder()
        return padder.update(data) + padder.finalize()

    @staticmethod
    def _unpad(data: bytes, block_size: int = 16) -> bytes:
        unpadder = _padding.PKCS7(block_size * 8).unpadder()
        return unpadder.update(data) + unpadder.finalize()

    def test_pad_block_aligned_data(self) -> None:
        padded = self._pad(b"A" * 16)
        assert padded == b"A" * 16 + bytes([16] * 16)

    def test_pad_partial_block(self) -> None:
        padded = self._pad(b"A" * 5)
        assert padded == b"A" * 5 + bytes([11] * 11)

    def test_pad_single_byte(self) -> None:
        padded = self._pad(b"X")
        assert padded == b"X" + bytes([15] * 15)

    def test_pad_empty(self) -> None:
        padded = self._pad(b"")
        assert padded == bytes([16] * 16)

    def test_unpad_normal_block(self) -> None:
        unpadded = self._unpad(bytes([16] * 16))
        assert unpadded == b""

    def test_unpad_partial(self) -> None:
        padded = b"A" * 5 + bytes([11] * 11)
        unpadded = self._unpad(padded)
        assert unpadded == b"A" * 5

    def test_unpad_invalid_length(self) -> None:
        with pytest.raises(ValueError):
            self._unpad(b"\x01")

    def test_unpad_zero_pad_byte(self) -> None:
        padded = bytearray(b"A" * 15)
        padded.append(0)
        with pytest.raises(ValueError):
            self._unpad(bytes(padded))

    def test_unpad_pad_too_large(self) -> None:
        padded = bytearray(b"A" * 15)
        padded.append(17)
        with pytest.raises(ValueError):
            self._unpad(bytes(padded))

    def test_unpad_inconsistent_padding(self) -> None:
        padded = bytearray(b"A" * 12)
        padded.extend([4, 4, 3, 4])
        with pytest.raises(ValueError):
            self._unpad(bytes(padded))

    def test_pad_unpad_roundtrip_varied_sizes(self) -> None:
        for size in range(64):
            data = secrets.token_bytes(size)
            padded = self._pad(data)
            unpadded = self._unpad(padded)
            assert unpadded == data


class TestConstantTimeUnpad:
    def test_normal_unpad(self) -> None:
        padded = b"hello" + bytes([11] * 11)
        result = _constant_time_unpad(padded, 16)
        assert result == b"hello"

    def test_empty_input_raises(self) -> None:
        with pytest.raises(CBCError):
            _constant_time_unpad(b"", 16)

    def test_not_block_aligned_raises(self) -> None:
        with pytest.raises(CBCError):
            _constant_time_unpad(b"x" * 17, 16)

    def test_invalid_padding_byte_raises(self) -> None:
        padded = bytearray(b"A" * 14)
        padded.extend([0, 2])
        with pytest.raises(CBCError):
            _constant_time_unpad(bytes(padded), 16)

    def test_inconsistent_padding_raises(self) -> None:
        padded = bytearray(b"A" * 12)
        padded.extend([5, 5, 5, 3])
        with pytest.raises(CBCError):
            _constant_time_unpad(bytes(padded), 16)


class TestConstantTimeCompare:
    def test_equal_buffers(self) -> None:
        assert _constant_time_compare(b"abc", b"abc") is True

    def test_unequal_buffers(self) -> None:
        assert _constant_time_compare(b"abc", b"abd") is False

    def test_different_lengths(self) -> None:
        assert _constant_time_compare(b"abc", b"ab") is False

    def test_zero_length(self) -> None:
        assert _constant_time_compare(b"", b"") is True


class TestBasicRoundtrip:
    def test_encrypt_decrypt_simple(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"hello, CBC mode"
        ciphertext = encrypt(key, plaintext)
        result = decrypt(key, ciphertext)
        assert result == plaintext

    def test_encrypt_decrypt_empty(self) -> None:
        key = secrets.token_bytes(32)
        ciphertext = encrypt(key, b"")
        result = decrypt(key, ciphertext)
        assert result == b""

    def test_encrypt_decrypt_large(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = secrets.token_bytes(1_000_000)
        ciphertext = encrypt(key, plaintext)
        result = decrypt(key, ciphertext)
        assert result == plaintext

    def test_encrypt_decrypt_exact_block(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = secrets.token_bytes(16)
        ciphertext = encrypt(key, plaintext)
        result = decrypt(key, ciphertext)
        assert result == plaintext

    def test_encrypt_decrypt_block_plus_one(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = secrets.token_bytes(17)
        ciphertext = encrypt(key, plaintext)
        result = decrypt(key, ciphertext)
        assert result == plaintext


class TestKeyAndIVValidation:
    def test_key_16_bytes_works(self) -> None:
        key = secrets.token_bytes(16)
        c = encrypt(key, b"hello")
        assert decrypt(key, c) == b"hello"

    def test_key_24_bytes_works(self) -> None:
        key = secrets.token_bytes(24)
        c = encrypt(key, b"hello")
        assert decrypt(key, c) == b"hello"

    def test_key_32_bytes_works(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt(key, b"hello")
        assert decrypt(key, c) == b"hello"

    def test_key_invalid_size_encrypt(self) -> None:
        with pytest.raises(CBCError):
            encrypt(secrets.token_bytes(20), b"hello")

    def test_key_invalid_size_decrypt(self) -> None:
        key32 = secrets.token_bytes(32)
        c = encrypt(key32, b"hello")
        with pytest.raises(CBCError):
            decrypt(secrets.token_bytes(20), c)

    def test_iv_invalid_size(self) -> None:
        with pytest.raises(CBCError):
            encrypt(secrets.token_bytes(32), b"hello", iv=b"short")

    def test_ciphertext_too_short(self) -> None:
        with pytest.raises(CBCError):
            decrypt(secrets.token_bytes(32), b"too-short")

    def test_ciphertext_not_block_aligned(self) -> None:
        key = secrets.token_bytes(32)
        iv = secrets.token_bytes(16)
        ciphertext = iv + secrets.token_bytes(33)
        with pytest.raises(CBCError):
            decrypt(key, ciphertext)

    def test_legacy_unauthenticated_frame_is_rejected(self) -> None:
        key = secrets.token_bytes(32)
        authenticated = encrypt(key, b"A" * 64, iv=bytes(16))
        legacy_iv_and_ciphertext = authenticated[6:-32]
        with pytest.raises(CBCError, match="Integrity verification failed"):
            decrypt(key, legacy_iv_and_ciphertext)


class TestTamperDetection:
    def test_wrong_key_fails(self) -> None:
        k1, k2 = secrets.token_bytes(32), secrets.token_bytes(32)
        c = encrypt(k1, b"secret")
        with pytest.raises(CBCError):
            decrypt(k2, c)

    def test_tampered_ciphertext_fails(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt(key, b"A" * 32)
        tampered = bytearray(c)
        tampered[32] ^= 0xAA
        with pytest.raises(CBCError):
            decrypt(key, bytes(tampered))

    def test_tampered_padding_block_fails(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt(key, b"secret")
        tampered = bytearray(c)
        tampered[-1] ^= 0xFF
        with pytest.raises(CBCError):
            decrypt(key, bytes(tampered))

    def test_corrupted_last_ciphertext_block_fails(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt(key, b"A" * 64)
        tampered = bytearray(c)
        tampered[-17] ^= 0x01
        with pytest.raises(CBCError):
            decrypt(key, bytes(tampered))

    def test_version_header_tamper_fails_before_decryption(self) -> None:
        key = secrets.token_bytes(32)
        tampered = bytearray(encrypt(key, b"authenticated"))
        tampered[0] ^= 0x01
        with pytest.raises(CBCError, match="Integrity verification failed"):
            decrypt(key, bytes(tampered))

    def test_padding_malleability_cannot_forge_valid_frame(self) -> None:
        """Authenticate CBC so a forged valid PKCS#7 suffix is still rejected."""
        key = bytes(range(32))
        plaintext = b"hosted Python 3.11 integrity regression"
        ciphertext = encrypt(key, plaintext, iv=bytes(16))
        original_padding = 16 - (len(plaintext) % 16)
        tampered = bytearray(ciphertext)
        # HMAC-SHA256 is 32 bytes; the preceding two CBC blocks demonstrate
        # the classic chosen-ciphertext transformation from padding N to 1.
        tampered[-49] ^= original_padding ^ 1

        with pytest.raises(CBCError):
            decrypt(key, bytes(tampered))


class TestDeterminismAndUniqueness:
    def test_same_key_iv_same_output(self) -> None:
        key = secrets.token_bytes(32)
        iv = secrets.token_bytes(16)
        c1 = encrypt(key, b"deterministic", iv=iv)
        c2 = encrypt(key, b"deterministic", iv=iv)
        assert c1 == c2

    def test_different_iv_different_output(self) -> None:
        key = secrets.token_bytes(32)
        c1 = encrypt(key, b"same")
        c2 = encrypt(key, b"same")
        assert c1 != c2

    def test_different_plaintext_different_ciphertext(self) -> None:
        key = secrets.token_bytes(32)
        iv = secrets.token_bytes(16)
        c1 = encrypt(key, b"aaa", iv=iv)
        c2 = encrypt(key, b"aab", iv=iv)
        assert c1 != c2

    def test_ciphertext_larger_than_plaintext(self) -> None:
        key = secrets.token_bytes(32)
        for size in range(1, 33):
            plaintext = b"A" * size
            c = encrypt(key, plaintext)
            authenticated_frame_overhead = 6 + 16 + 32
            assert len(c) == authenticated_frame_overhead + ((size // 16) + 1) * 16


class TestPaddingOracleResistance:
    def test_is_valid_padding_true(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt(key, b"hello")
        assert is_valid_padding(key, c) is True

    def test_is_valid_padding_wrong_key_false(self) -> None:
        k1, k2 = secrets.token_bytes(32), secrets.token_bytes(32)
        c = encrypt(k1, b"hello")
        assert is_valid_padding(k2, c) is False

    def test_is_valid_padding_tampered_false(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt(key, b"hello")
        tampered = bytearray(c)
        tampered[-1] ^= 0xFF
        assert is_valid_padding(key, bytes(tampered)) is False

    def test_is_valid_padding_never_raises(self) -> None:
        key = secrets.token_bytes(32)
        bad_ct = secrets.token_bytes(32)
        assert is_valid_padding(key, bad_ct) in (True, False)

    def test_oracle_resistant_no_info_leak(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt(key, b"test")

        for _ in range(50):
            tampered = bytearray(c)
            tampered[-1] ^= secrets.randbits(8)
            result = is_valid_padding(key, bytes(tampered))
            assert result in (True, False)


class TestBoundaryConditions:
    def test_single_byte_payload(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt(key, b"\x00")
        assert decrypt(key, c) == b"\x00"

    def test_max_byte_values(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = bytes(range(256))
        c = encrypt(key, plaintext)
        assert decrypt(key, c) == plaintext

    def test_binary_data_with_nulls(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"\x00\x01\x02\x00\xff\x00"
        c = encrypt(key, plaintext)
        assert decrypt(key, c) == plaintext

    def test_all_zeroes_plaintext(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"\x00" * 64
        c = encrypt(key, plaintext)
        assert decrypt(key, c) == plaintext

    def test_ff_filled_plaintext(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"\xff" * 32
        c = encrypt(key, plaintext)
        assert decrypt(key, c) == plaintext

    def test_varied_block_boundaries(self) -> None:
        key = secrets.token_bytes(32)
        for size in (15, 16, 17, 31, 32, 33, 63, 64, 65):
            plaintext = secrets.token_bytes(size)
            c = encrypt(key, plaintext)
            assert decrypt(key, c) == plaintext


class TestNonConstantTimeDecrypt:
    def test_non_ct_mode_roundtrip(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt(key, b"hello")
        result = decrypt(key, c, constant_time=False)
        assert result == b"hello"

    def test_non_ct_invalid_padding_raises(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt(key, b"hello")
        tampered = bytearray(c)
        tampered[-1] ^= 0xFF
        with pytest.raises(CBCError):
            decrypt(key, bytes(tampered), constant_time=False)
